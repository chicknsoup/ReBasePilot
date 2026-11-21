import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
from pathlib import Path
import re
import threading
import time
from typing import Dict, List, Optional, Tuple
import winreg

import cv2
import numpy as np

from app.utils.logger import setup_logger

logger = setup_logger('MuMuIPC')

MUMU_SENTINEL_BASE = 0x4D554D00
_MUMU_SENTINEL_LIMIT = MUMU_SENTINEL_BASE + 256
_MUMU_VM_PROCESSES = frozenset({
    'mumunxdevice.exe',
    'mumuvmmheadless.exe',
    'nemuheadless.exe',
    'mumuplayer.exe',
    'nemuplayer.exe',
})
_MUMU_ALL_PROCESSES = _MUMU_VM_PROCESSES | {'mumunxmain.exe'}
_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', ctypes.c_wchar * 260),
    ]


@dataclass(frozen=True)
class MuMuInstanceInfo:
    install_dir: Path
    dll_path: Path
    instance_index: int
    name: str
    width: int
    height: int


def is_mumu_sentinel_hwnd(hwnd: int) -> bool:
    return bool(hwnd) and MUMU_SENTINEL_BASE <= int(hwnd) < _MUMU_SENTINEL_LIMIT


def mumu_sentinel_hwnd(instance_index: int = 0) -> int:
    return MUMU_SENTINEL_BASE + max(0, min(255, int(instance_index)))


def mumu_index_from_sentinel(hwnd: int) -> int:
    return max(0, int(hwnd) - MUMU_SENTINEL_BASE)


def find_ipc_dll(install_dir: Path) -> Optional[Path]:
    """Locate ``external_renderer_ipc.dll`` inside a MuMuPlayer installation root."""
    if not install_dir or not install_dir.is_dir():
        return None
    for rel in (
        Path('nx_main') / 'sdk' / 'external_renderer_ipc.dll',
        Path('shell') / 'sdk' / 'external_renderer_ipc.dll',
    ):
        cand = install_dir / rel
        if cand.is_file():
            return cand
    nx_device = install_dir / 'nx_device'
    if nx_device.is_dir():
        for cand in sorted(nx_device.glob('*/shell/sdk/external_renderer_ipc.dll'), reverse=True):
            if cand.is_file():
                return cand
    return None


def _resolve_install_root_from_path(path: Path) -> Optional[Path]:
    cur = path if path.is_dir() else path.parent
    for candidate in (cur, *cur.parents):
        if find_ipc_dll(candidate) is not None:
            return candidate
    return None


def _scan_mumu_processes() -> Tuple[bool, List[Path]]:
    """Return ``(vm_process_running, discovered_exe_paths)`` via Win32 Toolhelp32."""
    vm_running = False
    exe_paths: List[Path] = []
    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return (False, [])
    try:
        pe = _PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            exe_lower = (pe.szExeFile or '').lower()
            if exe_lower in _MUMU_ALL_PROCESSES:
                if exe_lower in _MUMU_VM_PROCESSES:
                    vm_running = True
                hproc = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pe.th32ProcessID)
                if hproc:
                    try:
                        buf = ctypes.create_unicode_buffer(1024)
                        sz = wintypes.DWORD(1024)
                        if k32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(sz)) and buf.value:
                            exe_paths.append(Path(buf.value))
                    finally:
                        k32.CloseHandle(hproc)
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k32.CloseHandle(snap)
    return (vm_running, exe_paths)


def _registry_mumu_install_dirs() -> List[Path]:
    found: List[Path] = []
    subkeys = (
        r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
    )
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in subkeys:
            try:
                with winreg.OpenKey(root, sub) as key:
                    count = winreg.QueryInfoKey(key)[0]
                    for i in range(count):
                        name = winreg.EnumKey(key, i)
                        if 'mumu' not in name.lower() and 'nemu' not in name.lower():
                            continue
                        with winreg.OpenKey(key, name) as sk:
                            for val_name in ('InstallLocation', 'UninstallString', 'DisplayIcon'):
                                try:
                                    raw = str(winreg.QueryValueEx(sk, val_name)[0] or '').strip().strip('"')
                                except OSError:
                                    continue
                                if not raw:
                                    continue
                                clean = raw.split(',')[0].strip().strip('"')
                                resolved = _resolve_install_root_from_path(Path(clean))
                                if resolved is not None:
                                    found.append(resolved)
                                    break
            except OSError:
                continue
    return found


def find_mumu_install_dirs(exe_paths: Optional[List[Path]] = None) -> List[Path]:
    """Return deduplicated MuMuPlayer install roots that expose ``external_renderer_ipc.dll``."""
    roots: List[Path] = []
    seen = set()
    if exe_paths is None:
        _, exe_paths = _scan_mumu_processes()
    for p in exe_paths:
        root = _resolve_install_root_from_path(p)
        if root is not None:
            key = str(root).lower()
            if key not in seen:
                seen.add(key)
                roots.append(root)
    for root in _registry_mumu_install_dirs():
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            roots.append(root)
    return roots


def list_configured_instances(install_dir: Path) -> List[Tuple[int, str]]:
    """Return ``[(instance_index, player_name), ...]`` from ``<install_dir>/vms``."""
    vms_dir = install_dir / 'vms'
    entries: List[Tuple[int, str]] = []
    seen_indices = set()
    if vms_dir.is_dir():
        for vm_folder in sorted(vms_dir.iterdir()):
            if not vm_folder.is_dir():
                continue
            m = re.search(r'-(\d+)$', vm_folder.name)
            if not m:
                continue
            idx = int(m.group(1))
            if idx in seen_indices:
                continue
            seen_indices.add(idx)
            player_name = f'MuMu #{idx}' if idx > 0 else 'MuMu Player'
            extra_cfg = vm_folder / 'configs' / 'extra_config.json'
            if extra_cfg.is_file():
                try:
                    data = json.loads(extra_cfg.read_text(encoding='utf-8'))
                    raw_name = str(data.get('playerName', '') or '').strip()
                    if raw_name:
                        player_name = raw_name
                except (OSError, ValueError):
                    pass
            entries.append((idx, player_name))
    if not entries:
        entries.append((0, 'MuMu Player'))
    return sorted(entries, key=lambda item: item[0])


_DLL_CACHE: Dict[str, ctypes.CDLL] = {}
_DLL_LOCK = threading.Lock()


def _load_ipc_dll(dll_path: Path) -> ctypes.CDLL:
    key = str(dll_path).lower()
    with _DLL_LOCK:
        cached = _DLL_CACHE.get(key)
        if cached is not None:
            return cached
        dll = ctypes.CDLL(str(dll_path))
        dll.nemu_connect.argtypes = [ctypes.c_wchar_p, ctypes.c_int]
        dll.nemu_connect.restype = ctypes.c_int
        dll.nemu_disconnect.argtypes = [ctypes.c_int]
        dll.nemu_disconnect.restype = None
        dll.nemu_capture_display.argtypes = [
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_void_p,
        ]
        dll.nemu_capture_display.restype = ctypes.c_int
        dll.nemu_input_event_touch_down.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        dll.nemu_input_event_touch_down.restype = ctypes.c_int
        dll.nemu_input_event_touch_up.argtypes = [ctypes.c_int, ctypes.c_int]
        dll.nemu_input_event_touch_up.restype = ctypes.c_int
        dll.nemu_input_event_finger_touch_down.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        dll.nemu_input_event_finger_touch_down.restype = ctypes.c_int
        dll.nemu_input_event_finger_touch_up.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        dll.nemu_input_event_finger_touch_up.restype = ctypes.c_int
        _DLL_CACHE[key] = dll
        return dll


def discover_running_mumu_instances() -> List[MuMuInstanceInfo]:
    """Probe running MuMuPlayer Android VM instances via ``external_renderer_ipc.dll``."""
    vm_running, exe_paths = _scan_mumu_processes()
    if not vm_running:
        return []
    live: List[MuMuInstanceInfo] = []
    for install_dir in find_mumu_install_dirs(exe_paths):
        dll_path = find_ipc_dll(install_dir)
        if dll_path is None:
            continue
        try:
            dll = _load_ipc_dll(dll_path)
        except OSError as exc:
            logger.debug('Could not load MuMu IPC DLL %s: %s', dll_path, exc)
            continue
        for idx, name in list_configured_instances(install_dir):
            handle = dll.nemu_connect(str(install_dir), int(idx))
            if handle <= 0:
                continue
            try:
                w = ctypes.c_int(0)
                h = ctypes.c_int(0)
                rc = dll.nemu_capture_display(handle, 0, 0, ctypes.byref(w), ctypes.byref(h), None)
                if rc == 0 and w.value > 0 and h.value > 0:
                    live.append(
                        MuMuInstanceInfo(
                            install_dir=install_dir,
                            dll_path=dll_path,
                            instance_index=idx,
                            name=name,
                            width=int(w.value),
                            height=int(h.value),
                        )
                    )
            finally:
                dll.nemu_disconnect(handle)
    return live


class MuMuIpcSession:
    """Persistent native IPC connection to a single MuMuPlayer instance."""

    is_mumu_ipc = True

    def __init__(self, install_dir: Path, instance_index: int = 0, display_id: int = 0, dll_path: Optional[Path] = None, dll=None):
        self.install_dir = Path(install_dir)
        self.instance_index = int(instance_index)
        self.display_id = int(display_id)
        self.dll_path = Path(dll_path) if dll_path is not None else find_ipc_dll(self.install_dir)
        self._dll = dll
        self.handle = 0
        self._lock = threading.RLock()
        self._pixel_buf = None
        self._pixel_buf_len = 0
        self._last_size: Optional[Tuple[int, int]] = None

    def _get_dll(self):
        if self._dll is not None:
            return self._dll
        if self.dll_path is None or not self.dll_path.is_file():
            return None
        try:
            self._dll = _load_ipc_dll(self.dll_path)
            return self._dll
        except OSError as exc:
            logger.error('Failed to load MuMu IPC DLL %s: %s', self.dll_path, exc)
            return None

    def connect(self) -> bool:
        with self._lock:
            dll = self._get_dll()
            if dll is None:
                return False
            if self.handle > 0:
                dll.nemu_disconnect(self.handle)
                self.handle = 0
            handle = int(dll.nemu_connect(str(self.install_dir), self.instance_index))
            if handle <= 0:
                return False
            self.handle = handle
            return True

    def disconnect(self) -> None:
        with self._lock:
            if self.handle > 0 and self._dll is not None:
                try:
                    self._dll.nemu_disconnect(self.handle)
                except Exception:
                    pass
            self.handle = 0

    def ensure_connected(self) -> bool:
        with self._lock:
            if self.handle > 0:
                return True
            return self.connect()

    def get_display_size(self, *, refresh: bool = False) -> Optional[Tuple[int, int]]:
        with self._lock:
            if not refresh and self._last_size is not None and self.handle > 0:
                return self._last_size
            if not self.ensure_connected():
                return None
            dll = self._get_dll()
            if dll is None:
                return None
            w = ctypes.c_int(0)
            h = ctypes.c_int(0)
            rc = dll.nemu_capture_display(self.handle, self.display_id, 0, ctypes.byref(w), ctypes.byref(h), None)
            if rc != 0 or w.value <= 0 or h.value <= 0:
                if not self.connect():
                    return None
                rc = dll.nemu_capture_display(self.handle, self.display_id, 0, ctypes.byref(w), ctypes.byref(h), None)
                if rc != 0 or w.value <= 0 or h.value <= 0:
                    self.disconnect()
                    return None
            self._last_size = (int(w.value), int(h.value))
            return self._last_size

    def capture_bgr(self) -> Optional[np.ndarray]:
        """Capture the Android display directly from shared renderer memory (works even when minimized)."""
        with self._lock:
            sz = self.get_display_size(refresh=(self._last_size is None))
            if sz is None:
                return None
            width, height = sz
            needed = width * height * 4
            if self._pixel_buf is None or self._pixel_buf_len != needed:
                self._pixel_buf = (ctypes.c_ubyte * needed)()
                self._pixel_buf_len = needed
            dll = self._get_dll()
            if dll is None:
                return None
            w_out = ctypes.c_int(width)
            h_out = ctypes.c_int(height)
            rc = dll.nemu_capture_display(
                self.handle,
                self.display_id,
                needed,
                ctypes.byref(w_out),
                ctypes.byref(h_out),
                ctypes.byref(self._pixel_buf),
            )
            if rc != 0 and w_out.value > 0 and h_out.value > 0 and (w_out.value != width or h_out.value != height):
                width, height = int(w_out.value), int(h_out.value)
                self._last_size = (width, height)
                needed = width * height * 4
                self._pixel_buf = (ctypes.c_ubyte * needed)()
                self._pixel_buf_len = needed
                rc = dll.nemu_capture_display(
                    self.handle,
                    self.display_id,
                    needed,
                    ctypes.byref(w_out),
                    ctypes.byref(h_out),
                    ctypes.byref(self._pixel_buf),
                )
            if rc != 0:
                if not self.connect():
                    return None
                sz = self.get_display_size(refresh=True)
                if sz is None:
                    return None
                width, height = sz
                needed = width * height * 4
                if self._pixel_buf is None or self._pixel_buf_len != needed:
                    self._pixel_buf = (ctypes.c_ubyte * needed)()
                    self._pixel_buf_len = needed
                w_out = ctypes.c_int(width)
                h_out = ctypes.c_int(height)
                rc = dll.nemu_capture_display(
                    self.handle,
                    self.display_id,
                    needed,
                    ctypes.byref(w_out),
                    ctypes.byref(h_out),
                    ctypes.byref(self._pixel_buf),
                )
                if rc != 0:
                    self.disconnect()
                    return None
            self._last_size = (width, height)
            rgba = np.ctypeslib.as_array(self._pixel_buf).reshape((height, width, 4))
            flipped = np.ascontiguousarray(rgba[::-1])
            frame = cv2.cvtColor(flipped, cv2.COLOR_RGBA2BGR)
            if not np.any(frame):
                return None
            return frame

    def _clamp(self, x: int, y: int) -> Tuple[int, int]:
        sz = self._last_size or self.get_display_size()
        if not sz:
            return (max(0, int(x)), max(0, int(y)))
        w, h = sz
        return (max(0, min(w - 1, int(x))), max(0, min(h - 1, int(y))))

    def touch_down(self, x: int, y: int) -> bool:
        with self._lock:
            if not self.ensure_connected():
                return False
            xc, yc = self._clamp(x, y)
            rc = self._dll.nemu_input_event_touch_down(self.handle, self.display_id, xc, yc)
            if rc != 0 and self.connect():
                rc = self._dll.nemu_input_event_touch_down(self.handle, self.display_id, xc, yc)
            return rc == 0

    def touch_move(self, x: int, y: int) -> bool:
        return self.touch_down(x, y)

    def touch_up(self) -> bool:
        with self._lock:
            if not self.ensure_connected():
                return False
            rc = self._dll.nemu_input_event_touch_up(self.handle, self.display_id)
            return rc == 0

    def tap(self, x: int, y: int, hold_s: float = 0.035) -> bool:
        if not self.touch_down(x, y):
            return False
        if hold_s > 0:
            time.sleep(hold_s)
        return self.touch_up()

    def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        duration_s: float = 0.26,
        steps: int = 14,
        hold_end_s: float = 0.07,
        stop_event=None,
    ) -> None:
        """Single-finger swipe from ``(x1, y1)`` to ``(x2, y2)``.

        Holding briefly at ``(x2, y2)`` before ``touch_up`` zeroes Android's velocity
        tracker so list scrolling stops cleanly without uncontrolled inertial fling.
        """
        n = max(2, int(steps))
        step_sleep = max(0.005, float(duration_s) / float(n))
        if not self.touch_down(x1, y1):
            return
        try:
            for i in range(1, n + 1):
                if stop_event is not None and stop_event.is_set():
                    break
                t = i / float(n)
                xi = int(round(x1 + (x2 - x1) * t))
                yi = int(round(y1 + (y2 - y1) * t))
                self.touch_move(xi, yi)
                if stop_event is not None:
                    if stop_event.wait(step_sleep):
                        break
                else:
                    time.sleep(step_sleep)
            if hold_end_s > 0 and not (stop_event is not None and stop_event.is_set()):
                if stop_event is not None:
                    stop_event.wait(hold_end_s)
                else:
                    time.sleep(hold_end_s)
        finally:
            self.touch_up()

    def pinch_zoom_out(
        self,
        cx: Optional[int] = None,
        cy: Optional[int] = None,
        *,
        strokes: int = 3,
        stop_event=None,
    ) -> None:
        """Two-finger symmetric pinch-in across the HUD-free central band to zoom out the camera.

        Uses ``finger_id`` 1 and 2 across ``x = 0.26W..0.74W`` at ``y ≈ 0.45H`` so neither
        finger ever touches the Available Loot panel, top-right resource bars, Surrender/Next
        buttons, or bottom troop bar (any UI hit swallows that finger and aborts pinch-zoom).
        """
        _ = (cx, cy)
        sz = self._last_size or self.get_display_size()
        if not sz:
            return
        w, h = sz
        mid_x = w // 2
        mid_y = int(round(h * 0.45))
        start_dx, start_dy = int(round(w * 0.24)), int(round(h * 0.05))
        end_dx, end_dy = int(round(w * 0.085)), int(round(h * 0.02))
        steps = 15
        total_strokes = max(1, int(strokes))
        for stroke in range(total_strokes):
            if stop_event is not None and stop_event.is_set():
                return
            with self._lock:
                if not self.ensure_connected():
                    return
                dll = self._get_dll()
                if dll is None:
                    return
            try:
                for i in range(steps + 1):
                    if stop_event is not None and stop_event.is_set():
                        break
                    t = i / float(steps)
                    dx = int(round(start_dx + (end_dx - start_dx) * t))
                    dy = int(round(start_dy + (end_dy - start_dy) * t))
                    x1, y1 = self._clamp(mid_x - dx, mid_y - dy)
                    x2, y2 = self._clamp(mid_x + dx, mid_y + dy)
                    with self._lock:
                        dll.nemu_input_event_finger_touch_down(self.handle, self.display_id, 1, x1, y1)
                        dll.nemu_input_event_finger_touch_down(self.handle, self.display_id, 2, x2, y2)
                    time.sleep(0.015)
                time.sleep(0.03)
            finally:
                with self._lock:
                    if self.handle > 0 and self._dll is not None:
                        self._dll.nemu_input_event_finger_touch_up(self.handle, self.display_id, 1)
                        self._dll.nemu_input_event_finger_touch_up(self.handle, self.display_id, 2)
            pause_s = 0.08 if stroke + 1 < total_strokes else 0.20
            if stop_event is not None:
                if stop_event.wait(pause_s):
                    return
            else:
                time.sleep(pause_s)
