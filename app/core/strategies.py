import math
import time
import random
import cv2
import numpy as np
from typing import Any, List, Optional, Tuple
from app.services.input import InputService
from app.services.vision import VisionService
from app.config import ASPECT_16_9, Config
from app.utils.logger import setup_logger
from app.utils.profile_settings_store import EARTHQUAKE_METHOD_CURVE, EARTHQUAKE_METHOD_RANDOM
logger = setup_logger('Strategies')
_EARTHQUAKE_REGION_ARC_SAMPLES = 49
_EARTHQUAKE_RANDOM_LINE_FROM_BOTTOM = (4, 10)
_EDRAG_COUNT = 12
_EDRAG_DELAY = 0.2
_EDRAG_CORNER_MARGIN = 100
_DIAMOND_EDGES = (('left', 'top'), ('top', 'right'))
_EDGE_CORNERS = {
    'NW': ('left', 'top'),
    'NE': ('top', 'right'),
    'SE': ('right', 'bottom'),
    'SW': ('bottom', 'left'),
}
_ADJACENT_FRONT_PAIRS = (
    ('NW', 'NE', ('left', 'top', 'right')),
    ('NE', 'SE', ('top', 'right', 'bottom')),
    ('SE', 'SW', ('right', 'bottom', 'left')),
    ('SW', 'NW', ('bottom', 'left', 'top')),
)
_DEPLOY_BAR_SAFE_Y_FRAC = 0.80
_CC_HEADER_BADGE_DEV_FLOOR = 15.0
_HIGH_DPI_DIAMOND_16_9 = {
    'left': (315, 645),
    'top': (1280, -45),
    'right': (2245, 645),
    'bottom': (1280, 1345),
}

class AttackStrategy:
    '''Base class for attack strategies.'''
    
    def __init__(self, input_service, vision_service, config, stop_event = None, earthquake_method = EARTHQUAKE_METHOD_CURVE):
        self.input = input_service
        self.vision = vision_service
        self.config = config
        self.stop_event = stop_event
        self.earthquake_method = earthquake_method
        self.event_handler = None
        self._active_attack_edges = None
        self._active_front_corners = None
        self.CORNER_ORDER = [
            'left',
            'top',
            'right',
            'bottom']

    
    def execute(self, frame, stop_event = None):
        raise NotImplementedError

    
    def _expand_loc(self, x, y):
        return (x + random.randint(-10, 10), y + random.randint(-10, 10))

    
    def _sync_frame_size(self, frame):
        self.config.set_target_size_from_frame(frame)

    def _reward_checkpoint(self, frame=None, selected=None):
        """Clear event overlays before input; reselect a displaced troop/spell.

        Return a current frame and whether a choice interrupted deployment.
        A missing capture or a stuck overlay must stop further battle clicks.
        """
        if self.stop_event and self.stop_event.is_set():
            raise InterruptedError('Bot stopped by user')
        if self.event_handler is None:
            return frame, False
        # Callers may retain a frame from before a previous deployment/choice.
        frame = self.input.window_service.screenshot()
        if frame is None:
            raise RuntimeError('Game capture unavailable during deployment')
        handled = self.event_handler.handle(frame)
        if handled:
            frame = self.input.window_service.screenshot()
            if frame is None:
                raise RuntimeError('Game capture unavailable after event choice')
            self._sync_frame_size(frame)
            if selected:
                roi = self.vision.bottom_half_region(frame)
                x, y = self._find_non_cc_template(frame, selected, threshold=0.75, region=roi)
                if x is None:
                    return None, True  # The previously selected troop is exhausted.
                self.input.click(x, y, pause=0.2, rand=False)
        return frame, handled

    def _is_clan_castle_card(self, frame, left, top, tw, th):
        """True when the card containing ``(left, top, tw, th)`` has a Clan Castle badge in its top-left header.

        Clash of Clans renders the player's own custom Clan Badge (not a fixed icon) in the
        top-left header of Clan Castle cards, while regular troop/spell headers are a flat
        blue/purple gradient across their left half. Comparing the left badge zone against
        the card's own blank middle-left header strip detects any clan emblem regardless of
        colors, resolution, or background scenery.
        """
        if frame is None or not getattr(frame, 'size', 0):
            return False
        fh, fw = frame.shape[:2]
        cx = int(left + tw // 2)
        cy = int(top + th // 2)
        ui_s = max(0.85, min(1.45, float(getattr(VisionService, '_active_ui_scale', 1.0))))

        xm0 = max(0, cx - max(3, int(round(fw * 0.0085 * ui_s))))
        xm1 = max(xm0 + 2, cx - max(1, int(round(fw * 0.0015 * ui_s))))
        ys0 = max(0, cy - int(round(fh * 0.078 * ui_s)))
        ys1 = min(fh, max(ys0 + 4, int(top)))
        if xm1 > fw or ys1 <= ys0:
            return False
        mid_strip = frame[ys0:ys1, xm0:xm1].astype(np.float32)
        b_mean = mid_strip[:, :, 0].mean(axis=1)
        g_mean = mid_strip[:, :, 1].mean(axis=1)
        min_gr = np.minimum(g_mean, mid_strip[:, :, 2].mean(axis=1))
        row_flat = mid_strip.std(axis=1).mean(axis=1)
        hdr_mask = (b_mean >= 135.0) & ((b_mean - g_mean) >= 25.0) & ((b_mean - min_gr) >= 40.0) & (row_flat <= 18.0)
        min_run = max(3, int(round(fh * 0.005 * ui_s)))
        runs = []
        run_start = None
        for r_idx, ok in enumerate(hdr_mask):
            if ok and run_start is None:
                run_start = r_idx
            elif not ok and run_start is not None:
                if r_idx - run_start >= min_run:
                    runs.append((run_start, r_idx))
                run_start = None
        if run_start is not None and len(hdr_mask) - run_start >= min_run:
            runs.append((run_start, len(hdr_mask)))
        if not runs:
            return False
        r0, r1 = runs[-1]
        hy0 = ys0 + r0 + max(1, int(round(fh * 0.002 * ui_s)))
        hy1 = min(ys0 + r1, hy0 + max(4, int(round(fh * 0.014 * ui_s))))

        bx0 = max(0, cx - max(8, int(round(fw * 0.022 * ui_s))))
        bx1 = max(bx0 + 2, cx - max(4, int(round(fw * 0.011 * ui_s))))
        if bx1 > fw or hy1 <= hy0:
            return False
        ref_bgr = np.median(frame[hy0:hy1, xm0:xm1].astype(np.float32), axis=(0, 1))
        badge_patch = frame[hy0:hy1, bx0:bx1].astype(np.float32)
        dev = float(np.mean(np.abs(badge_patch - ref_bgr)))
        return dev >= _CC_HEADER_BADGE_DEV_FLOOR

    def _find_non_cc_template(self, frame, template_name, *, threshold=0.75, region=None):
        """Find the leftmost match of ``template_name`` that is NOT a Clan Castle troop/spell card."""
        if type(self.vision) is not VisionService:
            return self.vision.find_template(frame, template_name, threshold=threshold, region=region)
        matches = self.vision._find_template_matches(frame, template_name, threshold, region, max_matches=4)
        if not matches:
            return (None, None)
        for left, top, tw, th, _score in sorted(matches, key=lambda m: m[0]):
            if self._is_clan_castle_card(frame, left, top, tw, th):
                logger.debug('Skipping Clan Castle card for %s at %d,%d', template_name, left, top)
                continue
            return (int(left) + int(tw) // 2, int(top) + int(th) // 2)
        return (None, None)

    
    @staticmethod
    def _ui_diamond_blend():
        '''0.0 at standard 320-DPI UI scale (1.0x); 1.0 at 240-DPI UI scale (>=1.22x) where max zoom-out insets the 44x44 grass.'''
        ui_s = float(getattr(VisionService, '_active_ui_scale', 1.0))
        if ui_s <= 1.04:
            return 0.0
        return min(1.0, (ui_s - 1.04) / (1.22 - 1.04))

    def _point(self, key):
        pt = self.config.get_point(key)
        blend = self._ui_diamond_blend()
        if blend <= 0.0 or key not in ('left', 'top', 'right', 'bottom'):
            return pt
        if self.config.aspect_key == ASPECT_16_9 and key in _HIGH_DPI_DIAMOND_16_9:
            target = self.config.scale_point(_HIGH_DPI_DIAMOND_16_9[key])
            return [
                int(round(pt[0] + (target[0] - pt[0]) * blend)),
                int(round(pt[1] + (target[1] - pt[1]) * blend)),
            ]
        cx, cy = self.config.scale_point([1280, 740])
        shrink = 1.0 - 0.08 * blend
        return [
            int(round(cx + (pt[0] - cx) * shrink)),
            int(round(cy + (pt[1] - cy) * shrink)),
        ]

    
    def _scaled_deployment_data(self):
        return { key: self._point(key) for key in self.CORNER_ORDER }
        key = None

    
    def deploy_heroes(self, frame):
        frame, _ = self._reward_checkpoint(frame)
        self._sync_frame_size(frame)
        heroes = [
            'queen',
            'warden',
            'RC',
            'king',
            'prince',
            'dragonduke']
        random.shuffle(heroes)
        deployed_heroes = []
        roi = self.vision.bottom_half_region(frame)
        can_verify = type(self.vision) is VisionService and getattr(self.input, 'window_service', None) is not None
        for hero in heroes:
            fresh, interrupted = self._reward_checkpoint()
            if fresh is not None:
                frame = fresh
                roi = self.vision.bottom_half_region(frame)
            if interrupted:
                deployed_heroes = self._relocate_abilities(deployed_heroes, frame)
            (bx, by) = self.vision.find_template(frame, f'''{hero}.png''', threshold = 0.7, region = roi)
            if not bx:
                continue
            deploy_point = self._get_hero_deploy_point(frame)
            self.input.click(bx, by, pause = random.uniform(0.15, 0.3), rand = False)
            self.input.click(deploy_point[0], deploy_point[1], pause = random.uniform(0.15, 0.3), rand = False)
            # Remember the live, deployed appearance only for relocation after
            # a reward shifts the bar. Normal ability clicks retain main's slots.
            live = self.input.window_service.screenshot() if (self.event_handler or can_verify) else frame
            if can_verify and isinstance(live, np.ndarray) and live.size:
                for _retry in range(2):
                    if self.stop_event and self.stop_event.is_set():
                        break
                    live_roi = self.vision.bottom_half_region(live)
                    rx, ry, rconf = self.vision.find_template_with_confidence(live, f'{hero}.png', threshold = 0.80, region = live_roi)
                    if rx is None:
                        break
                    logger.info('Hero %s still undeployed in bar (conf=%.2f) — retrying deploy on outer grass', hero, rconf)
                    bx, by = rx, ry
                    alt_pt = self._get_hero_deploy_point(live, fallback_top=True)
                    self.input.click(bx, by, pause = 0.18, rand = False)
                    self.input.click(alt_pt[0], alt_pt[1], pause = 0.22, rand = False)
                    nxt = self.input.window_service.screenshot()
                    if isinstance(nxt, np.ndarray) and nxt.size:
                        live = nxt
                frame = live
                roi = self.vision.bottom_half_region(frame)
            radius = max(8, round(frame.shape[0]*.025))
            tile = None
            if live is not None and isinstance(live, np.ndarray) and bx >= radius and by >= radius:
                tile = live[by-radius:by+radius, bx-radius:bx+radius].copy()
            deployed_heroes.append((bx, by, tile))
        while deployed_heroes:
            fresh, interrupted = self._reward_checkpoint()
            if interrupted:
                deployed_heroes = self._relocate_abilities(deployed_heroes, fresh)
            if not deployed_heroes:
                break
            hx, hy, _ = deployed_heroes.pop(0)
            self.input.click(hx, hy, pause = 0.2)
            logger.info('Hero ability clicked at %d,%d', hx, hy)
            if self.stop_event:
                self.stop_event.wait(random.uniform(0.1, 0.2))
                continue
            time.sleep(random.uniform(0.1, 0.2))

    def _relocate_abilities(self, heroes, frame):
        """Match cached live portraits only when an event actually moved slots."""
        if frame is None:
            return []
        top = frame.shape[0]//2
        roi = cv2.cvtColor(frame[top:], cv2.COLOR_BGR2GRAY)
        relocated = []
        for x, y, tile in heroes:
            if tile is None or not tile.size:
                continue
            gray = cv2.cvtColor(tile, cv2.COLOR_BGR2GRAY)
            _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(roi, gray, cv2.TM_CCOEFF_NORMED))
            if score >= .65:
                relocated.append((loc[0]+tile.shape[1]//2, top+loc[1]+tile.shape[0]//2, tile))
            else:
                logger.info('Hero no longer recognizable after reward; skipping stale slot')
        return relocated

    
    def _hero_corner_xy(self, corner):
        '''Corner used when building hero deploy lines; on ``16_9``, ``top`` is nudge-scaled at 1.0x UI scale.'''
        (px, py) = self._point(corner)
        if self.config.aspect_key == ASPECT_16_9 and corner == 'top':
            nudge = int(round(self.config.scale_scalar(70) * (1.0 - self._ui_diamond_blend())))
            py -= nudge
        return (int(px), int(py))

    
    @staticmethod
    def _safe_deploy_max_y(fh):
        ui_s = max(1.0, min(1.45, float(getattr(VisionService, '_active_ui_scale', 1.0))))
        safe_frac = 1.0 - (1.0 - _DEPLOY_BAR_SAFE_Y_FRAC) * ui_s
        return max(0, min(fh - 1, int(round(fh * safe_frac))))

    @staticmethod
    def _quantize_deploy_to_frame(px, py, fw, fh):
        '''Clamp to ``[0, fw-1]`` x ``[0, safe_max_y]`` above the bottom deploy bar.'''
        if fw <= 0 or fh <= 0:
            return (int(round(px)), int(round(py)))
        max_y = AttackStrategy._safe_deploy_max_y(fh)
        cx = max(0, min(float(fw - 1), float(px)))
        cy = max(0, min(float(max_y), float(py)))
        return (int(round(cx)), int(round(cy)))

    def _edge_endpoints(self, c1, c2):
        """Return ``((x1, y1), (x2, y2))`` for a diamond edge.

        Upper edges (NW/NE) use the proven calibrated corners. Lower edges (SE/SW)
        push outward to the true outer grass rim of the 44x44 map so clicks do not
        clip into red building zones on 240-DPI captures.
        """
        p1 = list(self._point(c1))
        p2 = list(self._point(c2))
        if 'bottom' in (c1, c2):
            dx_out = int(round(self.config.scale_scalar(85)))
            dy_side = int(round(self.config.scale_scalar(110)))
            dy_bot = int(round(self.config.scale_scalar(115)))
            for corner, pt in ((c1, p1), (c2, p2)):
                if corner == 'left':
                    pt[0] -= dx_out
                    pt[1] += dy_side
                elif corner == 'right':
                    pt[0] += dx_out
                    pt[1] += dy_side
                elif corner == 'bottom':
                    pt[1] += dy_bot
        return ((p1[0], p1[1]), (p2[0], p2[1]))

    def _safe_edge_point(self, c1, c2, frame, *, margin_px=None, t_range=(0.08, 0.92)):
        """Sample a point strictly along edge ``c1 -> c2`` that stays inside safe outer grass.

        Instead of projecting out-of-bounds lower-edge points vertically into the base,
        this intersects the segment with the safe viewport and HUD exclusion zones first.
        """
        (x1, y1), (x2, y2) = self._edge_endpoints(c1, c2)
        edge_len = math.hypot(x2 - x1, y2 - y1)
        if margin_px is not None and edge_len > 2.0 * margin_px:
            t_lo = max(t_range[0], margin_px / edge_len)
            t_hi = min(t_range[1], 1.0 - margin_px / edge_len)
        else:
            t_lo, t_hi = t_range
        min_y = float(self.config.scale_scalar(35))
        fh, fw = (frame.shape[:2] if frame is not None and getattr(frame, 'size', 0) else (0, 0))
        max_y = float(self._safe_deploy_max_y(fh)) if fh > 0 else float('inf')

        def _is_safe(px, py):
            if py < min_y or py > max_y:
                return False
            if fw > 0 and fh > 0:
                if px < 4.0 or px > fw - 5.0:
                    return False
                # Avoid top-left Available Loot panel
                if px < fw * 0.24 and py < fh * 0.28:
                    return False
                # Avoid bottom-left Surrender button and bottom-right Overall Damage HUD
                if py > fh * 0.66 and (px < fw * 0.17 or px > fw * 0.83):
                    return False
            return True

        # Sample candidate t values along the segment and pick among safe ones
        samples = 25
        valid_ts = []
        if t_hi > t_lo:
            for i in range(samples):
                t = t_lo + (t_hi - t_lo) * (i / float(samples - 1))
                px = x1 + (x2 - x1) * t
                py = y1 + (y2 - y1) * t
                if _is_safe(px, py):
                    valid_ts.append(t)
        if valid_ts:
            t_a, t_b = valid_ts[0], valid_ts[-1]
            t = random.uniform(t_a, t_b)
        else:
            t = 0.5
        xf = x1 + (x2 - x1) * t
        yf = y1 + (y2 - y1) * t
        if yf < min_y and y1 != y2:
            t_clamp = max(0.0, min(1.0, (min_y - y1) / float(y2 - y1)))
            xf = x1 + (x2 - x1) * t_clamp
            yf = y1 + (y2 - y1) * t_clamp
        if fh > 0 and fw > 0:
            return self._quantize_deploy_to_frame(xf, yf, fw, fh)
        return (int(round(xf)), int(round(yf)))

    def _choose_loot_attack_edges(self, frame):
        """Inspect Gold/Elixir storage colors inside the 40x40 base diamond and pick the closest 2-edge front.

        Returns ``(edges_tuple, front_corners, label, reason)`` where ``edges_tuple`` is a pair of
        adjacent ``(corner_a, corner_b)`` tuples. When loot is evenly distributed (or unreadable),
        rotates randomly across all 4 adjacent fronts so raids don't always hit the same angle.
        """
        if frame is None or not getattr(frame, 'size', 0):
            e1, e2, corners = random.choice(_ADJACENT_FRONT_PAIRS)
            return ((_EDGE_CORNERS[e1], _EDGE_CORNERS[e2]), corners, f'{e1}+{e2}', 'random')

        fh, fw = frame.shape[:2]
        data = self._scaled_deployment_data()
        poly_pts = np.array([data['left'], data['top'], data['right'], data['bottom']], dtype=np.float32)
        center = poly_pts.mean(axis=0)
        # Shrink 14% inward so outer scenery/waterfalls/borders are excluded
        inner_poly = (center + (poly_pts - center) * 0.86).astype(np.int32)
        mask = np.zeros((fh, fw), dtype=np.uint8)
        cv2.fillPoly(mask, [inner_poly], 255)
        max_y = self._safe_deploy_max_y(fh)
        mask[max_y:, :] = 0
        mask[:int(fh * 0.28), :int(fw * 0.25)] = 0

        # Downsample 2x for < 3ms HSV classification
        small_fr = frame[::2, ::2]
        small_mask = mask[::2, ::2]
        hsv = cv2.cvtColor(small_fr, cv2.COLOR_BGR2HSV)
        elix = cv2.inRange(hsv, (138, 130, 170), (168, 255, 255)) & small_mask
        gold = cv2.inRange(hsv, (18, 155, 185), (30, 255, 255)) & small_mask
        comb = elix | gold
        ys, xs = np.where(comb > 0)

        if len(xs) < 40:
            e1, e2, corners = random.choice(_ADJACENT_FRONT_PAIRS)
            return ((_EDGE_CORNERS[e1], _EDGE_CORNERS[e2]), corners, f'{e1}+{e2}', 'balanced-rotate')

        # Project storage pixels into normalized diamond-axis coordinates:
        # dx > 0 = East (right), dy > 0 = South (bottom)
        px = xs.astype(np.float32) * 2.0
        py = ys.astype(np.float32) * 2.0
        half_w = max(1.0, float(data['right'][0] - data['left'][0]) * 0.5)
        half_h = max(1.0, float(data['bottom'][1] - data['top'][1]) * 0.5)
        nx = (px - float(center[0])) / half_w
        ny = (py - float(center[1])) / half_h

        # Score each edge by loot pixels in its quadrant (weighted by closeness to outer rim)
        edge_scores = {
            'NW': float(np.sum(((nx <= 0.0) & (ny <= 0.0)) * (1.0 + np.hypot(nx, ny)))),
            'NE': float(np.sum(((nx > 0.0) & (ny <= 0.0)) * (1.0 + np.hypot(nx, ny)))),
            'SE': float(np.sum(((nx > 0.0) & (ny > 0.0)) * (1.0 + np.hypot(nx, ny)))),
            'SW': float(np.sum(((nx <= 0.0) & (ny > 0.0)) * (1.0 + np.hypot(nx, ny)))),
        }
        pair_scores = [
            (edge_scores[e1] + edge_scores[e2], e1, e2, corners)
            for e1, e2, corners in _ADJACENT_FRONT_PAIRS
        ]
        pair_scores.sort(key=lambda item: item[0], reverse=True)
        best_score, be1, be2, bcorners = pair_scores[0]
        avg_score = sum(p[0] for p in pair_scores) / float(len(pair_scores))

        if avg_score > 0.0 and best_score >= avg_score * 1.12:
            return ((_EDGE_CORNERS[be1], _EDGE_CORNERS[be2]), bcorners, f'{be1}+{be2}', 'loot-targeted')

        e1, e2, corners = random.choice(_ADJACENT_FRONT_PAIRS)
        return ((_EDGE_CORNERS[e1], _EDGE_CORNERS[e2]), corners, f'{e1}+{e2}', 'balanced-rotate')

    def _get_hero_deploy_point(self, frame = None, *, fallback_top = False):
        active_edges = _DIAMOND_EDGES if fallback_top else getattr(self, '_active_attack_edges', None)
        if active_edges:
            c1, c2 = random.choice(active_edges)
            margin = float(self.config.scale_scalar(_EDRAG_CORNER_MARGIN))
            return self._safe_edge_point(c1, c2, frame, margin_px=margin, t_range=(0.08, 0.88))
        c_name = random.choice([
            'top',
            'right',
            'left'])
        (c1x, c1y) = self._hero_corner_xy(c_name)
        if c_name in ('left', 'right'):
            (c2x, c2y) = self._hero_corner_xy('top')
        else:
            corner = random.choice([
                'left',
                'right'])
            (c2x, c2y) = self._hero_corner_xy(corner)
        t = random.uniform(0.08, 0.82)
        xf = c1x + (c2x - c1x) * t
        yf = c1y + (c2y - c1y) * t
        min_y = float(self.config.scale_scalar(35))
        if yf < min_y and c1y != c2y:
            t_clamp = max(0.0, min(1.0, (min_y - c1y) / float(c2y - c1y)))
            xf = c1x + (c2x - c1x) * t_clamp
            yf = c1y + (c2y - c1y) * t_clamp
        if frame is not None and getattr(frame, 'size', 0):  # [recovered: decompiler dropped the `not`]
            (fh, fw) = frame.shape[:2]
            return self._quantize_deploy_to_frame(xf, yf, fw, fh)
        return (int(round(xf)), int(round(yf)))

    
    @staticmethod
    def _earthquake_anchor_triplet(data, offset, front_corners=None):
        '''Three drop anchors along the active attack front, nudged inward toward base center.'''
        if not front_corners or tuple(front_corners) == ('left', 'top', 'right'):
            (lx, ly) = data['left']
            lx += int(offset * 1.3)
            (tx, ty) = data['top']
            ty += int(offset)
            (rx, ry) = data['right']
            rx -= int(offset * 1.3)
            return ((float(lx), float(ly)), (float(tx), float(ty)), (float(rx), float(ry)))
        cx = sum(float(data[k][0]) for k in ('left', 'top', 'right', 'bottom')) * 0.25
        cy = sum(float(data[k][1]) for k in ('left', 'top', 'right', 'bottom')) * 0.25
        anchors = []
        for idx, corner in enumerate(front_corners):
            px, py = float(data[corner][0]), float(data[corner][1])
            dx, dy = cx - px, cy - py
            dist = math.hypot(dx, dy)
            step = float(offset) * (1.0 if idx == 1 else 1.25)
            if dist > 1e-3:
                px += (dx / dist) * min(step, dist * 0.75)
                py += (dy / dist) * min(step, dist * 0.75)
            anchors.append((px, py))
        return (anchors[0], anchors[1], anchors[2])

    
    @staticmethod
    def _sample_arc_through_three(ltr, n):
        '''
``n`` points along a circular arc from L to R that passes through T.
If L,T,R are collinear, uses a quadratic Bezier with control point chosen so t=0.5 hits T.
'''
        (L, T, R) = ltr
        (ax, ay) = L
        (bx, by) = T
        (cx, cy) = R
        d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if n < 2:
            return [
                (int(round(L[0])), int(round(L[1])))]
        if abs(d) < 1e-06:
            p1x = 2 * bx - 0.5 * ax - 0.5 * cx
            p1y = 2 * by - 0.5 * ay - 0.5 * cy
            out = []
            for i in range(n):
                t = i / (n - 1)
                u = 1 - t
                x = u * u * ax + 2 * u * t * p1x + t * t * cx
                y = u * u * ay + 2 * u * t * p1y + t * t * cy
                out.append((int(round(x)), int(round(y))))
            return out
        a2 = ax * ax + ay * ay
        b2 = bx * bx + by * by
        c2 = cx * cx + cy * cy
        ox = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
        oy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
        r = math.hypot(ax - ox, ay - oy)
        
        def ang(p):
            return math.atan2(p[1] - oy, p[0] - ox)

        phi_r = ang(R)
        phi_t = ang(T)
        phi_l = ang(L)
        two_pi = 2 * math.pi
        ccw_span = (phi_r - phi_l) % two_pi
        t_ccw = (phi_t - phi_l) % two_pi
        if t_ccw <= ccw_span:
            sweep = ccw_span
        else:
            sweep = ccw_span - two_pi
        out = []
        for i in range(n):
            t = i / (n - 1)
            phi = phi_l + t * sweep
            x = ox + r * math.cos(phi)
            y = oy + r * math.sin(phi)
            out.append((int(round(x)), int(round(y))))
        return out

    
    @staticmethod
    def _earthquake_horizontal_y(frame_h, from_bottom_num, from_bottom_den):
        '''Row ``y`` for a line ``from_bottom_num/from_bottom_den`` of the way from bottom to top.'''
        if frame_h <= 0:
            return 0
        h = frame_h
        f = from_bottom_num / float(from_bottom_den)
        return int(round((h - 1) * (1 - f)))

    
    @staticmethod
    def _earthquake_fill_polygon(arc_pts, y_line):
        '''Closed polygon: arc polyline (left→right) then segment along ``y=y_line`` back to start.'''
        if len(arc_pts) < 2:
            return list(arc_pts)
        (x0, y0) = arc_pts[0]
        (x1, y1) = arc_pts[-1]
        return list(arc_pts) + [
            (x1, y_line),
            (x0, y_line)]

    
    @staticmethod
    def _polygon_double_area(poly):
        if len(poly) < 3:
            return 0
        a = 0
        n = len(poly)
        for i in range(n):
            (x1, y1) = poly[i]
            (x2, y2) = poly[(i + 1) % n]
            a += x1 * y2 - x2 * y1
        return a

    
    @staticmethod
    def _point_in_polygon(px, py, poly):
        '''Even-odd ray test; ``poly`` closed implicitly (last vertex → first not repeated).'''
        n = len(poly)
        if n < 3:
            return False
        inside = False
        j = n - 1
        for i in range(n):
            (ix, iy) = poly[i]
            (jx, jy) = poly[j]
            if (iy > py) != (jy > py):
                x_at = (jx - ix) * (py - iy) / (jy - iy) + ix
                if px < x_at:
                    inside = not inside
            j = i
        return inside

    
    @staticmethod
    def _random_points_in_polygon(poly, frame_w, frame_h, n_points):
        xs = [ p[0] for p in poly ]
        ys = [ p[1] for p in poly ]
        min_x = max(0, min(xs))
        max_x = min(frame_w - 1, max(xs))
        min_y = max(0, min(ys))
        max_y = min(frame_h - 1, max(ys))
        out = []
        if max_x < min_x or max_y < min_y:
            return out
        for _ in range(n_points):
            for _try in range(1000):
                rx = random.randint(min_x, max_x)
                ry = random.randint(min_y, max_y)
                if not AttackStrategy._point_in_polygon(rx, ry, poly):
                    continue
                out.append((rx, ry))
                break  # [recovered: decompiler dropped this break — every in-polygon sample was appended]
            else:
                (ax, ay) = poly[max(1, len(poly) // 4)]
                out.append((max(0, min(frame_w - 1, ax)), max(0, min(frame_h - 1, ay))))
        return out

    
    def _earthquake_curve_points_with_jitter(self, ltr):
        points = self._sample_arc_through_three(ltr, 11)
        if random.choice((True, False)):
            points.reverse()
        jitter_px = 100
        return [ (cx + random.randint(-jitter_px, jitter_px), cy + random.randint(-jitter_px, jitter_px)) for cx, cy in points ]
        cy = None
        cx = None

    
    def _random_diamond_perimeter_point(self, frame, *, fallback_top = False):
        edges = _DIAMOND_EDGES if fallback_top else (getattr(self, '_active_attack_edges', None) or _DIAMOND_EDGES)
        (c1, c2) = random.choice(edges)
        margin = float(self.config.scale_scalar(_EDRAG_CORNER_MARGIN))
        return self._safe_edge_point(c1, c2, frame, margin_px=margin, t_range=(0.04, 0.96))

    
    @staticmethod
    def _point_on_polyline_at_distance(vertices, distance):
        '''Point ``distance`` pixels along a polyline from the first vertex.'''
        if not vertices:
            return (0, 0)
        if len(vertices) == 1:
            return (float(vertices[0][0]), float(vertices[0][1]))
        remaining = max(0, float(distance))
        # [recovered: pycdc reversed every dual-store here, transposing x/y mid-walk — verified against bytecode]
        (x0, y0) = (float(vertices[0][0]), float(vertices[0][1]))
        for i in range(len(vertices) - 1):
            (x1, y1) = (float(vertices[i + 1][0]), float(vertices[i + 1][1]))
            seg_len = math.hypot(x1 - x0, y1 - y0)
            if seg_len <= 0:
                (x0, y0) = (x1, y1)
                continue
            if remaining <= seg_len:
                t = remaining / seg_len
                return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
            remaining -= seg_len
            (x0, y0) = (x1, y1)
        return (x0, y0)

    
    def _even_diamond_top_perimeter_points(self, frame, count, *, deviation_frac = 0.1, reserve_top_corner_slot = False):
        '''
Evenly spaced deploy clicks along left→top→right diamond edges (Edrag front),
with ±``deviation_frac`` jitter per slot and corner margin clamping.

When ``reserve_top_corner_slot`` is set, spacing uses ``count + 1`` slots and the
slot nearest the top vertex is left empty (virtual troop at the apex).
'''
        if count <= 0:
            return []
        vertices = [ tuple(self._point(corner)) for corner in ('left', 'top', 'right') ]
        seg_lens = [ math.hypot(vertices[i + 1][0] - vertices[i][0], vertices[i + 1][1] - vertices[i][1]) for i in range(len(vertices) - 1) ]
        total_len = sum(seg_lens)
        margin = float(self.config.scale_scalar(_EDRAG_CORNER_MARGIN))
        start_d = margin
        end_d = total_len - margin
        span = end_d - start_d
        (fh, fw) = frame.shape[:2]
        if span <= 0:
            mid = self._point_on_polyline_at_distance(vertices, total_len / 2)
            return [
                self._quantize_deploy_to_frame(mid[0], mid[1], fw, fh)]
        slot_count = count + 1 if reserve_top_corner_slot else count
        spacing = span / slot_count
        slot_distances = []
        for i in range(slot_count):
            d = start_d + (i + 0.5) * spacing
            d += random.uniform(-deviation_frac, deviation_frac) * spacing
            d = max(start_d, min(end_d, d))
            slot_distances.append(d)
        skip_idx = None
        if reserve_top_corner_slot and slot_count > 1:
            top_d = seg_lens[0]
            skip_idx = min(range(slot_count), key = (lambda i: abs(slot_distances[i] - top_d)))
        out = []
        for i, d in enumerate(slot_distances):
            if i == skip_idx:
                continue
            (xf, yf) = self._point_on_polyline_at_distance(vertices, d)
            out.append(self._quantize_deploy_to_frame(xf, yf, fw, fh))
        return out
        corner = None
        i = None

    
    def _deploy_diamond_perimeter_troop(self, frame, template_name, stop_event = None, *, count = _EDRAG_COUNT, delay = _EDRAG_DELAY):
        '''Select troop in the bottom bar and click ``count`` points on the diamond perimeter.'''
        ev = stop_event or self.stop_event
        frame, _ = self._reward_checkpoint(frame)
        roi = self.vision.bottom_half_region(frame)
        (tx, ty) = self._find_non_cc_template(frame, template_name, threshold = 0.72, region = roi)
        if tx is None and template_name != 'goldendrag.png' and getattr(self.input, 'window_service', None) is not None:
            for _ in range(3):
                if ev and ev.wait(0.3):
                    return True
                fresh = self.input.window_service.screenshot()
                if fresh is not None and getattr(fresh, 'size', 0):
                    frame = fresh
                    self._sync_frame_size(frame)
                    roi = self.vision.bottom_half_region(frame)
                    (tx, ty) = self._find_non_cc_template(frame, template_name, threshold = 0.70, region = roi)
                    if tx is not None:
                        break
        if tx is None:
            if template_name != 'goldendrag.png' and frame is not None and getattr(frame, 'size', 0):
                try:
                    from app.utils.common import get_user_app_data_dir
                    dbg = get_user_app_data_dir() / 'debug'
                    dbg.mkdir(parents=True, exist_ok=True)
                    path = dbg / f'troop_missing_{template_name.replace(".png", "")}_{int(time.time())}.jpg'
                    cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
                    logger.info('Debug frame saved to %s', path)
                except Exception:
                    pass
            return False
        self.input.click(tx, ty, pause = 0.3, rand = False)
        if ev and ev.wait(0.2):
            return True
        for index in range(count):
            if ev and ev.is_set():
                return True
            fresh, handled = self._reward_checkpoint(selected=template_name)
            if handled and fresh is None:
                break
            if fresh is not None:
                frame = fresh
            (px, py) = self._random_diamond_perimeter_point(frame)
            # random human-like pacing: base delay ±60%, occasionally a longer pause
            if random.random() < 0.12:
                beat = delay * random.uniform(2.0, 3.2)
            else:
                beat = delay * random.uniform(0.6, 1.6)
            self.input.click(px, py, pause = beat, rand = False)
        if type(self.vision) is VisionService and getattr(self.input, 'window_service', None) is not None:
            for _sweep in range(12):
                if ev and ev.is_set():
                    break
                chk = self.input.window_service.screenshot()
                if not isinstance(chk, np.ndarray) or not chk.size:
                    break
                self._sync_frame_size(chk)
                chk_roi = self.vision.bottom_half_region(chk)
                rx, ry = self._find_non_cc_template(chk, template_name, threshold = 0.76, region = chk_roi)
                if rx is None:
                    break
                if _sweep == 0:
                    self.input.click(rx, ry, pause = 0.18, rand = False)
                (px, py) = self._random_diamond_perimeter_point(chk, fallback_top = True)
                self.input.click(px, py, pause = 0.16, rand = False)
        return True

    
    def deploy_golden_drags_if_present(self, frame, stop_event = None):
        '''Optional Super Dragon deploy after main troops, before heroes.'''
        ev = stop_event or self.stop_event
        if self._deploy_diamond_perimeter_troop(frame, 'goldendrag.png', ev):
            logger.info('Deployed golden dragons')
        return bool(ev and ev.is_set())

    
    def deploy_spells(self, frame):
        frame, _ = self._reward_checkpoint(frame)
        self._sync_frame_size(frame)
        roi = self.vision.bottom_half_region(frame)
        (bx, by) = self._find_non_cc_template(frame, 'earthquake.png', threshold = 0.75, region = roi)
        if bx:  # [recovered: decompiler turned `if` into `while`, causing endless earthquake spam after spells ran out]
            self.input.click(bx, by, pause = 0.2)
            offset = int(self.config.get_scaled('earthquake', 400))
            front_corners = getattr(self, '_active_front_corners', None)
            ltr = self._earthquake_anchor_triplet(self._scaled_deployment_data(), offset, front_corners=front_corners)
            (fh, fw) = frame.shape[:2]
            (num, den) = _EARTHQUAKE_RANDOM_LINE_FROM_BOTTOM
            y_line = self._earthquake_horizontal_y(fh, num, den)
            if self.earthquake_method == EARTHQUAKE_METHOD_RANDOM:
                arc_dense = self._sample_arc_through_three(ltr, _EARTHQUAKE_REGION_ARC_SAMPLES)
                if random.choice((True, False)):
                    arc_dense = arc_dense[::-1]
                poly = self._earthquake_fill_polygon(arc_dense, y_line)
                if abs(self._polygon_double_area(poly)) < 2:
                    logger.warning('Earthquake random region degenerate; using curve placement with jitter.')
                    points = self._earthquake_curve_points_with_jitter(ltr)
                else:
                    points = self._random_points_in_polygon(poly, fw, fh, 11)
            else:
                points = self._earthquake_curve_points_with_jitter(ltr)
            for cx, cy in points:
                fresh, handled = self._reward_checkpoint(selected='earthquake.png')
                if handled and fresh is None:
                    break
                jx, jy = self._quantize_deploy_to_frame(cx, cy, fw, fh)
                self.input.click_at(jx, jy, rand = False)
                delay = random.uniform(0.1, 0.3)
                if self.stop_event:
                    if self.stop_event.wait(delay):
                        return None
                else:  # [recovered: decompiler dropped the else — both waits ran, doubling every spell delay]
                    time.sleep(delay)
        offset = int(self.config.get_scaled('earthquake', 400))
        front_corners = getattr(self, '_active_front_corners', None)
        ltr = self._earthquake_anchor_triplet(self._scaled_deployment_data(), offset, front_corners=front_corners)
        (fh, fw) = frame.shape[:2]
        for spell_tpl, drop_count in (('ragespell.png', 5), ('freezespell.png', 2), ('invisspell.png', 2)):
            if self.stop_event and self.stop_event.is_set():
                return None
            fresh = self.input.window_service.screenshot()
            if fresh is not None:
                frame = fresh
                self._sync_frame_size(frame)
                roi = self.vision.bottom_half_region(frame)
                (fh, fw) = frame.shape[:2]
            (sx, sy) = self._find_non_cc_template(frame, spell_tpl, threshold = 0.75, region = roi)
            if not sx:
                continue
            self.input.click(sx, sy, pause = 0.2, rand = False)
            arc_pts = self._sample_arc_through_three(ltr, max(2, drop_count))
            if random.choice((True, False)):
                arc_pts.reverse()
            jitter_px = int(self.config.scale_scalar(45))
            for cx, cy in arc_pts[:drop_count]:
                fresh, handled = self._reward_checkpoint(selected=spell_tpl)
                if handled and fresh is None:
                    break
                jx, jy = self._quantize_deploy_to_frame(
                    cx + random.randint(-jitter_px, jitter_px),
                    cy + random.randint(-jitter_px, jitter_px),
                    fw,
                    fh,
                )
                self.input.click_at(jx, jy, rand = False)
                delay = random.uniform(0.12, 0.25)
                if self.stop_event:
                    if self.stop_event.wait(delay):
                        return None
                else:
                    time.sleep(delay)
        return None



class TroopSpamStrategy(AttackStrategy):
    
    def __init__(self, input_service, vision_service, config, stop_event, troop_name, duration, status_callback = None, earthquake_method = EARTHQUAKE_METHOD_CURVE):
        super().__init__(input_service, vision_service, config, stop_event, earthquake_method = earthquake_method)
        self.troop_name = troop_name
        self.duration = duration
        self.status_callback = status_callback

    
    def execute(self, frame, stop_event = None):
        ev = stop_event or self.stop_event
        frame, _ = self._reward_checkpoint(frame)
        self._sync_frame_size(frame)
        logger.info(f'''Executing {self.troop_name} strategy''')
        (fh, fw) = frame.shape[:2]
        roi = self.vision.bottom_half_region(frame)
        (tx, ty) = self._find_non_cc_template(frame, f'''{self.troop_name}.png''', threshold = 0.75, region = roi)
        if tx is None:
            msg = f'''Troop {self.troop_name} not found!'''
            logger.warning(msg)
            if self.status_callback:
                self.status_callback(msg)
            return False
        self.input.click(tx, ty, pause = 0.3, rand = False)
        if ev and ev.wait(0.2):
            return True
        corners = [
            'top',
            'right',
            'bottom',
            'left']
        if self.config.aspect_key == ASPECT_16_9:
            start_idx = random.choice([
                1,
                3])
        else:
            start_idx = random.choice([
                0,
                1,
                3])
        direction = random.choice([
            1,
            -1])
        ordered_corners = []
        for i in range(5):
            idx = (start_idx + i * direction) % 4
            ordered_corners.append(corners[idx])
        start_corner = ordered_corners[0]
        (curr_x, curr_y) = self._quantize_deploy_to_frame(*self._expand_loc(*self._point(start_corner)), fw, fh)
        self.input.mouse_down(curr_x, curr_y)
        if ev and ev.wait(0.65):
            self.input.mouse_up(curr_x, curr_y)
            return True
        
        try:
            total_duration = self.duration
            segment_duration = total_duration / 4
            for i in range(len(ordered_corners) - 1):
                if ev and ev.is_set():
                    break  # [recovered: decompiler dropped this break]
                fresh, handled = self._reward_checkpoint(selected=f'{self.troop_name}.png')
                if handled:
                    if fresh is None:
                        break
                    (fh, fw) = fresh.shape[:2]
                    self.input.mouse_down(curr_x, curr_y)
                next_c = ordered_corners[i + 1]
                (target_x, target_y) = self._quantize_deploy_to_frame(*self._expand_loc(*self._point(next_c)), fw, fh)
                duration = random.uniform(segment_duration * 0.9, segment_duration * 1.1)
                self.input.human_move(curr_x, curr_y, target_x, target_y, duration = duration)
                curr_y = target_y
                curr_x = target_x
            self.input.mouse_up(curr_x, curr_y)
            if ev and ev.is_set():
                return True
            frame = self.input.window_service.screenshot()
            if not frame is None:
                self._sync_frame_size(frame)
                if self.deploy_golden_drags_if_present(frame, ev):
                    return True
                self.deploy_heroes(frame)
                if ev and ev.is_set():
                    return True
                frame = self.input.window_service.screenshot()
                if not frame is None:
                    self._sync_frame_size(frame)
                    self.deploy_spells(frame)
            return True
        finally:
            self.input.mouse_up(curr_x, curr_y)




class EdragStrategy(AttackStrategy):
    
    def __init__(self, input_service, vision_service, config, stop_event, status_callback = None, earthquake_method = EARTHQUAKE_METHOD_CURVE):
        super().__init__(input_service, vision_service, config, stop_event, earthquake_method = earthquake_method)
        self.status_callback = status_callback

    
    def execute(self, frame, stop_event = None):
        ev = stop_event or self.stop_event
        self._sync_frame_size(frame)
        edges, front_corners, label, reason = self._choose_loot_attack_edges(frame)
        self._active_attack_edges = edges
        self._active_front_corners = front_corners
        logger.info('Executing edrag strategy (%s front: %s)', reason, label)
        if not self._deploy_diamond_perimeter_troop(frame, 'edrag.png', ev):
            msg = 'Troop edrag not found!'
            logger.warning(msg)
            if self.status_callback:
                self.status_callback(msg)
            return False
        if ev and ev.is_set():
            return True
        frame = self.input.window_service.screenshot()
        if not frame is None:
            self._sync_frame_size(frame)
            if self.deploy_golden_drags_if_present(frame, ev):
                return True
            self.deploy_heroes(frame)
            if ev and ev.is_set():
                return True
            frame = self.input.window_service.screenshot()
            if not frame is None:
                self._sync_frame_size(frame)
                self.deploy_spells(frame)
        return True


