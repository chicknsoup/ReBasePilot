# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\services\\window.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

import contextlib
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import numpy as np
import cv2
from PIL import Image
from typing import List, Optional, Tuple
from app.services.mumu_ipc import (
    MuMuIpcSession,
    discover_running_mumu_instances,
    is_mumu_sentinel_hwnd,
    mumu_index_from_sentinel,
    mumu_sentinel_hwnd,
)
from app.utils.logger import setup_logger
from app.utils.window_settings_store import WindowSelection, load_window_selection
logger = setup_logger('WindowService')
WM_LBUTTONDOWN = 513
WM_LBUTTONUP = 514
WM_MOUSEMOVE = 512
MK_LBUTTON = 1
WM_MOUSEWHEEL = 522
WHEEL_DELTA = 120
_CHILD_CLASS_PREFIX = 'CROSVM'
_MUMU_SURFACE_CLASSES = frozenset({'nemuwin', 'mumunxdevice'})
_MINIMIZED_COORD = (-30000)
_ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def _is_mumu_surface_class(cls: str) -> bool:
    low = (cls or '').lower()
    return low in _MUMU_SURFACE_CLASSES or low.startswith('mumuipc')


def _is_game_surface_class(cls: str) -> bool:
    return (cls or '').upper().startswith(_CHILD_CLASS_PREFIX) or _is_mumu_surface_class(cls)
@dataclass
class WindowCandidate:
    """A visible top-level window plus its resolved Google Play Games game surface (if any)."""
    top_hwnd: int = 0
    title: str = ''
    top_class: str = ''
    child_hwnd: int = 0
    child_class: str = ''
    @property
    def is_game(self):
        '''True when this window exposes a CROSVM game surface we can capture.'''
        return self.child_hwnd != 0
    def to_selection(self):
        return WindowSelection(title = self.title, top_class = self.top_class, child_class = self.child_class)
    def display_label(self):
        title = self.title or '(no title)'
        if self.is_game:
            return f'{title}  —  surface: {self.child_class}'
        else:
            return f'{title}  —  no game surface ({self.top_class})'
@dataclass
class DescendantInfo:
    """A single descendant window under a top-level window (for the Info diagnostics view)."""
    hwnd: int
    cls: str
    title: str
    width: int
    height: int
    depth: int
    is_surface: bool
    def display_label(self):
        indent = '    ' * self.depth
        marker = '[surface] ' if self.is_surface else ''
        size = f'''{self.width}x{self.height}''' if self.width and self.height else '—'
        title = f'''  "{self.title}"''' if self.title else ''
        return f'''{indent}{marker}{self.cls}  ({size})  hwnd={self.hwnd}{title}'''
class WindowService:
    """Handles window finding and screenshot capture using Windows API."""
    _TOP_LEVEL_CLASS_PREFIX = 'HwndWrapper'
    _minimized_logged: bool = False
    def __init__(self, window_name='Clash of Clans', child_class='CROSVM_1'):
        self.window_name = window_name
        self.child_class = child_class
        self.hwnd = 0
        self.mumu_ipc: Optional[MuMuIpcSession] = None
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32
        self._configure_capture_api()
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            self.find_window()
        except Exception:
            self.user32.SetProcessDPIAware()

    def _clear_mumu_ipc(self):
        ipc = getattr(self, 'mumu_ipc', None)
        if getattr(ipc, 'is_mumu_ipc', False) is True:
            ipc.disconnect()
        self.mumu_ipc = None

    def _bind_mumu_ipc_instance(self, inst) -> bool:
        ipc = getattr(self, 'mumu_ipc', None)
        if ipc is not None and ipc.install_dir == inst.install_dir and ipc.instance_index == inst.instance_index and ipc.ensure_connected():
            return True
        self._clear_mumu_ipc()
        session = MuMuIpcSession(inst.install_dir, inst.instance_index, dll_path=inst.dll_path)
        if session.connect():
            self.mumu_ipc = session
            return True
        return False

    def _bind_mumu_for_candidate(self, surface_hwnd: int, title: str = '', surface_class: str = '') -> None:
        if not is_mumu_sentinel_hwnd(surface_hwnd) and not _is_mumu_surface_class(surface_class):
            self._clear_mumu_ipc()
            return
        instances = discover_running_mumu_instances()
        if not instances:
            self._clear_mumu_ipc()
            return
        if is_mumu_sentinel_hwnd(surface_hwnd):
            wanted_idx = mumu_index_from_sentinel(surface_hwnd)
            for inst in instances:
                if inst.instance_index == wanted_idx:
                    self._bind_mumu_ipc_instance(inst)
                    return
        low_cls = (surface_class or '').lower()
        if low_cls.startswith('mumuipc_'):
            try:
                wanted_idx = int(low_cls.split('_', 1)[1])
                for inst in instances:
                    if inst.instance_index == wanted_idx:
                        self._bind_mumu_ipc_instance(inst)
                        return
            except ValueError:
                pass
        if len(instances) == 1:
            self._bind_mumu_ipc_instance(instances[0])
            return
        low_title = (title or '').strip().lower()
        if low_title:
            matched = [inst for inst in instances if inst.name.lower() in low_title or low_title in inst.name.lower()]
            if len(matched) == 1:
                self._bind_mumu_ipc_instance(matched[0])
                return
        self._clear_mumu_ipc()
    def _configure_capture_api(self):
        """Declare pointer-width handle types for 64-bit Windows capture calls."""
        # ***<module>.WindowService._configure_capture_api: Failure: Different bytecode
        declarations = ((self.user32.EnumWindows, [_ENUM_WINDOWS_PROC, wintypes.LPARAM], wintypes.BOOL), (self.user32.EnumChildWindows, [wintypes.HWND, _ENUM_WINDOWS_PROC, wintypes.LPARAM], wintypes.BOOL), (self.user32.IsWindowVisible, [wintypes.HWND], wintypes.BOOL), (self.user32.IsWindow, [wintypes.HWND], wintypes.BOOL), (self.user32.GetClassNameW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int), (self.user32.GetWindowTextLengthW, [wintypes.HWND], ctypes.c_int), (self.user32.GetWindowTextW, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int), (self.user32.GetWindowDC, [wintypes.HWND], wintypes.HDC), (self.user32.ReleaseDC, [wintypes.HWND, wintypes.HDC], ctypes.c_int), (self.user32.PrintWindow, [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL), (self.user32.GetWindowRect, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL), (self.user32.IsIconic, [wintypes.HWND], wintypes.BOOL), (self.user32.GetParent, [wintypes.HWND], wintypes.HWND), (self.gdi32.CreateCompatibleDC, [wintypes.HDC], wintypes.HDC), (self.gdi32.CreateCompatibleBitmap, [wintypes.HDC, ctypes.c_int, ctypes.c_int], wintypes.HANDLE), (self.gdi32.SelectObject, [wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE), (self.gdi32.DeleteObject, [wintypes.HANDLE], wintypes.BOOL), (self.gdi32.DeleteDC, [wintypes.HDC], wintypes.BOOL), (self.gdi32.GetDIBits, [wintypes.HDC, wintypes.HANDLE, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT], ctypes.c_int))
        for function, arguments, result in declarations:
            function.argtypes = arguments
            function.restype = result
        if hasattr(self.user32, 'GetWindowDpiAwarenessContext') and hasattr(self.user32, 'SetThreadDpiAwarenessContext'):
            self.user32.GetWindowDpiAwarenessContext.argtypes = [wintypes.HWND]
            self.user32.GetWindowDpiAwarenessContext.restype = wintypes.HANDLE
            self.user32.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
            self.user32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE

    @contextlib.contextmanager
    def window_dpi_context(self, hwnd=None):
        """Temporarily switch the calling thread to ``hwnd``'s DPI awareness context.

        When the game window is placed on a Virtual Display (e.g. Virtual-Display-Driver at 100% DPI)
        while the primary display uses 125%/150% DPI, Google Play Games (``CROSVM_1``, System-DPI-Aware)
        renders and processes input at System DPI while Qt6 runs in Per-Monitor-V2 DPI. Matching the
        thread's DPI context to ``hwnd`` ensures ``GetWindowRect``, ``PrintWindow``, and ``ClientToScreen``
        use the exact coordinate space of ``CROSVM_1``.
        """
        target = hwnd or self.hwnd
        prev = None
        if target and not is_mumu_sentinel_hwnd(target) and hasattr(self.user32, 'GetWindowDpiAwarenessContext') and hasattr(self.user32, 'SetThreadDpiAwarenessContext'):
            try:
                ctx = self.user32.GetWindowDpiAwarenessContext(target)
                if ctx:
                    prev = self.user32.SetThreadDpiAwarenessContext(ctx)
            except Exception:
                prev = None
        try:
            yield
        finally:
            if prev:
                try:
                    self.user32.SetThreadDpiAwarenessContext(prev)
                except Exception:
                    pass
    def find_window(self):
        """\nResolve the game-surface HWND.\n\nHonors a user-pinned selection (Settings → Game window) first; otherwise falls back to\nauto-detection (Google Play Games window with a ``CROSVM*`` child surface).\n"""
        selection = load_window_selection()
        self.hwnd = self._resolve_hwnd(selection)
        if self.hwnd:
            how = 'pinned selection' if selection.is_set() else 'auto-detect'
            logger.info(f'Window found via {how} (HWND: {self.hwnd})')
            return True
        else:
            if getattr(self, '_pin_ambiguous', False):
                return False
            else:
                if selection.is_set():
                    logger.warning(f'Pinned game window not found (title={selection.title!r}, child={selection.child_class!r}); falling back to auto-detect.')
                    self.hwnd = self._auto_detect_child()
                    if self.hwnd:
                        logger.info(f'Window found via auto-detect fallback (HWND: {self.hwnd})')
                        return True
                logger.warning(f'Window not found: {self.window_name} (expect Google Play Games CROSVM* or a running MuMuPlayer instance). Open Settings → Game window to pick it manually.')
                return False
    def _get_class(self, hwnd):
        if is_mumu_sentinel_hwnd(hwnd):
            return f'MuMuIPC_{mumu_index_from_sentinel(hwnd)}'
        buf = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buf, 256)
        return buf.value
    def _get_title(self, hwnd):
        if is_mumu_sentinel_hwnd(hwnd):
            return f'MuMuPlayer #{mumu_index_from_sentinel(hwnd)}'
        length = self.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ''
        buff = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buff, length + 1)
        return buff.value
    def _find_descendant(self, root_hwnd, predicate):
        '''Depth-first search for the first descendant whose class satisfies ``predicate``.'''
        if not root_hwnd or is_mumu_sentinel_hwnd(root_hwnd):
            return (0, '')
        found = {
            'hwnd': 0,
            'class': '' }
        def enum_child_cb(child_hwnd, lParam):
            cls = self._get_class(child_hwnd)
            if predicate(cls):
                found['hwnd'] = child_hwnd
                found['class'] = cls
                return False
            return True

        cb = _ENUM_WINDOWS_PROC(enum_child_cb)
        self.user32.EnumChildWindows(root_hwnd, cb, 0)
        return (found['hwnd'], found['class'])
    def _find_crosvm_descendant(self, root_hwnd, preferred_class = ''):
        '''Resolve a game-surface child: exact ``preferred_class`` first, then any ``CROSVM*``.'''
        if preferred_class:
            (hwnd, cls) = self._find_descendant(root_hwnd, (lambda c: c == preferred_class))
            if hwnd:
                return (hwnd, cls)
        return self._find_descendant(root_hwnd, (lambda c: c.upper().startswith(_CHILD_CLASS_PREFIX)))
    def _resolve_surface(self, top_hwnd, top_class, preferred_class = ''):
        '''Resolve the game surface for a top-level window (Google Play Games ``CROSVM*`` or MuMuPlayer ``nemuwin``).'''
        if is_mumu_sentinel_hwnd(top_hwnd):
            idx = mumu_index_from_sentinel(top_hwnd)
            return (top_hwnd, preferred_class or f'MuMuIPC_{idx}')
        (hwnd, cls) = self._find_crosvm_descendant(top_hwnd, preferred_class)
        if hwnd:
            return (hwnd, cls)
        if top_class.upper().startswith(_CHILD_CLASS_PREFIX):
            return (top_hwnd, top_class)
        (mumu_hwnd, mumu_cls) = self._find_descendant(top_hwnd, _is_mumu_surface_class)
        if mumu_hwnd:
            return (mumu_hwnd, mumu_cls)
        if _is_mumu_surface_class(top_class):
            return (top_hwnd, top_class)
        return (0, '')
    def enumerate_windows(self):
        '''List visible, titled top-level windows with their resolved game surface (if any).

Used by the Settings picker so users can choose the right window when auto-detection
fails. Candidates with a game surface (Google Play Games CROSVM or MuMuPlayer) are sorted first.
'''
        IsWindowVisible = self.user32.IsWindowVisible
        candidates = []
        def enum_top_cb(hwnd, lParam):
            if IsWindowVisible(hwnd):
                title = self._get_title(hwnd)
                if title:
                    top_class = self._get_class(hwnd)
                    (child_hwnd, child_class) = self._resolve_surface(hwnd, top_class)
                    candidates.append(WindowCandidate(top_hwnd = hwnd, title = title, top_class = top_class, child_hwnd = child_hwnd, child_class = child_class))
            return True

        cb = _ENUM_WINDOWS_PROC(enum_top_cb)
        self.user32.EnumWindows(cb, 0)
        if not any(_is_mumu_surface_class(c.child_class) for c in candidates):
            for inst in discover_running_mumu_instances():
                sentinel = mumu_sentinel_hwnd(inst.instance_index)
                candidates.append(
                    WindowCandidate(
                        top_hwnd=sentinel,
                        title=f'MuMuPlayer ({inst.name})',
                        top_class='MuMuPlayerIPC',
                        child_hwnd=sentinel,
                        child_class=f'MuMuIPC_{inst.instance_index}',
                    )
                )
        candidates.sort(key = (lambda c: (not (c.is_game), c.title.lower())))
        return candidates
    def enumerate_descendants(self, root_hwnd):
        '''All descendant windows of ``root_hwnd`` (class, title, size, depth) for diagnostics.'''
        if not root_hwnd:
            return []
        if is_mumu_sentinel_hwnd(root_hwnd):
            sz = self.window_pixel_size(root_hwnd)
            idx = mumu_index_from_sentinel(root_hwnd)
            return [
                DescendantInfo(
                    hwnd=root_hwnd,
                    cls=f'MuMuIPC_{idx}',
                    title='external_renderer_ipc',
                    width=sz[0] if sz else 0,
                    height=sz[1] if sz else 0,
                    depth=0,
                    is_surface=True,
                )
            ]
        results = []
        seen = set()
        def _depth_from_root(hwnd):
            depth = 0
            cur = self.user32.GetParent(hwnd)
            visited = {hwnd}
            while cur and cur != root_hwnd and cur not in visited:
                visited.add(cur)
                depth += 1
                cur = self.user32.GetParent(cur)
            return depth

        def cb(child_hwnd, lParam):
            if child_hwnd not in seen:
                seen.add(child_hwnd)
                cls = self._get_class(child_hwnd)
                size = self.window_pixel_size(child_hwnd)
                results.append(DescendantInfo(hwnd = child_hwnd, cls = cls, title = self._get_title(child_hwnd), width = size[0] if size else 0, height = size[1] if size else 0, depth = _depth_from_root(child_hwnd), is_surface = _is_game_surface_class(cls)))
            return True

        proc = _ENUM_WINDOWS_PROC(cb)
        self.user32.EnumChildWindows(root_hwnd, proc, 0)
        return results
    def window_pixel_size(self, hwnd):
        '''Outer pixel size of any HWND via IPC (for MuMu) or ``GetWindowRect`` (same basis as :meth:`screenshot`).'''
        if not hwnd:
            return None
        ipc = getattr(self, 'mumu_ipc', None)
        if ipc is not None and (hwnd == self.hwnd or is_mumu_sentinel_hwnd(hwnd)):
            sz = ipc.get_display_size()
            if sz is not None:
                return sz
        if is_mumu_sentinel_hwnd(hwnd):
            idx = mumu_index_from_sentinel(hwnd)
            for inst in discover_running_mumu_instances():
                if inst.instance_index == idx:
                    return (inst.width, inst.height)
            return None
        try:
            cls = self._get_class(hwnd)
            if _is_mumu_surface_class(cls):
                instances = discover_running_mumu_instances()
                if len(instances) == 1:
                    return (instances[0].width, instances[0].height)
        except Exception:
            pass

        try:
            with self.window_dpi_context(hwnd):
                rect = wintypes.RECT()
                self.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w <= 0 or h <= 0:
                return None
            return (int(w), int(h))
        except Exception:
            return None
    @staticmethod
    def _top_class_matches(wanted_class, candidate_class):
        if not wanted_class:
            return True
        if wanted_class == candidate_class:
            return True
        # WPF HwndWrapper[DefaultDomain;;<GUID>] embeds a per-process GUID that
        # changes every time Google Play Games restarts, or GPG may expose the
        # CROSVM surface directly without the wrapper.
        if wanted_class.startswith('HwndWrapper') and (candidate_class.startswith('HwndWrapper') or candidate_class.upper().startswith(_CHILD_CLASS_PREFIX)):
            return True
        if (wanted_class.startswith('Qt') or wanted_class == 'MuMuPlayerIPC') and (candidate_class.startswith('Qt') or candidate_class == 'MuMuPlayerIPC'):
            return True
        return False
    def _resolve_hwnd(self, selection):
        """Resolve the game-surface HWND from a pinned selection, or auto-detect when unset."""
        self._pin_ambiguous = False
        if not selection.is_set():
            return self._auto_detect_child()
        else:
            wanted_title = selection.title.strip().lower()
            candidates = self.enumerate_windows()
            matches = []
            for cand in candidates:
                if wanted_title and cand.title.strip().lower() != wanted_title:
                        continue
                if not self._top_class_matches(selection.top_class, cand.top_class):
                        continue
                surface_hwnd, surface_cls = self._resolve_surface(cand.top_hwnd, cand.top_class, selection.child_class)
                if surface_hwnd:
                    matches.append((surface_hwnd, cand.title, surface_cls))
            if not matches and wanted_title.startswith(self.window_name.lower()):
                # Google Play Games appends "- <AccountName>" to the Clash of Clans
                # window title and updates it when switching Supercell ID accounts.
                prefix = self.window_name.lower()
                for cand in candidates:
                    if not cand.title.strip().lower().startswith(prefix):
                        continue
                    if not self._top_class_matches(selection.top_class, cand.top_class):
                        continue
                    surface_hwnd, surface_cls = self._resolve_surface(cand.top_hwnd, cand.top_class, selection.child_class)
                    if surface_hwnd:
                        matches.append((surface_hwnd, cand.title, surface_cls))
            if not matches and (_is_mumu_surface_class(selection.child_class) or selection.top_class == 'MuMuPlayerIPC'):
                for cand in candidates:
                    if _is_mumu_surface_class(cand.child_class):
                        matches.append((cand.child_hwnd, cand.title, cand.child_class))
            if not matches:
                self._clear_mumu_ipc()
                return 0
            else:
                if len(matches) > 1:
                    self._pin_ambiguous = True
                    self._clear_mumu_ipc()
                    logger.warning('%d windows match the pinned selection %r — close the extra game window or give the clients distinct titles.', len(matches), selection.title)
                    return 0
                else:
                    chosen_hwnd, chosen_title, chosen_cls = matches[0]
                    self._bind_mumu_for_candidate(chosen_hwnd, chosen_title, chosen_cls)
                    return chosen_hwnd
    def _auto_detect_child(self):
        """Auto-detect Google Play Games (``CROSVM*``) first, then running MuMuPlayer instances."""
        name = self.window_name.lower()
        candidates = self.enumerate_windows()
        crosvm_matches = [
            cand.child_hwnd
            for cand in candidates
            if cand.is_game
            and not _is_mumu_surface_class(cand.child_class)
            and (not name or name in cand.title.lower())
        ]
        if len(crosvm_matches) > 1:
            self._clear_mumu_ipc()
            logger.warning('Multiple Clash game windows found — pick a distinct window in Settings or close the extra window.')
            return 0
        if len(crosvm_matches) == 1:
            self._clear_mumu_ipc()
            return crosvm_matches[0]

        mumu_instances = discover_running_mumu_instances()
        mumu_win_cands = [cand for cand in candidates if _is_mumu_surface_class(cand.child_class)]
        if len(mumu_instances) > 1:
            self._clear_mumu_ipc()
            logger.warning('Multiple MuMuPlayer instances found — pick the target instance in Settings → Game window.')
            return 0
        if len(mumu_instances) == 1:
            inst = mumu_instances[0]
            if self._bind_mumu_ipc_instance(inst):
                real_cands = [c for c in mumu_win_cands if not is_mumu_sentinel_hwnd(c.child_hwnd)]
                if real_cands:
                    return real_cands[0].child_hwnd
                return mumu_sentinel_hwnd(inst.instance_index)
        if len(mumu_win_cands) > 1:
            self._clear_mumu_ipc()
            logger.warning('Multiple MuMuPlayer windows found — pick the target window in Settings → Game window.')
            return 0
        if len(mumu_win_cands) == 1:
            cand = mumu_win_cands[0]
            self._bind_mumu_for_candidate(cand.child_hwnd, cand.title, cand.child_class)
            return cand.child_hwnd
        self._clear_mumu_ipc()
        return 0
    def get_outer_pixel_size(self):
        """
Outer window size in pixels (MuMu IPC display size or ``GetWindowRect``), same basis as :meth:`screenshot`.
"""
        try:
            if not self.hwnd and not self.find_window():
                return None
            ipc = getattr(self, 'mumu_ipc', None)
            if ipc is not None:
                sz = ipc.get_display_size()
                if sz is not None:
                    return sz
                if is_mumu_sentinel_hwnd(self.hwnd):
                    return None
            with self.window_dpi_context(self.hwnd):
                rect = wintypes.RECT()
                self.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w <= 0 or h <= 0:
                return None
            return (int(w), int(h))
        except Exception:
            return None
    _cloaked_logged = False

    def is_cloaked(self):
        """True when Windows DWM has cloaked the window (e.g. moved to another Win+Tab Virtual Desktop)."""
        if getattr(self, 'mumu_ipc', None) is not None:
            return False
        try:
            dwmapi = ctypes.windll.dwmapi
            DWMWA_CLOAKED = 14
            hwnd = self.hwnd
            while hwnd:
                cloaked = ctypes.c_int(0)
                if dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0:
                    if cloaked.value != 0:
                        return True
                hwnd = self.user32.GetParent(hwnd)
            return False
        except Exception:
            return False

    def is_minimized(self):
        """True when the game window is minimized (its own window or a parent).

Windows parks a minimized window at (-32000, -32000); Google Play Games nests the
game surface in a wrapper, so the surface itself can report not-iconic while its
parent is. Measured live: minimized captures come back pure black while clicks
still reach the game, which is the worst possible combination — the bot cannot
see what it is clicking.
"""
        if getattr(self, 'mumu_ipc', None) is not None:
            return False
        try:
            hwnd = self.hwnd
            while hwnd:
                if self.user32.IsIconic(hwnd):
                    return True
                rect = wintypes.RECT()
                self.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                if rect.left <= _MINIMIZED_COORD and rect.top <= _MINIMIZED_COORD:
                    return True
                hwnd = self.user32.GetParent(hwnd)
            return False
        except Exception:
            return False

    def screenshot(self):
        """Captures a screenshot of the window.

Returns None when the window is minimized or renders nothing: a black frame
matches no template and reads no text, so acting on one means clicking blind.
"""
        ipc = getattr(self, 'mumu_ipc', None)
        if ipc is None and self.hwnd and not self.user32.IsWindow(self.hwnd):
            self.hwnd = 0
        if not self.hwnd and not self.find_window():
            return None
        ipc = getattr(self, 'mumu_ipc', None)
        if ipc is not None:
            frame = ipc.capture_bgr()
            if frame is not None:
                WindowService._minimized_logged = False
                WindowService._cloaked_logged = False
                fh, fw = frame.shape[:2]
                dims = ('mumu_ipc', fw, fh)
                if getattr(WindowService, '_last_capture_dims', None) != dims:
                    WindowService._last_capture_dims = dims
                    logger.info('Capture geometry (MuMu IPC): frame=%dx%d', fw, fh)
                return frame
            if is_mumu_sentinel_hwnd(self.hwnd) or not self.user32.IsWindow(self.hwnd):
                self._clear_mumu_ipc()
                self.hwnd = 0
                return None
        if self.is_minimized():
            if not WindowService._minimized_logged:
                WindowService._minimized_logged = True
                logger.warning('Game window is minimized — capture is blank while it stays that way. Restore the Clash of Clans window (it may sit behind other windows, just not minimized). Logged once per run.')
            return None
        WindowService._minimized_logged = False
        if self.is_cloaked():
            if not WindowService._cloaked_logged:
                WindowService._cloaked_logged = True
                logger.warning('Game window is on an inactive Windows Virtual Desktop (Win+Tab) — Windows DWM cloaks off-desktop DirectX/CROSVM surfaces so frames cannot update. Keep the game window on your current desktop (it can stay behind other windows in the background).')
        else:
            WindowService._cloaked_logged = False
        hwndDC = mfcDC = hbitmap = old_bitmap = None
        capture_hwnd = self.hwnd
        try:
            pm_rect = wintypes.RECT()
            self.user32.GetWindowRect(capture_hwnd, ctypes.byref(pm_rect))
            pm_w = max(0, pm_rect.right - pm_rect.left)
            pm_h = max(0, pm_rect.bottom - pm_rect.top)
            with self.window_dpi_context(capture_hwnd):
                rect = wintypes.RECT()
                if not self.user32.GetWindowRect(capture_hwnd, ctypes.byref(rect)):
                    return None
                win_w = rect.right - rect.left
                win_h = rect.bottom - rect.top
                if win_w <= 0 or win_h <= 0:
                    return None
                width = max(win_w, pm_w)
                height = max(win_h, pm_h)
                hwndDC = self.user32.GetWindowDC(capture_hwnd)
                if not hwndDC:
                    return None
                mfcDC = self.gdi32.CreateCompatibleDC(hwndDC)
                if not mfcDC:
                    return None
                hbitmap = self.gdi32.CreateCompatibleBitmap(hwndDC, width, height)
                if not hbitmap:
                    return None
                old_bitmap = self.gdi32.SelectObject(mfcDC, hbitmap)
                if not old_bitmap or old_bitmap == ctypes.c_void_p((-1)).value:
                    old_bitmap = None
                    return None
                PW_RENDERFULLCONTENT = 2
                if not self.user32.PrintWindow(capture_hwnd, mfcDC, PW_RENDERFULLCONTENT):
                    return None
                self.gdi32.SelectObject(mfcDC, old_bitmap)
                old_bitmap = None

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ('biSize', wintypes.DWORD),
                    ('biWidth', ctypes.c_long),
                    ('biHeight', ctypes.c_long),
                    ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD),
                    ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD),
                    ('biXPelsPerMeter', ctypes.c_long),
                    ('biYPelsPerMeter', ctypes.c_long),
                    ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD),
                ]

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = width
            bmi.biHeight = -height
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0
            buf_size = width * height * 4
            buffer = (ctypes.c_byte * buf_size)()
            if self.gdi32.GetDIBits(hwndDC, hbitmap, 0, height, ctypes.byref(buffer), ctypes.byref(bmi), 0) != height:
                return None
            img = Image.frombuffer('RGBA', (width, height), bytes(buffer), 'raw', 'BGRA', 0, 1)
            frame = np.array(img)
            if frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
            else:
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            if not np.any(frame):
                return None
            if (pm_w > 0 and pm_h > 0) and (pm_w != win_w or pm_h != win_h):
                small_w = min(win_w, pm_w)
                small_h = min(win_h, pm_h)
                # Check if PrintWindow rendered at the larger extent or only filled (small_w, small_h)
                right_strip = frame[:small_h, small_w + 4:] if width > small_w + 4 else None
                bottom_strip = frame[small_h + 4:, :small_w] if height > small_h + 4 else None
                rendered_large = (right_strip is not None and np.any(right_strip)) or (bottom_strip is not None and np.any(bottom_strip))
                if rendered_large:
                    if width != win_w or height != win_h:
                        interp = cv2.INTER_AREA if win_w < width else cv2.INTER_CUBIC
                        frame = cv2.resize(frame, (win_w, win_h), interpolation=interp)
                else:
                    cropped = frame[:small_h, :small_w]
                    if small_w != win_w or small_h != win_h:
                        interp = cv2.INTER_AREA if win_w < small_w else cv2.INTER_CUBIC
                        frame = cv2.resize(cropped, (win_w, win_h), interpolation=interp)
                    else:
                        frame = cropped
            dims = (pm_w, pm_h, win_w, win_h, frame.shape[1], frame.shape[0])
            if getattr(WindowService, '_last_capture_dims', None) != dims:
                WindowService._last_capture_dims = dims
                logger.info('Capture geometry: pm=%dx%d win_dpi=%dx%d frame=%dx%d', *dims)
            return frame
        except Exception as e:
            logger.error(f'Screenshot failed: {e}')
            return None
        finally:
            if old_bitmap and mfcDC:
                self.gdi32.SelectObject(mfcDC, old_bitmap)
            if hbitmap:
                self.gdi32.DeleteObject(hbitmap)
            if mfcDC:
                self.gdi32.DeleteDC(mfcDC)
            if hwndDC:
                self.user32.ReleaseDC(capture_hwnd, hwndDC)
