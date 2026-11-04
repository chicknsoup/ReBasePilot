# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\ui\\qt\\app.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

"""PySide6 GUI entry point."""
from __future__ import annotations
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.ui.qt.branding import apply_app_icon
from app.ui.qt.main_window import MainWindow
from app.ui.qt.theme import apply_theme
def _parse_autostart(argv):
    """``--autostart [--minutes N] [--walls] [--upgrades off|dry|maxer|rusher]\n[--donate] [--request]`` → (minutes, walls, upgrades, donate, request) or None.\n``--minutes 0`` = run until maxed (no limit)."""
    if '--autostart' not in argv:
        return
    else:
        minutes = 15
        if '--minutes' in argv:
            try:
                minutes = max(0, min(999, int(argv[argv.index('--minutes') + 1])))
            except (IndexError, ValueError):
                pass
        upgrades = 'off'
        if '--upgrades' in argv:
            try:
                candidate = argv[argv.index('--upgrades') + 1].lower()
                if candidate in ['off', 'dry', 'maxer', 'rusher']:
                    upgrades = candidate
            except IndexError:
                pass
        return (minutes, '--walls' in argv, upgrades, '--donate' in argv, '--request' in argv)
def run_gui():
    app = QApplication(sys.argv)
    app.setApplicationName('BasePilot')
    apply_app_icon(app)
    apply_theme(app)
    window = MainWindow()
    apply_app_icon(window)
    window.show()
    autostart = _parse_autostart(sys.argv[1:])
    if autostart is not None:
        minutes, walls, upgrades, donate, request = autostart
        QTimer.singleShot(3000, lambda: window.autostart_run(minutes, walls, upgrades, donate, request))
    sys.exit(app.exec())