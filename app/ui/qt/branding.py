# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\ui\\qt\\branding.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

"""App logo and window icon."""
from __future__ import annotations
global _ICON
from PySide6.QtGui import QIcon, QPixmap
from app.utils.common import get_resource_path
_ICON: 'QIcon | None' = None
LOGO_PNG = 'assets/basepilot_logo.png'
LOGO_ICO = 'assets/basepilot_logo.ico'
def app_icon():
    """Return the cached application icon (ICO preferred on Windows)."""
    global _ICON
    if _ICON is None:
        ico = get_resource_path(LOGO_ICO)
        png = get_resource_path(LOGO_PNG)
        if ico.is_file():
            _ICON = QIcon(str(ico))
            return _ICON
        else:
            if png.is_file():
                _ICON = QIcon(str(png))
                return _ICON
            else:
                _ICON = QIcon()
    return _ICON
def logo_pixmap(size=48):
    """Square logo pixmap for in-app branding (sidebar header, etc.)."""
    icon = app_icon()
    if icon.isNull():
        return QPixmap()
    else:
        return icon.pixmap(size, size)
def apply_app_icon(widget):
    """Set the app logo on any QWidget with a title bar (QApplication, QMainWindow, QDialog)."""
    icon = app_icon()
    if not icon.isNull():
        widget.setWindowIcon(icon)