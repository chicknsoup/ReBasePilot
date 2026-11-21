import ctypes
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.services.input import InputService
from app.services.mumu_ipc import (
    MuMuInstanceInfo,
    MuMuIpcSession,
    find_ipc_dll,
    list_configured_instances,
    mumu_sentinel_hwnd,
)
from app.services.vision import VisionService
from app.services.window import MK_LBUTTON, WindowCandidate, WindowService
from app.utils.common import get_template_path
from app.utils.window_settings_store import WindowSelection


class _FakeMuMuDll:
    def __init__(self, width=1600, height=900):
        self.width = width
        self.height = height
        self.next_handle = 0
        self.fail_next_capture = False
        self.calls = []
        # Bottom-up RGBA: row 0 in buffer is bottom of screen (blue), last row is top of screen (red)
        buf = np.zeros((height, width, 4), dtype=np.uint8)
        buf[0, :, :] = (0, 0, 255, 255)          # RGBA blue at buffer row 0 -> bottom row after flip
        buf[height - 1, :, :] = (255, 0, 0, 255) # RGBA red at last buffer row -> top row after flip
        self._raw_bytes = buf.tobytes()

    def nemu_connect(self, install_dir, idx):
        self.next_handle += 1
        self.calls.append(('connect', str(install_dir), int(idx), self.next_handle))
        return self.next_handle

    def nemu_disconnect(self, handle):
        self.calls.append(('disconnect', int(handle)))

    def nemu_capture_display(self, handle, display_id, buf_size, w_ptr, h_ptr, pixels_ptr):
        if self.fail_next_capture:
            self.fail_next_capture = False
            return -1
        w_ptr._obj.value = self.width
        h_ptr._obj.value = self.height
        if buf_size > 0 and pixels_ptr is not None:
            ctypes.memmove(pixels_ptr, self._raw_bytes, len(self._raw_bytes))
        return 0

    def nemu_input_event_touch_down(self, handle, display_id, x, y):
        self.calls.append(('touch_down', int(x), int(y)))
        return 0

    def nemu_input_event_touch_up(self, handle, display_id):
        self.calls.append(('touch_up',))
        return 0

    def nemu_input_event_finger_touch_down(self, handle, display_id, finger_id, x, y):
        if finger_id < 1 or finger_id > 10:
            return 3
        self.calls.append(('finger_down', int(finger_id), int(x), int(y)))
        return 0

    def nemu_input_event_finger_touch_up(self, handle, display_id, finger_id):
        if finger_id < 1 or finger_id > 10:
            return 3
        self.calls.append(('finger_up', int(finger_id)))
        return 0


class MuMuSupportTests(unittest.TestCase):
    def tearDown(self):
        VisionService._active_ui_scale = 1.0
        VisionService._ui_scale_calibrated_size = None

    def test_mumu_install_layout_and_vm_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dll_file = root / 'nx_main' / 'sdk' / 'external_renderer_ipc.dll'
            dll_file.parent.mkdir(parents=True)
            dll_file.write_bytes(b'MZ')
            vm_cfg = root / 'vms' / 'MuMuPlayerGlobal-15.0-2' / 'configs'
            vm_cfg.mkdir(parents=True)
            (vm_cfg / 'extra_config.json').write_text('{"playerName": "CoC Farm 2"}', encoding='utf-8')

            self.assertEqual(find_ipc_dll(root), dll_file)
            self.assertEqual(list_configured_instances(root), [(2, 'CoC Farm 2')])

    def test_capture_flips_bottom_up_rgba_to_bgr_and_reconnects(self):
        fake_dll = _FakeMuMuDll(width=320, height=180)
        sess = MuMuIpcSession(Path('C:/MuMu'), instance_index=0, dll=fake_dll)

        frame = sess.capture_bgr()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape, (180, 320, 3))
        # Top row must be BGR red (0, 0, 255), bottom row must be BGR blue (255, 0, 0)
        self.assertEqual(tuple(int(c) for c in frame[0, 10]), (0, 0, 255))
        self.assertEqual(tuple(int(c) for c in frame[-1, 10]), (255, 0, 0))

        # Transient RPC failure reconnects automatically
        fake_dll.fail_next_capture = True
        frame2 = sess.capture_bgr()
        self.assertIsNotNone(frame2)
        self.assertEqual(sess.handle, 2)

    def test_pinch_uses_valid_finger_ids_and_stays_in_hud_free_band(self):
        fake_dll = _FakeMuMuDll(width=1600, height=900)
        sess = MuMuIpcSession(Path('C:/MuMu'), instance_index=0, dll=fake_dll)
        sess.connect()

        with patch('app.services.mumu_ipc.time.sleep'):
            # Even when passed off-center (625, 625), pinch must stay in the central HUD-free band
            sess.pinch_zoom_out(cx=625, cy=625, strokes=1)

        finger_downs = [c for c in fake_dll.calls if c[0] == 'finger_down']
        finger_ups = [c for c in fake_dll.calls if c[0] == 'finger_up']
        self.assertTrue(finger_downs)
        self.assertEqual({c[1] for c in finger_downs}, {1, 2})
        self.assertEqual([c[1] for c in finger_ups], [1, 2])
        for _, _fid, fx, fy in finger_downs:
            self.assertGreaterEqual(abs(fx - 800), int(1600 * 0.08))
            self.assertTrue(int(1600 * 0.24) <= fx <= int(1600 * 0.76))
            self.assertTrue(int(900 * 0.38) <= fy <= int(900 * 0.52))

    def test_input_service_routes_zoom_vs_list_scroll_and_drag_on_mumu(self):
        fake_dll = _FakeMuMuDll(width=1600, height=900)
        sess = MuMuIpcSession(Path('C:/MuMu'), instance_index=0, dll=fake_dll)
        sess.connect()

        ws = WindowService.__new__(WindowService)
        ws.hwnd = mumu_sentinel_hwnd(0)
        ws.mumu_ipc = sess
        svc = InputService(ws)

        with patch('app.services.mumu_ipc.time.sleep'), patch('app.services.input.time.sleep'):
            # Hover move (wparam=0) must NOT touch screen; held move (MK_LBUTTON) must move finger
            fake_dll.calls.clear()
            svc.move(500, 400, 0)
            self.assertEqual(fake_dll.calls, [])
            svc.move(510, 410, MK_LBUTTON)
            self.assertEqual(fake_dll.calls, [('touch_down', 510, 410)])

            # Camera scroll -> 2-finger pinch
            fake_dll.calls.clear()
            svc.scroll(800, 450, 20)
            self.assertTrue(any(c[0] == 'finger_down' for c in fake_dll.calls))
            self.assertFalse(any(c[0] == 'touch_down' for c in fake_dll.calls))

            # List scroll down -> finger drags UPWARD (y decreases)
            fake_dll.calls.clear()
            svc.scroll(1100, 450, 5, list_scroll=True)
            tdowns = [c for c in fake_dll.calls if c[0] == 'touch_down']
            self.assertGreater(len(tdowns), 2)
            self.assertGreater(tdowns[0][2], tdowns[-1][2])

            # List reset to top (upward=True) -> finger drags DOWNWARD (y increases)
            fake_dll.calls.clear()
            svc.scroll(1100, 450, 24, upward=True, list_scroll=True)
            tdowns_up = [c for c in fake_dll.calls if c[0] == 'touch_down']
            self.assertGreater(len(tdowns_up), 2)
            self.assertLess(tdowns_up[0][2], tdowns_up[-1][2])

    def test_window_service_prefers_gpg_then_mumu_and_captures_when_minimized(self):
        fake_dll = _FakeMuMuDll(width=1600, height=900)
        sess = MuMuIpcSession(Path('C:/MuMu'), instance_index=0, dll=fake_dll)
        sess.connect()

        ws = WindowService.__new__(WindowService)
        ws.window_name = 'Clash of Clans'
        ws.child_class = 'CROSVM_1'
        ws.hwnd = 0
        ws.mumu_ipc = None

        gpg_cand = WindowCandidate(top_hwnd=100, title='Clash of Clans - Player', top_class='HwndWrapper', child_hwnd=101, child_class='CROSVM_1')
        mumu_cand = WindowCandidate(top_hwnd=200, title='Android Device', top_class='Qt5156QWindowIcon', child_hwnd=201, child_class='nemuwin')
        mumu_info = MuMuInstanceInfo(Path('C:/MuMu'), Path('C:/MuMu/ipc.dll'), 0, 'Android Device', 1600, 900)

        with patch.object(ws, 'enumerate_windows', return_value=[gpg_cand, mumu_cand]), \
             patch('app.services.window.discover_running_mumu_instances', return_value=[mumu_info]):
            self.assertEqual(ws._auto_detect_child(), 101)
            self.assertIsNone(ws.mumu_ipc)

        with patch.object(ws, 'enumerate_windows', return_value=[mumu_cand]), \
             patch('app.services.window.discover_running_mumu_instances', return_value=[mumu_info]), \
             patch.object(ws, '_bind_mumu_ipc_instance', side_effect=lambda _inst: setattr(ws, 'mumu_ipc', sess) or True):
            self.assertEqual(ws._auto_detect_child(), 201)
            self.assertIs(ws.mumu_ipc, sess)
            ws.hwnd = 201
            self.assertFalse(ws.is_minimized())
            self.assertFalse(ws.is_cloaked())
            shot = ws.screenshot()
            self.assertIsNotNone(shot)
            self.assertEqual(shot.shape, (900, 1600, 3))

    def test_adaptive_ui_dpi_scale_matches_240dpi_mumu_and_320dpi_gpg(self):
        from app.config import Config
        cfg = Config()
        cfg.set_target_size(1600, 900)
        tpl = cv2.imread(str(get_template_path('attack.png')))
        self.assertIsNotNone(tpl)
        th, tw = tpl.shape[:2]

        # Simulate 1600x900 @ 240 DPI (1.22x UI scale relative to 2560x1440 @ 320 DPI)
        frame_mumu = np.full((900, 1600, 3), 45, dtype=np.uint8)
        mumu_w = int(round(tw * (1600 / float(cfg.ref_width)) * 1.22))
        mumu_h = int(round(th * (900 / float(cfg.ref_height)) * 1.22))
        scaled_mumu = cv2.resize(tpl, (mumu_w, mumu_h), interpolation=cv2.INTER_AREA)
        frame_mumu[750:750 + mumu_h, 40:40 + mumu_w] = scaled_mumu

        mx, my = VisionService.find_template(frame_mumu, 'attack.png', threshold=0.82, region=VisionService.bottom_half_region(frame_mumu))
        self.assertIsNotNone(mx)
        self.assertAlmostEqual(VisionService._active_ui_scale, 1.22, delta=0.08)

        # Simulate switching back to 1600x900 @ 320 DPI (1.0x UI scale)
        frame_gpg = np.full((900, 1600, 3), 45, dtype=np.uint8)
        gpg_w = int(round(tw * (1600 / float(cfg.ref_width))))
        gpg_h = int(round(th * (900 / float(cfg.ref_height))))
        scaled_gpg = cv2.resize(tpl, (gpg_w, gpg_h), interpolation=cv2.INTER_AREA)
        frame_gpg[750:750 + gpg_h, 40:40 + gpg_w] = scaled_gpg

        gx, gy = VisionService.find_template(frame_gpg, 'attack.png', threshold=0.82, region=VisionService.bottom_half_region(frame_gpg))
        self.assertIsNotNone(gx)

    def test_240dpi_diamond_points_and_hero_retry(self):
        from unittest.mock import Mock
        from app.config import Config
        from app.core.strategies import AttackStrategy

        cfg = Config()
        cfg.set_target_size(2560, 1440)
        inp = Mock()
        strat = AttackStrategy(inp, VisionService(), cfg)

        VisionService._active_ui_scale = 1.0
        self.assertEqual(strat._point('left'), [190, 680])
        self.assertEqual(strat._hero_corner_xy('top'), (1280, -160))

        VisionService._active_ui_scale = 1.22
        self.assertEqual(strat._point('left'), [315, 645])
        self.assertEqual(strat._point('right'), [2245, 645])
        self.assertEqual(strat._hero_corner_xy('top'), (1280, -45))

        # Verify hero deploy retries when card stays undeployed on first click
        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        inp.window_service.screenshot.return_value = frame
        with patch.object(VisionService, 'find_template', side_effect=lambda _f, name, **_kw: (804, 817) if name == 'prince.png' else (None, None)), \
             patch.object(VisionService, 'find_template_with_confidence', side_effect=[(804, 817, 0.95), (None, None, 0.50)]):
            strat.deploy_heroes(frame)
        # 2 clicks initial (select + deploy) + 2 clicks retry (reselect + redeploy) + 1 ability click = 5
        self.assertEqual(inp.click.call_count, 5)


if __name__ == '__main__':
    unittest.main()
