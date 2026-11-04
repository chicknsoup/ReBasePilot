# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\core\\ui_navigation.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

"""Recognize visible navigation labels after the October 2026 HUD rework.\n\nThe zones below describe where to *search*, never where to click. Click targets\ncome from OCR boxes. English labels are a fallback for the existing templates;\nunknown, low-confidence, and ambiguous labels remain unrecognized.\n"""
import math
import re
from app.utils.common import get_template_path
import cv2
import numpy as np
def _normalized(text):
    aliases = {'battie': 'battle', 'matcuh': 'match', 'confinm': 'confirm', 'yougot': 'you got'}
    return ' '.join((aliases.get(token, token) for token in re.findall('[a-z0-9]+', str(text).lower())))
def _zone(frame, left=0, top=0, right=1, bottom=1):
    height, width = frame.shape[:2]
    x, y = (round(width * left), round(height * top))
    return (x, y, round(width * right) - x, round(height * bottom) - y)
def _segments(words, region, min_confidence):
    """Group nearby words on a line without joining separate menu buttons."""
    x, y, width, height = region
    valid = []
    for word in words or ():
        cx = word.left + word.width / 2
        cy = word.top + word.height / 2
        if _normalized(word.text) and x <= cx < x + width and (y <= cy < y + height):
                            valid.append(word)
    lines = []
    for word in sorted(valid, key=lambda w: (w.top + w.height / 2, w.left)):
        cy = word.top + word.height / 2
        line = next((line for line in lines if abs(cy - (line[0].top + line[0].height / 2)) <= max(word.height, line[0].height) * 0.6), None)
        if line is None:
            lines.append([word])
        else:
            line.append(word)
    result = []
    for line in lines:
        segment = []
        for word in sorted(line, key=lambda w: w.left):
            if segment and word.left - (segment[(-1)].left + segment[(-1)].width) > min(word.height, segment[(-1)].height) * 1.25:
                    result.append(segment)
                    segment = []
            segment.append(word)
        if segment:
            result.append(segment)
    return result
def _intersect_region(r1, r2):
    if r1 is None:
        return r2
    if r2 is None:
        return r1
    x1 = max(r1[0], r2[0])
    y1 = max(r1[1], r2[1])
    x2 = min(r1[0] + r1[2], r2[0] + r2[2])
    y2 = min(r1[1] + r1[3], r2[1] + r2[3])
    if x2 <= x1 or y2 <= y1:
        return r2
    return (x1, y1, x2 - x1, y2 - y1)
def find_ui_label(frame, vision, labels, region=None, min_confidence=70):
    """Center of one complete, confidently read label, or ``(None, None)``.\n\nUnlike username OCR, matching is exact: \"Ranked Battle\" cannot match\n\"Battle\", and \"Use gems\" cannot match \"Use\". Confidence is checked here\nbecause the shared OCR service retains all words regardless of its argument.\n"""
    if frame is None or getattr(frame, 'size', 0) == 0:
        return (None, None)
    else:
        if isinstance(labels, str):
            labels = (labels,)
        wanted = {_normalized(label) for label in labels}
        region = region or _zone(frame)
        rw, rh = region[2], region[3]
        upscale = 1 if rw * rh > 450000 else (2 if rw * rh > 120000 else 3)
        for white, floor in [(True, 235), (True, 205)]:
            words = vision.find_words_ocr(frame, region=region, min_confidence=min_confidence, preprocess=True, white_text=white, brightness_floor=floor, roi_upscale=upscale, tesseract_config='--psm 11')
            hits = []
            for segment in _segments(words, region, min_confidence):
                if _normalized(' '.join((word.text for word in segment))) not in wanted:
                    continue
                else:
                    if any((not math.isfinite(float(getattr(word, 'confidence', (-1)))) or float(getattr(word, 'confidence', (-1))) < min_confidence for word in segment)):
                        continue
                    else:
                        left = min((word.left for word in segment))
                        top = min((word.top for word in segment))
                        right = max((word.left + word.width for word in segment))
                        bottom = max((word.top + word.height for word in segment))
                        hits.append(((left + right) // 2, (top + bottom) // 2))
            if len(hits) == 1:
                return hits[0]
            else:
                if len(hits) > 1:
                    return (None, None)
        return (None, None)
def find_battle_button(frame, vision):
    if frame is None or getattr(frame, 'size', 0) == 0:
        return (None, None)
    else:
        region = _zone(frame, 0, 0.8, 0.25, 1)
        if get_template_path('attack.png').is_file():
            point = vision.find_template(frame, 'attack.png', region=region)
            if point[0] is not None:
                return point
        return find_ui_label(frame, vision, 'Battle', region)
def find_build_button(frame, vision):
    if frame is None or getattr(frame, 'size', 0) == 0:
        return (None, None)
    else:
        region = _zone(frame, 0.5, 0.5, 1, 1)
        if get_template_path('build.png').is_file():
            point = vision.find_template(frame, 'build.png', region=region)
            if point[0] is not None:
                return point
        return find_ui_label(frame, vision, 'Build', region)
def find_buildings_tab(frame, vision):
    """Find the Buildings & Heroes footer icon, including when another tab is active."""
    if frame is None or getattr(frame, 'size', 0) == 0:
        return (None, None)
    else:
        if not get_template_path('buildings_tab.png').is_file():
            return (None, None)
        else:
            return vision.find_template(frame, 'buildings_tab.png', region=_zone(frame, 0.5, 0.88, 1, 1))
def find_research_tab(frame, vision):
    """Army Upgrades footer icon; the panel title is verified after navigation."""
    if frame is None or getattr(frame, 'size', 0) == 0:
        return (None, None)
    else:
        if not get_template_path('research_tab.png').is_file():
            return (None, None)
        else:
            return vision.find_template(frame, 'research_tab.png', region=_zone(frame, 0.5, 0.88, 1, 1))
def find_profile_button(frame, vision):
    """Recognize the profile card from its border, level badge, and player name."""
    if frame is None or getattr(frame, 'size', 0) == 0:
        return (None, None)
    label = find_ui_label(frame, vision, 'Profile', _zone(frame, 0, 0, 0.5, 0.5))
    if label[0] is not None:
        return label
    if not is_village_hud(frame, vision):
        return (None, None)
    if get_template_path('profile.png').is_file():
        point = vision.find_template(frame, 'profile.png', region=_zone(frame, 0, 0, 0.3, 0.25))
        if point[0] is not None:
            return point
    height, width = frame.shape[:2]
    roi = frame[:round(height * 0.25), :round(width * 0.3)]
    edges = cv2.Canny(roi, 80, 180)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (0.75 <= w / max(1, h) <= 1.3 and height * 0.05 <= h <= height * 0.2):
            continue
        pad = max(2, round(h * 0.3))
        badge = frame[max(0, y - pad):min(height, y + pad), max(0, x - pad):min(width, x + round(w * 0.5))]
        hsv = cv2.cvtColor(badge, cv2.COLOR_BGR2HSV)
        cyan = cv2.inRange(hsv, np.array([80, 110, 130]), np.array([110, 255, 255]))
        if np.count_nonzero(cyan) / max(1, cyan.size) < 0.04:
            continue
        name_x = x + w
        name_region = (name_x, y + round(h * 0.15), min(width - name_x, w * 3), max(1, round(h * 0.5)))
        names = vision.find_words_ocr(frame, region=name_region, white_text=True, brightness_floor=235, roi_upscale=4, tesseract_config='--psm 7')
        if not any((len(re.sub('[^a-zA-Z]', '', word.text)) >= 3 and float(getattr(word, 'confidence', (-1))) >= 40 for word in names or ())):
            continue
        point = (x + w // 2, y + h // 2)
        if any((abs(point[0] - other[0]) <= h * 0.1 and abs(point[1] - other[1]) <= h * 0.1 for other in candidates)):
            continue
        candidates.append(point)
    if len(candidates) == 1:
        return candidates[0]
    return (None, None)
def find_settings_button(frame, vision):
    return find_ui_label(frame, vision, 'Settings')
def is_village_hud(frame, vision):
    """Both primary buttons are visible; does not infer which village is open."""
    return find_battle_button(frame, vision)[0] is not None and find_build_button(frame, vision)[0] is not None
def find_navigation_fallback(frame, vision, template, region=None):
    if frame is None or getattr(frame, 'size', 0) == 0:
        return (None, None)
    else:
        if template == 'attack.png':
            if find_build_button(frame, vision)[0] is None:
                return (None, None)
            else:
                return find_battle_button(frame, vision)
        else:
            if template in ('farmbattle.png', 'rankedbattle.png'):
                if find_ui_label(frame, vision, 'Multiplayer', _zone(frame, 0, 0, 1, 0.16))[0] is None:
                    return (None, None)
                titles = _zone(frame, 0, 0.15, 1, 0.8)
                rw, rh = titles[2], titles[3]
                upscale = 1 if rw * rh > 450000 else (2 if rw * rh > 120000 else 3)
                farm = (None, None)
                ranked = (None, None)
                for white, floor in [(True, 235), (True, 205)]:
                    words = vision.find_words_ocr(frame, region=titles, min_confidence=70, preprocess=True, white_text=white, brightness_floor=floor, roi_upscale=upscale, tesseract_config='--psm 11')
                    farm_hits, ranked_hits = [], []
                    for segment in _segments(words, titles, 70):
                        norm = _normalized(' '.join((word.text for word in segment)))
                        if norm not in ('battle', 'ranked battle'):
                            continue
                        if any((not math.isfinite(float(getattr(word, 'confidence', (-1)))) or float(getattr(word, 'confidence', (-1))) < 70 for word in segment)):
                            continue
                        left_s = min((word.left for word in segment))
                        top_s = min((word.top for word in segment))
                        right_s = max((word.left + word.width for word in segment))
                        bottom_s = max((word.top + word.height for word in segment))
                        pt = ((left_s + right_s) // 2, (top_s + bottom_s) // 2)
                        if norm == 'battle':
                            farm_hits.append(pt)
                        else:
                            ranked_hits.append(pt)
                    if len(farm_hits) == 1 and farm[0] is None:
                        farm = farm_hits[0]
                    if len(ranked_hits) == 1 and ranked[0] is None:
                        ranked = ranked_hits[0]
                    if farm[0] is not None and ranked[0] is not None:
                        break
                if farm[0] is not None and ranked[0] is not None and farm[0] < ranked[0]:
                    distance = ranked[0] - farm[0]
                    divider = (farm[0] + ranked[0]) // 2
                    left = divider if template == 'rankedbattle.png' else max(0, farm[0] - distance // 2)
                    right = min(frame.shape[1], ranked[0] + distance // 2) if template == 'rankedbattle.png' else divider
                    title = ranked if template == 'rankedbattle.png' else farm
                    pt = find_ui_label(frame, vision, 'Find a Match', (left, title[1] + round(frame.shape[0] * 0.04), right - left, round(frame.shape[0] * 0.05)), min_confidence=40)
                    if pt[0] is not None:
                        return pt
                fallback_zone = _zone(frame, 0.48, 0.25, 0.98, 0.85) if template == 'rankedbattle.png' else _zone(frame, 0.02, 0.25, 0.52, 0.85)
                return find_ui_label(frame, vision, 'Find a Match', fallback_zone, min_confidence=40)
            else:
                labels = {'train.png': 'Edit Army', 'attack2.png': 'Attack', 'rankedattackconfirm.png': 'Attack', 'findnow.png': 'Find Now', 'savedrecipes.png': 'Saved Recipes', 'use.png': 'Use', 'returnhome.png': 'Return Home', 'returnhome2.png': 'Return Home', 'breturnhome.png': 'Return Home', 'breturnhome1920.png': 'Return Home', 'surrender.png': 'Surrender', 'endbattle.png': 'End Battle'}
                label = labels.get(template)
                if label is None:
                    return (None, None)
                else:
                    zones = {
                        'surrender.png': (0, 0.70, 0.22, 0.92),
                        'endbattle.png': (0, 0.70, 0.22, 0.92),
                        'returnhome.png': (0.35, 0.72, 0.65, 0.98),
                        'returnhome2.png': (0.35, 0.72, 0.65, 0.98),
                        'breturnhome.png': (0.35, 0.72, 0.65, 0.98),
                        'breturnhome1920.png': (0.35, 0.72, 0.65, 0.98),
                        'attack2.png': (0.35, 0.65, 1.0, 0.98),
                        'rankedattackconfirm.png': (0.35, 0.55, 0.98, 0.98),
                        'findnow.png': (0.35, 0.55, 0.98, 0.98),
                        'train.png': (0, 0.65, 0.22, 0.88),
                        'savedrecipes.png': (0.50, 0.08, 1.0, 0.92),
                    }
                    z = zones.get(template)
                    target_region = _intersect_region(region, _zone(frame, *z)) if z is not None else region
                    if template in ('surrender.png', 'endbattle.png') and target_region is not None and frame.ndim == 3:
                        rx, ry, rw, rh = target_region
                        crop = frame[ry:ry + rh, rx:rx + rw]
                        if crop.size > 0:
                            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
                            red = cv2.bitwise_or(cv2.inRange(hsv, (0, 80, 80), (12, 255, 255)), cv2.inRange(hsv, (168, 80, 80), (179, 255, 255)))
                            if float(cv2.countNonZero(red)) / float(crop.shape[0] * crop.shape[1]) < 0.025:
                                return (None, None)
                    return find_ui_label(frame, vision, label, region=target_region)