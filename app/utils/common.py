from __future__ import annotations

import os
import sys
from pathlib import Path


def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    try:
        base_path = Path(sys._MEIPASS)
        return base_path / relative_path
    except Exception:
        base_path = Path(__file__).resolve().parent.parent.parent
        return base_path / relative_path


APP_NAME = 'BaseLooter'
_LEGACY_DATA_DIR_NAMES = ('BasePilot', 'ClashAutoLoot')
_migration_checked = False


def _legacy_data_dirs():
    dirs = []
    for name in _LEGACY_DATA_DIR_NAMES:
        if sys.platform == 'win32':
            local = os.environ.get('LOCALAPPDATA')
            if local:
                dirs.append(Path(local) / name)
                continue
        dirs.append(Path.home() / '.local' / 'share' / name)
    return dirs


def _maybe_migrate_legacy_data(new_dir):
    """One-time settings migration from pre-rebrand data dirs (BasePilot / ClashAutoLoot):
copy user-state files (settings, player list, window pin, display restore, custom templates)
— never logs or debug frames. Idempotent: skipped once the new dir holds settings."""
    global _migration_checked
    if _migration_checked:
        return
    _migration_checked = True
    if (new_dir / 'settings.json').exists():
        return None
    import shutil

    for old_dir in _legacy_data_dirs():
        if not old_dir.is_dir():
            continue
        ensure_dir(new_dir)
        for item in old_dir.glob('*.json'):
            try:
                shutil.copy2(item, new_dir / item.name)
            except OSError:
                pass
        for item in old_dir.glob('*.devmode'):
            try:
                shutil.copy2(item, new_dir / item.name)
            except OSError:
                pass
        old_tpl = old_dir / 'templates'
        if old_tpl.is_dir() and not (new_dir / 'templates').exists():
            try:
                shutil.copytree(old_tpl, new_dir / 'templates', dirs_exist_ok=True)
            except OSError:
                pass
        break


PROFILE_ENV = 'BASEPILOT_PROFILE'


def get_base_app_data_dir():
    """The main profile's data directory (Windows: LOCALAPPDATA\\BaseLooter)."""
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA')
        if local:
            path = Path(local) / APP_NAME
        else:
            path = Path.home() / '.local' / 'share' / APP_NAME
    else:
        path = Path.home() / '.local' / 'share' / APP_NAME
    _maybe_migrate_legacy_data(path)
    return path


def get_profile_name():
    """The profile this process runs under, or ``''`` for the main one."""
    raw = os.environ.get('BASELOOTER_PROFILE') or os.environ.get(PROFILE_ENV, '')
    return _sanitise_profile(raw)


def _sanitise_profile(name):
    """A profile name reduced to something safe to put in a path (``''`` if nothing is left)."""
    keep = [c for c in str(name or '').strip() if c.isalnum() or c in ' -_']
    return ''.join(keep).strip()[:40]


def get_user_app_data_dir():
    """Per-user writable data for this process — the profile's directory when one is set."""
    base = get_base_app_data_dir()
    profile = get_profile_name()
    if not profile:
        return base
    path = base / 'profiles' / profile
    _seed_profile(base, path)
    return path


_seeded_profiles = set()


def _seed_profile(base_dir, profile_dir):
    """Start a brand-new profile from the main one's settings — but never its window pin."""
    key = str(profile_dir)
    if key in _seeded_profiles:
        return
    _seeded_profiles.add(key)
    if (profile_dir / 'settings.json').exists() or not base_dir.is_dir():
        return None
    import shutil

    ensure_dir(profile_dir)
    for name in ['settings.json', 'player_list.json']:
        src = base_dir / name
        if src.is_file():
            try:
                shutil.copy2(src, profile_dir / name)
            except OSError:
                pass


def user_template_dirs():
    """Where a run looks for captured templates, nearest first."""
    dirs = []
    for d in (get_user_app_data_dir(), get_base_app_data_dir()):
        path = d / 'templates'
        if path not in dirs:
            dirs.append(path)
    return dirs


def get_log_path():
    """Path to the rotating ``baselooter.log`` in the per-user data dir."""
    ensure_dir(get_user_app_data_dir())
    return get_user_app_data_dir() / 'baselooter.log'


get_autoloot_log_path = get_log_path


def get_template_path(template_name):
    """Return ``templates/<16_10|16_9>/…`` for the active aspect."""
    from app.config import Config

    sub = Config().aspect_key
    for root in user_template_dirs():
        override = root / sub / template_name
        if override.is_file():
            return override
    return get_resource_path(f'templates/{sub}/{template_name}')


def ensure_dir(path):
    """Ensure a directory exists."""
    path.mkdir(parents=True, exist_ok=True)
