"""Tests for v1.2.1: window title version, fast enemy loot reading, HUD cache, and 4-angle smart Edrag."""
import pathlib
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import __version__
from app.config import Config
from app.core.bot import Bot
from app.core.loot_filter import LootFilter, _read_enemy_loot_fast, read_battle_remaining_loot, read_enemy_loot
from app.core.strategies import EdragStrategy, _EDGE_CORNERS
from app.services.vision import VisionService


REAL_FRAME_1600 = pathlib.Path(r'C:\Users\chinhnc\AppData\Local\BaseLooter\debug\troop_missing_edrag_1795237360.jpg')
REAL_FRAME_1024 = pathlib.Path(
    r'C:\Users\chinhnc\.gemini\antigravity\brain\bb2b8194-fc00-477e-954b-7384172661d0'
    r'\.user_uploaded\media_1795231477333_2dacf1f3.jpg'
)
REAL_FRAME_1X = pathlib.Path(
    r'C:\Users\chinhnc\.gemini\antigravity\brain\bb2b8194-fc00-477e-954b-7384172661d0'
    r'\.user_uploaded\media_1795248601802_bbaaf070.jpg'
)
REAL_FRAME_1M = pathlib.Path(
    r'C:\Users\chinhnc\.gemini\antigravity\brain\bb2b8194-fc00-477e-954b-7384172661d0'
    r'\.user_uploaded\media_1795249401797_a27c163d.jpg'
)
REAL_FRAME_CHEST_TAP = pathlib.Path(
    r'C:\Users\chinhnc\.gemini\antigravity\brain\bb2b8194-fc00-477e-954b-7384172661d0'
    r'\.user_uploaded\media_1795249949267_5c6b819b.jpg'
)
REAL_FRAME_CHEST_CONTINUE = pathlib.Path(
    r'C:\Users\chinhnc\.gemini\antigravity\brain\bb2b8194-fc00-477e-954b-7384172661d0'
    r'\.user_uploaded\media_1795249997115_8d384016.jpg'
)


class TestWindowTitleVersion(unittest.TestCase):
    def test_version_and_title_formatting(self):
        from app.ui.qt.main_window import format_window_title

        self.assertEqual(__version__, '1.4.5')
        self.assertEqual(format_window_title('', '1.4.5'), 'BaseLooter v1.4.5')
        self.assertEqual(format_window_title('Main', '1.4.5'), 'BaseLooter v1.4.5 — Main')


class TestFastEnemyLootAndSpeed(unittest.TestCase):
    def test_fast_reader_on_real_screenshots_without_tesseract(self):
        # High-res 1600x900 live capture must decode purely via fast glyph reader (0 Tesseract calls)
        if REAL_FRAME_1600.exists():
            frame = cv2.imread(str(REAL_FRAME_1600))
            self.assertIsNotNone(frame)
            with patch.object(
                VisionService,
                'extract_grouped_numbers_in_region',
                side_effect=AssertionError('Tesseract fallback must not be called on crisp 1600x900 frame'),
            ):
                t0 = time.perf_counter()
                loot = read_enemy_loot(frame)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.assertEqual(loot, (1900904, 2004691, 10422))
            self.assertLess(elapsed_ms, 500.0, f'Fast reader too slow: {elapsed_ms:.1f}ms')

    def test_lopsided_low_loot_triggers_ocr_rescue_before_skip(self):
        bot = Bot.__new__(Bot)
        bot.stop_event = threading.Event()
        bot.window = MagicMock()
        bot.window.screenshot.return_value = np.zeros((900, 1600, 3), dtype=np.uint8)
        bot.input = MagicMock()
        bot._loot_filter = LootFilter(min_gold=1_000_000, min_elixir=1_000_000, max_skips=5)
        bot._last_loot_read_agreed = True
        bot._read_base_loot = MagicMock(return_value=(5678, 1_847_500, 9000))

        with (
            patch('app.core.bot.get_template_path') as mock_tpl,
            patch('app.core.bot.read_enemy_loot_ocr', return_value=(1_567_800, 1_847_500, 9000)) as mock_ocr,
        ):
            mock_tpl.return_value.exists.return_value = True
            should_attack = bot._skip_low_loot_bases()
        self.assertTrue(should_attack)
        mock_ocr.assert_called_once()
        bot.input.click.assert_not_called()

    def test_ambiguous_1024p_8_does_not_silently_misread_as_0_in_fast_mode(self):
        if REAL_FRAME_1M.exists():
            fr = cv2.imread(str(REAL_FRAME_1M))
            fast = _read_enemy_loot_fast(fr)
            # Either exact (1049668, 1021800, 1360) or None (deferred to Tesseract) — never 1021000
            if fast is not None:
                self.assertEqual(fast[:2], (1049668, 1021800))

    def test_blank_frame_falls_back_to_tesseract_path(self):
        blank = np.zeros((576, 1024, 3), dtype=np.uint8)
        self.assertIsNone(_read_enemy_loot_fast(blank))
        with patch.object(VisionService, 'extract_grouped_numbers_in_region', return_value=[]) as mock_ocr:
            self.assertIsNone(read_enemy_loot(blank))
            mock_ocr.assert_called_once()

    def test_read_base_loot_stops_early_on_two_matching_reads(self):
        bot = Bot.__new__(Bot)
        bot.stop_event = threading.Event()
        bot.window = MagicMock()
        bot.window.screenshot.return_value = np.zeros((900, 1600, 3), dtype=np.uint8)
        bot._update_config_size = MagicMock()

        with patch('app.core.bot.read_enemy_loot', return_value=(120000, 150000, 1500)) as mock_read:
            loot = bot._read_base_loot(polls=6, samples=3)
        self.assertEqual(loot, (120000, 150000, 1500))
        self.assertTrue(bot._last_loot_read_agreed)
        self.assertEqual(mock_read.call_count, 2)

    def test_refresh_home_resources_reuses_fresh_cached_hud(self):
        bot = Bot.__new__(Bot)
        bot._last_hud_triplet = (5000000, 6000000, 50000)
        bot._last_hud_triplet_ts = time.monotonic()
        bot._read_hud_triplet_stable = MagicMock(side_effect=AssertionError('Should not re-read fresh HUD'))
        bot._refresh_home_resources(max_age_s=15.0)
        bot._read_hud_triplet_stable.assert_not_called()


class TestSmartEdragStrategy(unittest.TestCase):
    def _make_strategy(self, ui_scale=1.22):
        cfg = Config()
        cfg.set_target_size(1600, 900)
        VisionService._active_ui_scale = ui_scale
        strat = EdragStrategy(MagicMock(), MagicMock(), cfg, threading.Event())
        return strat

    def test_loot_cluster_selects_closest_adjacent_front(self):
        strat = self._make_strategy(ui_scale=1.22)
        fh, fw = 900, 1600
        data = strat._scaled_deployment_data()
        cx = int(sum(data[k][0] for k in ('left', 'top', 'right', 'bottom')) * 0.25)
        cy = int(sum(data[k][1] for k in ('left', 'top', 'right', 'bottom')) * 0.25)

        # Vivid magenta Elixir color in BGR: HSV(150, 220, 230) -> BGR approx (215, 32, 230)
        elixir_bgr = (215, 32, 230)
        quadrants = [
            ('NW', (cx - 180, cy - 110)),
            ('NE', (cx + 180, cy - 110)),
            ('SE', (cx + 180, cy + 95)),
            ('SW', (cx - 180, cy + 95)),
        ]
        for expected_edge, (qx, qy) in quadrants:
            frame = np.full((fh, fw, 3), (40, 110, 40), dtype=np.uint8)
            cv2.circle(frame, (qx, qy), 45, elixir_bgr, -1)
            edges, _corners, label, reason = strat._choose_loot_attack_edges(frame)
            self.assertEqual(reason, 'loot-targeted')
            self.assertIn(expected_edge, label.split('+'), f'Expected {expected_edge} in {label}')
            self.assertIn(_EDGE_CORNERS[expected_edge], edges)

    def test_smart_front_on_real_battle_frames(self):
        strat = self._make_strategy(ui_scale=1.22)
        if REAL_FRAME_1600.exists():
            f1600 = cv2.imread(str(REAL_FRAME_1600))
            strat._sync_frame_size(f1600)
            _edges, _corners, label, reason = strat._choose_loot_attack_edges(f1600)
            self.assertEqual(reason, 'loot-targeted')
            self.assertIn('NW', label.split('+'), f'Top-left storages should include NW, got {label}')
        if REAL_FRAME_1024.exists():
            f1024 = cv2.imread(str(REAL_FRAME_1024))
            strat._sync_frame_size(f1024)
            _edges, _corners, label, reason = strat._choose_loot_attack_edges(f1024)
            self.assertEqual(reason, 'loot-targeted')
            self.assertTrue(
                'SE' in label.split('+') or 'NE' in label.split('+'),
                f'Right-side storages should pick NE/SE, got {label}',
            )

    def test_all_four_edges_respect_deploy_bar_and_hud_safe_bounds(self):
        strat = self._make_strategy(ui_scale=1.22)
        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        fh, fw = frame.shape[:2]
        safe_max_y = strat._safe_deploy_max_y(fh)

        for edge_name, (c1, c2) in _EDGE_CORNERS.items():
            strat._active_attack_edges = ((c1, c2),)
            for _ in range(40):
                px, py = strat._random_diamond_perimeter_point(frame)
                self.assertGreaterEqual(px, 0)
                self.assertLess(px, fw)
                self.assertGreaterEqual(py, 0)
                self.assertLessEqual(py, safe_max_y, f'Edge {edge_name} breached deploy bar: y={py} > {safe_max_y}')
                if py > fh * 0.66:
                    self.assertGreaterEqual(px, fw * 0.16, f'Edge {edge_name} hit Surrender area: ({px},{py})')
                    self.assertLessEqual(px, fw * 0.84, f'Edge {edge_name} hit Overall Damage area: ({px},{py})')

    def test_spell_anchors_adapt_to_non_top_front(self):
        strat = self._make_strategy(ui_scale=1.22)
        data = strat._scaled_deployment_data()
        offset = int(strat.config.get_scaled('earthquake', 400))
        top_ltr = strat._earthquake_anchor_triplet(data, offset, front_corners=('left', 'top', 'right'))
        right_ltr = strat._earthquake_anchor_triplet(data, offset, front_corners=('top', 'right', 'bottom'))
        # Right-front middle anchor should lie noticeably to the right of top-front middle anchor
        self.assertGreater(right_ltr[1][0], top_ltr[1][0] + 100)

    def test_fallback_top_forces_upper_edges_even_when_lower_front_active(self):
        strat = self._make_strategy(ui_scale=1.22)
        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        strat._active_attack_edges = (_EDGE_CORNERS['SE'], _EDGE_CORNERS['SW'])
        for _ in range(30):
            _px, py = strat._random_diamond_perimeter_point(frame, fallback_top=True)
            self.assertLessEqual(py, 450, f'fallback_top should stay on upper edges, got y={py}')


class TestSmartBattleWaitAndFastForward(unittest.TestCase):
    def _make_bot(self, frame):
        bot = Bot.__new__(Bot)
        bot.config = Config()
        bot.stop_event = MagicMock()
        bot.stop_event.is_set.return_value = False
        bot.stop_event.wait.return_value = False
        bot.window = MagicMock()
        bot.window.screenshot.return_value = frame
        bot.vision = MagicMock()
        bot.vision.find_template.return_value = (None, None)
        bot.input = MagicMock()
        bot._battle_rewards = MagicMock()
        bot._battle_rewards.handle.return_value = False
        bot._find_reload_button = MagicMock(return_value=(None, None, None))
        bot._end_battle_now = MagicMock()
        return bot

    def test_real_1x_frame_reads_zero_loot_and_detects_speed_button(self):
        if REAL_FRAME_1X.exists():
            f1x = cv2.imread(str(REAL_FRAME_1X))
            self.assertEqual(read_battle_remaining_loot(f1x), (0, 0, 0))
            sx, sy = Bot._find_battle_speed_button(f1x)
            self.assertIsNotNone(sx)
            self.assertTrue(950 <= sx <= 1015, f'Unexpected 1x x={sx}')
            self.assertTrue(295 <= sy <= 365, f'Unexpected 1x y={sy}')
        for non_1x in (REAL_FRAME_1600, REAL_FRAME_1024):
            if non_1x.exists():
                fr = cv2.imread(str(non_1x))
                self.assertEqual(Bot._find_battle_speed_button(fr), (None, None), f'False 1x on {non_1x.name}')

    def test_wait_ends_early_when_zero_loot_confirmed(self):
        frame = np.zeros((574, 1024, 3), dtype=np.uint8)
        bot = self._make_bot(frame)
        with patch('app.core.bot.read_battle_remaining_loot', return_value=(0, 0, 0)):
            bot._wait_for_battle_end(is_sneaky=False)
        bot._end_battle_now.assert_called_once()
        bot.input.click.assert_not_called()

    def test_wait_ends_after_15s_no_loot_gain_and_clicks_1x_only_once(self):
        frame = np.zeros((574, 1024, 3), dtype=np.uint8)
        bot = self._make_bot(frame)
        bot._battle_has_alive_hero_hp_bar = MagicMock(return_value=True)
        # Simulated clock advancing 1s per poll; loot drops once at t=2s then stalls for 15s
        clock = {'t': 100.0}

        def fake_mono():
            return clock['t']

        def fake_wait(_sec):
            clock['t'] += 1.0
            return False

        bot.stop_event.wait.side_effect = fake_wait
        loot_seq = [(500000, 500000, 2000), (500000, 500000, 2000), (400000, 400000, 2000)]

        def fake_loot(_fr):
            idx = int(clock['t'] - 100.0)
            return loot_seq[idx] if idx < len(loot_seq) else loot_seq[-1]

        with (
            patch('app.core.bot.time.monotonic', side_effect=fake_mono),
            patch('app.core.bot.read_battle_remaining_loot', side_effect=fake_loot),
            patch.object(Bot, '_find_battle_speed_button', return_value=(981, 328)),
        ):
            bot._wait_for_battle_end(is_sneaky=False)

        # Must click 1x exactly ONCE (never toggling back to 1x), then end after 15s stall
        bot.input.click.assert_called_once_with(981, 328, pause=0.15, rand=False)
        bot._end_battle_now.assert_called_once()
        self.assertLessEqual(clock['t'] - 100.0, 18.0)


class TestChestTapOpenFlow(unittest.TestCase):
    def test_detects_chest_tap_and_continue_on_real_frames(self):
        bot = Bot.__new__(Bot)
        bot.config = Config()
        bot.vision = VisionService()
        VisionService._active_ui_scale = 1.0
        bot._match_ui_control = MagicMock(return_value=(None, None))

        if REAL_FRAME_CHEST_TAP.exists():
            f_tap = cv2.imread(str(REAL_FRAME_CHEST_TAP))
            self.assertTrue(bot._is_chest_tap_screen(f_tap))
            self.assertEqual(bot._find_chest_continue_button(f_tap), (None, None))
        if REAL_FRAME_CHEST_CONTINUE.exists():
            f_cont = cv2.imread(str(REAL_FRAME_CHEST_CONTINUE))
            self.assertFalse(bot._is_chest_tap_screen(f_cont))
            tx, ty = bot._find_chest_continue_button(f_cont)
            self.assertIsNotNone(tx)
            self.assertTrue(410 <= tx <= 590)
            self.assertTrue(460 <= ty <= 530)
        if REAL_FRAME_1600.exists():
            f_battle = cv2.imread(str(REAL_FRAME_1600))
            self.assertFalse(bot._is_chest_tap_screen(f_battle))

    def test_tap_four_times_then_click_continue(self):
        if not (REAL_FRAME_CHEST_TAP.exists() and REAL_FRAME_CHEST_CONTINUE.exists()):
            self.skipTest('Real chest screenshots not available')
        f_tap = cv2.imread(str(REAL_FRAME_CHEST_TAP))
        f_cont = cv2.imread(str(REAL_FRAME_CHEST_CONTINUE))

        bot = Bot.__new__(Bot)
        bot.config = Config()
        bot.config.set_target_size(1024, 576)
        bot.vision = VisionService()
        VisionService._active_ui_scale = 1.0
        bot.stop_event = MagicMock()
        bot.stop_event.is_set.return_value = False
        bot.stop_event.wait.return_value = False
        bot.input = MagicMock()
        bot.window = MagicMock()
        # 3 taps still on Tap! screen, 4th tap reveals Continue reward screen, then None (exited)
        bot.window.screenshot.side_effect = [f_tap, f_tap, f_tap, f_cont, None]
        bot._match_ui_control = MagicMock(return_value=(None, None))

        bot._tap_empty_until_chest_continue()
        self.assertEqual(bot.input.click_at.call_count, 4)
        bot.input.click.assert_called_once()
        cx, cy = bot.input.click.call_args[0][:2]
        self.assertTrue(410 <= cx <= 590)
        self.assertTrue(460 <= cy <= 530)


class TestResilientLogRotation(unittest.TestCase):
    def test_multiple_loggers_share_handler_and_rotate_cleanly_on_windows(self):
        import logging
        import tempfile
        from app.utils.logger import _FILE_HANDLERS, _SafeRotatingFileHandler, setup_logger

        with tempfile.TemporaryDirectory() as tmp:
            log_path = pathlib.Path(tmp) / 'test_rotate.log'
            a = setup_logger('TestRotA_125', log_file=str(log_path))
            b = setup_logger('TestRotB_125', log_file=str(log_path))
            fh_a = next(h for h in a.handlers if isinstance(h, _SafeRotatingFileHandler))
            fh_b = next(h for h in b.handlers if isinstance(h, _SafeRotatingFileHandler))
            self.assertIs(fh_a, fh_b)

            # Force small maxBytes to trigger rollover across both loggers
            fh_a.maxBytes = 200
            for i in range(10):
                a.info('Logger A line %d %s', i, 'x' * 40)
                b.info('Logger B line %d %s', i, 'y' * 40)
            fh_a.flush()

            backup = pathlib.Path(tmp) / 'test_rotate.log.1'
            self.assertTrue(backup.exists(), 'Expected rotated backup .1 to be created cleanly')
            content = log_path.read_text(encoding='utf-8')
            self.assertIn('Logger B line 9', content)

            # Clean up shared handler cache & loggers so temp dir can be deleted on Windows
            for lg in (a, b):
                lg.handlers.clear()
            fh_a.close()
            for k in list(_FILE_HANDLERS.keys()):
                if _FILE_HANDLERS[k] is fh_a:
                    del _FILE_HANDLERS[k]

    def test_rollover_permission_error_reopens_stream_and_keeps_logging(self):
        import logging
        import tempfile
        from app.utils.logger import _SafeRotatingFileHandler

        with tempfile.TemporaryDirectory() as tmp:
            log_path = pathlib.Path(tmp) / 'locked.log'
            handler = _SafeRotatingFileHandler(str(log_path), mode='a', encoding='utf-8', maxBytes=80, backupCount=2)
            lg = logging.getLogger('TestLockedRot_125')
            lg.setLevel(logging.INFO)
            lg.handlers.clear()
            lg.addHandler(handler)
            try:
                with patch.object(handler, 'rotate', side_effect=PermissionError('WinError 32 simulated')):
                    lg.info('first line %s', 'a' * 60)
                    lg.info('after failed rollover line')
                handler.flush()
                text = log_path.read_text(encoding='utf-8')
                self.assertIn('after failed rollover line', text)
            finally:
                lg.handlers.clear()
                handler.close()

    def test_logs_page_tail_resets_position_after_rotation(self):
        import tempfile
        from app.ui.qt.pages.logs import LogsPage

        with tempfile.TemporaryDirectory() as tmp:
            log_path = pathlib.Path(tmp) / 'tail.log'
            log_path.write_text('2026-11-22 08:30:00 - BotCore - INFO - fresh after rotate\n', encoding='utf-8')

            page = LogsPage.__new__(LogsPage)
            page._pos = 5_242_880  # old offset before 5 MB rotation
            page._all_lines = []
            page._active_category = 'all'
            page._search_query = ''
            page._log_view = MagicMock()
            page._log_view.document.return_value.characterCount.return_value = 1
            page._log_view.document.return_value.blockCount.return_value = 1
            page._auto_scroll_toggle = MagicMock()
            page._auto_scroll_toggle.isChecked.return_value = True
            page._line_count_label = MagicMock()

            with patch('app.ui.qt.pages.logs.get_autoloot_log_path', return_value=log_path):
                page._tail_log()

            self.assertEqual(len(page._all_lines), 1)
            self.assertIn('fresh after rotate', page._all_lines[0])
            self.assertEqual(page._pos, log_path.stat().st_size)


class TestAccountBadgeAndHomeName(unittest.TestCase):
    def test_read_home_account_name_strips_xp_number_and_rejects_noise(self):
        from types import SimpleNamespace

        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        fake_words = [
            SimpleNamespace(left=5, top=10, text='215'),
            SimpleNamespace(left=40, top=12, text='ChinhNC'),
            SimpleNamespace(left=40, top=58, text='SubClanLabel'),
        ]
        with patch.object(VisionService, 'find_words_ocr', return_value=fake_words):
            self.assertEqual(Bot._read_home_account_name(frame), 'ChinhNC')

        noise_words = [SimpleNamespace(left=5, top=10, text='123')]
        with patch.object(VisionService, 'find_words_ocr', return_value=noise_words):
            self.assertIsNone(Bot._read_home_account_name(frame))

    def test_read_base_loot_respects_time_budget_so_scout_timer_does_not_expire(self):
        bot = Bot.__new__(Bot)
        bot.stop_event = threading.Event()
        bot.window = MagicMock()
        bot.window.screenshot.return_value = np.zeros((900, 1600, 3), dtype=np.uint8)
        bot._update_config_size = MagicMock()
        clock = {'t': 10.0}

        def slow_read(_fr):
            clock['t'] += 9.0
            return (1_352_866, 853_404, 5000)

        with (
            patch('app.core.bot.time.monotonic', side_effect=lambda: clock['t']),
            patch('app.core.bot.read_enemy_loot', side_effect=slow_read) as mock_read,
        ):
            loot = bot._read_base_loot(polls=6, samples=3, max_seconds=8.0)
        self.assertEqual(loot, (1_352_866, 853_404, 5000))
        self.assertEqual(mock_read.call_count, 1)

    def test_run_page_top_badge_shows_account_name_while_stat_state_shows_bot_state(self):
        from app.ui.qt.pages.run import RunPage

        page = RunPage.__new__(RunPage)
        page._account_name = ''
        page._current_state_kind = 'stopped'
        page._state_badge = MagicMock()
        page._stat_state = MagicMock()
        page._stat_builders = MagicMock()
        page._stat_lab = MagicMock()
        page._stat_storages = MagicMock()
        page._stat_skipped = MagicMock()
        page._stat_note = MagicMock()

        page._on_state_changed({'account': 'ChinhNC', 'state': 'FARMING'})
        page._state_badge.setText.assert_called_with('● ChinhNC')
        page._stat_state.setText.assert_called_with('FARMING')

    def test_outline_filter_ignores_bright_snow_scenery_blobs(self):
        # Bright snow scenery full of white blobs WITHOUT black outlines must not trigger glyph OCR
        snow = np.full((900, 1600, 3), (235, 240, 245), dtype=np.uint8)
        for x in range(150, 320, 24):
            cv2.rectangle(snow, (x, 45), (x + 14, 65), (255, 255, 255), -1)
        VisionService._active_ui_scale = 1.22
        with patch.object(VisionService, 'find_words_ocr', return_value=[]) as mock_ocr:
            res = Bot._read_home_account_name(snow)
        self.assertIsNone(res)
        # No outlined glyphs passed -> must not call preprocess=False clean OCR path
        for call in mock_ocr.call_args_list:
            self.assertNotEqual(call.kwargs.get('preprocess'), False)

    def test_returnhome_match_at_090x_does_not_overwrite_global_122x_ui_scale(self):
        # Simulate calibrated 240-DPI MuMu (1.22x) encountering a 0.90x returnhome.png button
        VisionService._active_ui_scale = 1.22
        VisionService._ui_scale_calibrated_size = (1600, 900)
        tpl = cv2.imread(str(pathlib.Path(r'F:\ReBasePilot\templates\16_9\returnhome.png')))
        self.assertIsNotNone(tpl)
        sx, sy = 1600.0 / 2560.0, 900.0 / 1440.0
        scaled_090 = cv2.resize(tpl, (int(round(tpl.shape[1] * sx * 0.90)), int(round(tpl.shape[0] * sy * 0.90))))
        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        frame[700:700 + scaled_090.shape[0], 650:650 + scaled_090.shape[1]] = scaled_090

        v = VisionService()
        rx, ry = v.find_template(frame, 'returnhome.png', region=VisionService.bottom_half_region(frame))
        self.assertIsNotNone(rx)
        self.assertAlmostEqual(VisionService._active_ui_scale, 1.22, places=2)

    def test_startup_panel_x_at_1x_does_not_lock_calibration_before_attack_122x(self):
        # Fresh startup: uncalibrated, panel_x.png appears first at 1.0x -> must NOT lock calibration to 1.0x
        VisionService._active_ui_scale = 1.0
        VisionService._ui_scale_calibrated_size = None
        px_tpl = cv2.imread(str(pathlib.Path(r'F:\ReBasePilot\templates\16_9\panel_x.png')))
        atk_tpl = cv2.imread(str(pathlib.Path(r'F:\ReBasePilot\templates\16_9\attack.png')))
        self.assertIsNotNone(px_tpl)
        self.assertIsNotNone(atk_tpl)
        sx, sy = 1600.0 / 2560.0, 900.0 / 1440.0

        # 1) panel_x at 1.0x matches cleanly but leaves calibration open
        px_1x = cv2.resize(px_tpl, (int(round(px_tpl.shape[1] * sx)), int(round(px_tpl.shape[0] * sy))))
        f_popup = np.zeros((900, 1600, 3), dtype=np.uint8)
        f_popup[15:15 + px_1x.shape[0], 1540:1540 + px_1x.shape[1]] = px_1x
        v = VisionService()
        x, y = v.find_template(f_popup, 'panel_x.png')
        self.assertIsNotNone(x)
        self.assertIsNone(VisionService._ui_scale_calibrated_size)

        # 2) Then Home screen attack.png at 1.22x calibrates global scale to 1.22x
        atk_122 = cv2.resize(atk_tpl, (int(round(atk_tpl.shape[1] * sx * 1.22)), int(round(atk_tpl.shape[0] * sy * 1.22))))
        f_home = np.zeros((900, 1600, 3), dtype=np.uint8)
        f_home[720:720 + atk_122.shape[0], 40:40 + atk_122.shape[1]] = atk_122
        ax, ay = v.find_template(f_home, 'attack.png', region=VisionService.bottom_half_region(f_home))
        self.assertIsNotNone(ax)
        self.assertAlmostEqual(VisionService._active_ui_scale, 1.22, delta=0.05)


class TestNeuralOcrAndEngineSwitch(unittest.TestCase):
    def tearDown(self):
        VisionService.set_ocr_engine('tesseract')

    def test_neural_models_bundled_and_available(self):
        from app.services.neural_ocr import NeuralOcrService

        self.assertTrue(NeuralOcrService.models_present())
        self.assertTrue(NeuralOcrService.is_available())

    def test_profile_settings_ocr_engine_roundtrip(self):
        import tempfile
        from app.utils import profile_settings_store as store

        with tempfile.TemporaryDirectory() as tmp:
            fake_path = pathlib.Path(tmp) / 'settings.json'
            with patch.object(store, 'get_settings_path', return_value=fake_path):
                store.save_profile_settings(store.ProfileSettings(ocr_engine='neural'))
                loaded = store.load_profile_settings()
                self.assertEqual(loaded.ocr_engine, 'neural')

                # Invalid values fall back to tesseract
                self.assertEqual(store._normalize_ocr_engine('invalid_engine'), 'tesseract')

    def test_neural_ocr_reads_home_account_name_and_battle_words(self):
        VisionService.set_ocr_engine('neural')
        self.assertEqual(VisionService.get_ocr_engine(), 'neural')

        hud_crop = pathlib.Path(
            r'C:\Users\chinhnc\.gemini\antigravity\brain\bb2b8194-fc00-477e-954b-7384172661d0'
            r'\.user_uploaded\media_1795313750361_a6cc3882.png'
        )
        if hud_crop.exists():
            crop = cv2.imread(str(hud_crop))
            self.assertIsNotNone(crop)
            canvas = np.zeros((900, 1600, 3), dtype=np.uint8)
            ch, cw = crop.shape[:2]
            canvas[:ch, :cw] = crop
            prev_scale = VisionService._active_ui_scale
            try:
                VisionService._active_ui_scale = 1.0
                self.assertEqual(Bot._read_home_account_name(canvas), 'Soup')
                # Live MuMu 1.22x placement: account name sits around y=72..102, clan row below at y=102..127
                canvas_122 = np.zeros((900, 1600, 3), dtype=np.uint8)
                canvas_122[30:30 + ch, 40:40 + cw] = crop
                VisionService._active_ui_scale = 1.22
                self.assertEqual(Bot._read_home_account_name(canvas_122), 'Soup')
            finally:
                VisionService._active_ui_scale = prev_scale

        if REAL_FRAME_1600.exists():
            frame = cv2.imread(str(REAL_FRAME_1600))
            self.assertIsNotNone(frame)
            words = VisionService.find_words_ocr(
                frame,
                region=(0, 0, 480, 300),
                query='Available',
                white_text=True,
            )
            self.assertTrue(any('available' in w.text.lower() for w in words), f'Got words: {words}')

    def test_update_home_account_name_requires_two_matching_frames(self):
        bot = Bot.__new__(Bot)
        bot.stop_event = threading.Event()
        bot.window = MagicMock()
        dummy = np.zeros((900, 1600, 3), dtype=np.uint8)
        bot.window.screenshot.return_value = dummy
        bot._state_callback = MagicMock()

        # First call sees transient 'Boup' then 'Soup' -> must NOT lock 'Boup'
        with (
            patch.object(bot, '_is_chest_tap_screen', return_value=False),
            patch.object(bot, '_read_home_account_name', side_effect=['Boup', 'Soup']),
        ):
            bot._update_home_account_name(dummy)
        self.assertIsNone(getattr(bot, '_loot_account', None))

        # Second call sees 'Soup' twice -> locks 'Soup'
        with (
            patch.object(bot, '_is_chest_tap_screen', return_value=False),
            patch.object(bot, '_read_home_account_name', side_effect=['Soup', 'Soup']),
        ):
            bot._update_home_account_name(dummy)
        self.assertEqual(bot._loot_account, 'Soup')

    def test_wall_upgrade_more_found_in_two_row_action_bar(self):
        wall_frame = pathlib.Path(r'C:\Users\chinhnc\AppData\Local\BaseLooter\debug\wallrow_1795343138.jpg')
        if not wall_frame.exists():
            return
        fr = cv2.imread(str(wall_frame))
        self.assertIsNotNone(fr)
        fh, fw = fr.shape[:2]
        bar_roi = (int(fw * 0.16), int(fh * 0.56), int(fw * 0.62), int(fh * 0.42))
        prev_scale = VisionService._active_ui_scale
        prev_size = VisionService._ui_scale_calibrated_size
        try:
            VisionService._active_ui_scale = 1.22
            VisionService._ui_scale_calibrated_size = (fw, fh)
            v = VisionService()
            umx, umy = v.find_template(fr, 'upgrademore.png', threshold=0.66, region=bar_roi)
            self.assertIsNotNone(umx, 'upgrademore.png must match in widened two-row bar_roi')
            self.assertGreater(umx, 650)
            self.assertLess(umx, 860)
            self.assertGreater(umy, 560)
            self.assertLess(umy, 720)
        finally:
            VisionService._active_ui_scale = prev_scale
            VisionService._ui_scale_calibrated_size = prev_size


if __name__ == '__main__':
    unittest.main()
