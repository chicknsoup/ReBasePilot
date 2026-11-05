# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\services\\input.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

import ctypes
import time
import random
import math
from typing import Tuple
from app.services.window import WindowService, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, MK_LBUTTON, WM_MOUSEWHEEL, WHEEL_DELTA
from app.utils.logger import setup_logger
logger = setup_logger('InputService')
_CLICK_JITTER_REF_PX = 15
_REFERENCE_WIDTH = 2560
class InputService:
    """Handles mouse and keyboard injection."""
    def __init__(self, window_service, stop_event=None):
        self.window_service = window_service
        self.stop_event = stop_event
        self.user32 = ctypes.windll.user32
    def _click_jitter(self):
        """Half-width of the click scatter, in capture pixels.\n\nThe +/-15 px it was written with is 15 px *at the authoring resolution*. Left\nunscaled it doubles in relative terms on a 1299-wide window, where a builder-menu\nrow is only ~30 px tall — so the scatter, meant to keep clicks off one exact\npixel, was landing them on the row above or below.\n"""
        sz = self.window_service.get_outer_pixel_size()
        if not sz or not sz[0] or sz[0] <= 1:
            return _CLICK_JITTER_REF_PX
        else:
            return max(3, int(round(_CLICK_JITTER_REF_PX * sz[0] / float(_REFERENCE_WIDTH))))
    def _clamp_to_capture(self, x, y):
        """\nClamp client-style coordinates into the current captured window rectangle\n(:meth:`WindowService.get_outer_pixel_size`, same outer size as screenshots).\n"""
        sz = self.window_service.get_outer_pixel_size()
        if not sz:
            return (int(x), int(y))
        else:
            w, h = sz
            if w <= 1 or h <= 1:
                return (int(x), int(y))
            else:
                cx = max(0, min(w - 1, int(x)))
                cy = max(0, min(h - 1, int(y)))
                return (cx, cy)
    def _make_lparam(self, x, y):
        xc, yc = self._clamp_to_capture(x, y)
        return yc << 16 | xc & 65535
    def _client_to_screen(self, x, y):
        """Map capture/client coords to screen coords for ``WM_MOUSEWHEEL``."""
        hwnd = self.window_service.hwnd
        if not hwnd:
            return self._clamp_to_capture(x, y)
        else:
            xc, yc = self._clamp_to_capture(x, y)
            class POINT(ctypes.Structure):
                _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]
            pt = POINT(xc, yc)
            with self.window_service.window_dpi_context(hwnd):
                ok = self.user32.ClientToScreen(hwnd, ctypes.byref(pt))
            if not ok:
                return (xc, yc)
            else:
                return (int(pt.x), int(pt.y))
    def _make_wheel_lparam(self, screen_x, screen_y):
        """``WM_MOUSEWHEEL`` lParam uses signed screen coordinates."""
        sx = int(screen_x) & 65535
        sy = int(screen_y) & 65535
        return sy << 16 | sx
    def click(self, x, y, pause=1, rand=True):
        """Performs a click with optional randomization and delay."""
        if rand:
            spread = self._click_jitter()
            x += random.randint(-spread, spread)
            y += random.randint(-spread, spread)
        self._inject_click(x, y)
        sleep_time = random.uniform(pause - pause * 0.2, pause + pause * 0.2)
        time.sleep(max(0.1, sleep_time))
    def _inject_click(self, x, y):
        hwnd = self.window_service.hwnd
        if not hwnd:
            return
        else:
            lparam = self._make_lparam(int(x), int(y))
            with self.window_service.window_dpi_context(hwnd):
                self.user32.SendMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
                self.user32.SendMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
    def click_at(self, x, y, rand=False):
        """Single mouse down/up at (x, y) with no delay (for chained clicks with custom timing)."""
        if rand:
            spread = self._click_jitter()
            x += random.randint(-spread, spread)
            y += random.randint(-spread, spread)
        self._inject_click(int(x), int(y))
    def mouse_down(self, x, y):
        hwnd = self.window_service.hwnd
        if not hwnd:
            return
        else:
            lparam = self._make_lparam(x, y)
            with self.window_service.window_dpi_context(hwnd):
                self.user32.SendMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    def mouse_up(self, x, y):
        hwnd = self.window_service.hwnd
        if not hwnd:
            return
        else:
            lparam = self._make_lparam(x, y)
            with self.window_service.window_dpi_context(hwnd):
                self.user32.SendMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
    def move(self, x, y, wparam=0):
        """WM_MOUSEMOVE. Use ``wparam=MK_LBUTTON`` only while simulating a held drag (see ``human_move``)."""
        hwnd = self.window_service.hwnd
        if not hwnd:
            return
        else:
            lparam = self._make_lparam(x, y)
            with self.window_service.window_dpi_context(hwnd):
                self.user32.SendMessageW(hwnd, WM_MOUSEMOVE, wparam, lparam)
    def human_move(self, x1, y1, x2, y2, duration=0.5):
        # irreducible cflow, using cdg fallback
        """Simulates human-like mouse movement using a Bezier curve and easing."""
        hwnd = self.window_service.hwnd
        if not hwnd:
            return
        else:
            mx = (x1 + x2) / 2
            my = (y1 + y2) / 2
            dist = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            offset = dist * random.uniform(0.02, 0.15)
            cx = mx + random.uniform(-offset, offset)
            cy = my + random.uniform(-offset, offset)
            start_time = time.perf_counter()
            while True:
                if self.stop_event and self.stop_event.is_set():
                        break
                current_time = time.perf_counter()
                elapsed = current_time - start_time
                if elapsed >= duration:
                    break
                t = elapsed / duration
                ease = -(math.cos(math.pi * t) - 1) / 2
                u = 1 - ease
                x = u ** 2 * x1 + 2 * u * ease * cx + ease ** 2 * x2
                y = u ** 2 * y1 + 2 * u * ease * cy + ease ** 2 * y2
                self.move(int(x), int(y), MK_LBUTTON)
                if self.stop_event:
                    self.stop_event.wait(0.005)
                else:
                    time.sleep(0.005)
        self.move(x2, y2, MK_LBUTTON)
    def scroll(self, x, y, amount, *, upward=False):
        hwnd = self.window_service.hwnd
        if not hwnd:
            return
        else:
            delta = int(WHEEL_DELTA if upward else -WHEEL_DELTA)
            wparam = delta << 16
            sx, sy = self._client_to_screen(x, y)
            lparam = self._make_wheel_lparam(sx, sy)
            with self.window_service.window_dpi_context(hwnd):
                for _ in range(amount):
                    self.user32.SendMessageW(hwnd, WM_MOUSEWHEEL, wparam, lparam)
                    time.sleep(random.uniform(0.05, 0.2))
