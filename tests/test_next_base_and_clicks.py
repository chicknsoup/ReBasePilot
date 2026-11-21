import contextlib
import threading
import unittest
from unittest.mock import Mock, patch
import numpy as np

from app.core.bot import Bot
from app.core.loot_filter import LootFilter
from app.services.input import (
    InputService,
    MK_LBUTTON,
    WM_ACTIVATE,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_MOUSEMOVE,
    WM_SETFOCUS,
)


class NextBaseAndClickTests(unittest.TestCase):
    def _make_bot(self):
        bot = Bot.__new__(Bot)
        bot.config = Mock()
        bot.window = Mock()
        bot.window.hwnd = 12345
        bot.stop_event = Mock()
        bot.stop_event.is_set.return_value = False
        bot.stop_event.wait.return_value = False
        bot.input = Mock()
        bot.vision = Mock()
        bot._loot_filter = LootFilter(min_gold=1_000_000, min_elixir=1_000_000, max_skips=5)
        bot._loot_filter_off = False
        bot._bases_skipped = 0
        bot._status_callback = None
        bot._state_callback = None
        bot._update_config_size = Mock()
        return bot

    def test_click_sends_move_and_holds_button(self):
        ws = Mock()
        ws.hwnd = 999
        ws.get_outer_pixel_size.return_value = (1224, 689)
        ws.window_dpi_context = lambda _hwnd=None: contextlib.nullcontext()
        stop = threading.Event()
        svc = InputService(ws, stop)
        events = []
        svc._send_msg = lambda hwnd, msg, wp, lp: events.append((msg, wp))

        with patch('app.services.input.time.sleep') as sleep_mock:
            svc.click(200, 300, pause=0.05, rand=False)

        msgs = [m for m, _ in events]
        self.assertEqual(
            msgs,
            [WM_ACTIVATE, WM_SETFOCUS, WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP],
        )
        # Settle sleep after focus + hold sleep between DOWN and UP
        self.assertGreaterEqual(sleep_mock.call_count, 2)

    def test_eaten_next_click_reclicks_and_loads(self):
        bot = self._make_bot()
        same_frame = np.full((689, 1224, 3), 40, dtype=np.uint8)
        cloud_frame = np.full((689, 1224, 3), 240, dtype=np.uint8)

        # 10 polls + 1 check_frame still on same base (eaten), then 1st poll of retry sees clouds
        frames = [same_frame] * 11 + [cloud_frame]
        bot.window.screenshot.side_effect = frames
        bot._battle_prep_visible = lambda f: f is same_frame
        bot._find_next_base_button = lambda f: (1100, 560) if f is same_frame else (None, None)
        bot._wait_for_any_image = Mock(return_value=(72, 543))

        status = bot._wait_for_next_base(prev_frame=same_frame, prev_loot=(300_000, 300_000, 0))
        self.assertEqual(status, 'loaded')
        bot.input._ensure_surface_active.assert_called_once_with(12345, force=True)
        bot.input.click.assert_called_once_with(1100, 560, pause=0.05)

    @patch('app.core.bot.read_enemy_loot')
    def test_fast_cloud_transition_detected_without_false_crash(self, read_loot):
        bot = self._make_bot()
        old_frame = np.full((689, 1224, 3), 30, dtype=np.uint8)
        new_frame = np.full((689, 1224, 3), 120, dtype=np.uint8)

        bot.window.screenshot.side_effect = [new_frame, new_frame]
        bot._battle_prep_visible = lambda _f: True
        read_loot.return_value = (1_400_000, 1_500_000, 10_000)

        status = bot._wait_for_next_base(prev_frame=old_frame, prev_loot=(250_000, 280_000, 1_000))
        self.assertEqual(status, 'loaded')
        bot.input.click.assert_not_called()

    def test_expired_scout_timer_attacks_instead_of_crashing(self):
        bot = self._make_bot()
        live_frame = np.full((689, 1224, 3), 50, dtype=np.uint8)
        bot.window.screenshot.return_value = live_frame
        bot._battle_prep_visible = lambda _f: True
        bot._find_next_base_button = lambda _f: (None, None)

        status = bot._wait_for_next_base(prev_frame=live_frame, prev_loot=(300_000, 300_000, 0))
        self.assertEqual(status, 'attack_now')

    @patch('app.core.bot.get_template_path')
    def test_persistent_next_failure_recovers_without_raising(self, tpl_path):
        tpl_path.return_value.exists.return_value = True
        bot = self._make_bot()
        frame = np.full((689, 1224, 3), 40, dtype=np.uint8)
        bot.window.screenshot.return_value = frame
        bot._read_base_loot = Mock(return_value=(200_000, 200_000, 0))
        bot._find_next_base_button = Mock(return_value=(1100, 560))
        bot._wait_for_next_base = Mock(return_value='failed')

        ok = bot._skip_low_loot_bases()
        self.assertFalse(ok)

    def test_bot_controller_resets_consecutive_failures_after_loot(self):
        from app.ui.qt.bot_controller import BotController

        ctrl = BotController.__new__(BotController)
        ctrl._bot_version = 'test'
        ctrl._bot = Mock()
        ctrl._bot.stop_event = threading.Event()
        ctrl._bot_thread = None
        ctrl._active_worker_thread = None
        ctrl._restart_generation = 0
        ctrl.statusChanged = Mock()
        ctrl.botStarted = Mock()
        ctrl.botFinished = Mock()
        ctrl.runningChanged = Mock()
        ctrl.stateChanged = Mock()
        ctrl.lootChanged = Mock()
        ctrl._sleep_interruptible = Mock(return_value=False)

        # Crash 4 times, then earn loot on attempt 5 before crashing 2 more times, then succeed on attempt 7.
        # Without resetting consecutive_failures on loot, attempt 5's crash would hit 5/5 and abort!
        calls = [0]

        def fake_start(*_args, **kwargs):
            calls[0] += 1
            if calls[0] <= 4:
                raise RuntimeError(f'transient #{calls[0]}')
            if calls[0] == 5:
                kwargs['loot_callback'](1_200_000, 1_100_000, 9_000, 95.0)
                raise RuntimeError('later transient #5')
            if calls[0] == 6:
                raise RuntimeError('later transient #6')
            return None

        ctrl._bot.start.side_effect = fake_start
        with patch('app.ui.qt.bot_controller.logger'), patch('app.ui.qt.bot_controller.load_profile_settings') as load_prof:
            prof = Mock()
            prof.earthquake_method = 'Curve Placement'
            prof.wall_upgrade_threshold_m = 7
            prof.reserve_builders = 1
            prof.upgrade_order = 'priciest'
            prof.clan_chat_point = None
            prof.clan_chat_point_aspect = None
            prof.clan_dry_run = True
            prof.clan_donate_troop = 'auto'
            prof.clan_donate_count = 0
            prof.clan_min_elixir_k = 500
            prof.clan_donate_interval_m = 0
            prof.clan_request_interval_m = 30
            prof.attack_min_gold_k = 1000
            prof.attack_min_elixir_k = 1000
            prof.attack_max_skips = 20
            load_prof.return_value = prof

            ctrl.start(
                method=4,
                minutes=0,
                star_bonus=False,
                ranked_fill=False,
                upgrade_walls=False,
                multi_run_players=None,
            )
            th = ctrl._active_worker_thread
            if th is not None:
                th.join(timeout=5.0)

        self.assertEqual(calls[0], 7)
        ctrl.botFinished.emit.assert_called_with(None)


if __name__ == '__main__':
    unittest.main()
