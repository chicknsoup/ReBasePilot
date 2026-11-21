import unittest
from unittest.mock import Mock
import numpy as np

from app.core.strategies import AttackStrategy, TroopSpamStrategy, _DEPLOY_BAR_SAFE_Y_FRAC
from app.services.vision import VisionService


def _synthetic_battle_frame(w=1280, h=720, *, badge_colors=None):
    """Build a frame with one own spell card (left) and one CC spell card (right)."""
    frame = np.full((h, w, 3), (45, 75, 50), dtype=np.uint8)
    card_w = int(round(w * 0.056))
    hdr_y0 = int(round(h * 0.855))
    hdr_y1 = int(round(h * 0.878))
    body_y1 = int(round(h * 0.975))

    for idx, x0 in enumerate((int(w * 0.62), int(w * 0.74))):
        # Solid purple spell header + non-header portrait body (BGR)
        frame[hdr_y0:hdr_y1, x0:x0 + card_w] = (225, 86, 118)
        frame[hdr_y1:body_y1, x0:x0 + card_w] = (110, 95, 130)
        if idx == 1 and badge_colors:
            bx0 = x0 + max(3, int(round(w * 0.005)))
            bx1 = x0 + max(9, int(round(w * 0.018)))
            by0 = hdr_y0 + max(1, int(round(h * 0.002)))
            by1 = hdr_y1 - max(1, int(round(h * 0.002)))
            mid_x = (bx0 + bx1) // 2
            frame[by0:by1, bx0:mid_x] = badge_colors[0]
            frame[by0:by1, mid_x:bx1] = badge_colors[1]
            frame[by0, bx0:bx1] = (25, 25, 25)
            frame[by1 - 1, bx0:bx1] = (25, 25, 25)

    bw = int(round(card_w * 0.56))
    bx_off = (card_w - bw) // 2
    own_box = (int(w * 0.62) + bx_off, int(h * 0.888), bw, int(h * 0.05))
    cc_box = (int(w * 0.74) + bx_off, int(h * 0.888), bw, int(h * 0.05))
    return frame, own_box, cc_box


class ClanCastleExclusionTests(unittest.TestCase):
    def test_detects_any_clan_badge_across_resolutions_and_colors(self):
        strat = AttackStrategy(Mock(), VisionService(), Mock())
        emblems = (
            ((190, 175, 70), (130, 50, 175)),   # cyan + magenta
            ((30, 195, 240), (35, 45, 210)),    # gold + crimson
            ((235, 235, 235), (20, 20, 20)),    # white + black
        )
        for w, h in ((960, 540), (1224, 689), (1280, 720), (1920, 1080), (2560, 1440)):
            for colors in emblems:
                frame, own_box, cc_box = _synthetic_battle_frame(w, h, badge_colors=colors)
                self.assertFalse(
                    strat._is_clan_castle_card(frame, *own_box),
                    f'Own card misclassified as CC at {w}x{h} with {colors}',
                )
                self.assertTrue(
                    strat._is_clan_castle_card(frame, *cc_box),
                    f'CC card missed at {w}x{h} with {colors}',
                )

    def test_find_non_cc_skips_cc_only_spell_and_picks_own_spell(self):
        frame, own_box, cc_box = _synthetic_battle_frame(1224, 689, badge_colors=((190, 175, 70), (130, 50, 175)))
        vision = VisionService()
        strat = AttackStrategy(Mock(), vision, Mock())

        # Only CC spell available -> must return (None, None) instead of deploying CC spell
        vision._find_template_matches = lambda *_a, **_k: [(*cc_box, 0.94)]
        self.assertEqual(strat._find_non_cc_template(frame, 'freezespell.png'), (None, None))

        # Both own and CC spell available -> must pick own spell
        vision._find_template_matches = lambda *_a, **_k: [(*cc_box, 0.96), (*own_box, 0.93)]
        expected_own_center = (own_box[0] + own_box[2] // 2, own_box[1] + own_box[3] // 2)
        self.assertEqual(strat._find_non_cc_template(frame, 'ragespell.png'), expected_own_center)

    def test_glowing_water_scenery_above_card_does_not_misclassify_own_troop_as_cc(self):
        w, h = 1600, 900
        frame, own_box, cc_box = _synthetic_battle_frame(w, h, badge_colors=((190, 175, 70), (130, 50, 175)))
        hdr_y0 = int(round(h * 0.855))
        own_cx = own_box[0] + own_box[2] // 2
        # Paint glowing cyan/blue river + dark riverbank directly above the own card's black border
        frame[hdr_y0 - 20:hdr_y0 - 2, own_cx - 25:own_cx + 15] = (225, 228, 18)   # glowing cyan water
        frame[hdr_y0 - 20:hdr_y0 - 2, own_cx - 50:own_cx - 25] = (95, 102, 14)    # dark stone bank
        frame[hdr_y0 - 2:hdr_y0, own_cx - 50:own_cx + 30] = (15, 10, 0)           # card top outline
        # Also paint blue river above card to ensure bottom-most header run wins over scenery
        frame[hdr_y0 - 16:hdr_y0 - 8, own_cx - 25:own_cx + 15] = (230, 150, 80)

        strat = AttackStrategy(Mock(), VisionService(), Mock())
        self.assertFalse(strat._is_clan_castle_card(frame, *own_box))
        self.assertTrue(strat._is_clan_castle_card(frame, *cc_box))

    def test_deploy_coordinates_never_enter_bottom_deploy_bar(self):
        fw, fh = 1224, 689
        safe_max_y = int(round(fh * _DEPLOY_BAR_SAFE_Y_FRAC))
        x, y = AttackStrategy._quantize_deploy_to_frame(612, 708, fw, fh)
        self.assertEqual(x, 612)
        self.assertLessEqual(y, safe_max_y)

        inp, vision, cfg, stop = Mock(), Mock(), Mock(), Mock()
        inp.window_service.screenshot.return_value = None
        stop.is_set.return_value = False
        stop.wait.return_value = False
        cfg.aspect_key = '16_9'
        cfg.get_point.side_effect = lambda k: {
            'top': (612, 10),
            'right': (1120, 325),
            'bottom': (612, 708),
            'left': (95, 325),
        }[k]
        vision.bottom_half_region.return_value = (0, 344, 1224, 345)
        vision.find_template.return_value = (200, 625)

        spam = TroopSpamStrategy(inp, vision, cfg, stop, 'sneaky', 0.4)
        frame = np.zeros((fh, fw, 3), dtype=np.uint8)
        spam.execute(frame, stop)

        for call in inp.mouse_down.call_args_list:
            _mx, my = call.args
            self.assertLessEqual(my, safe_max_y)
        for call in inp.human_move.call_args_list:
            _x1, y1, _x2, y2 = call.args[:4]
            self.assertLessEqual(y1, safe_max_y)
            self.assertLessEqual(y2, safe_max_y)


if __name__ == '__main__':
    unittest.main()
