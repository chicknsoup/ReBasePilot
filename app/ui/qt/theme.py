"""BasePilot mission-control theme: OLED-dark instrument-panel QSS for the PySide6 UI.

Design system (OLED Tactical Telemetry + Fira/Cascadia pairing): near-black
background, slate instrument surfaces, GREEN as the "autopilot engaged" accent (red is
reserved for Stop/destructive), resource-specific accents (Gold, Elixir, Dark Elixir),
monospace tabular figures for data readouts, uppercase letter-spaced micro-labels for
panel sections. Fonts resolve at runtime with Windows fallbacks.
"""
from __future__ import annotations
from PySide6.QtGui import QColor, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

TOKENS = {
    'bg': '#020617',           # near-black canvas (OLED)
    'sidebar': '#0B1220',      # sidebar column & recessed wells
    'surface': '#0F172A',      # instrument panel / card
    'surface_hi': '#1E293B',   # raised surface, hover cards, active inputs
    'surface_raised': '#1E293B',
    'border': '#1E293B',
    'border_subtle': '#1E293B',
    'border_hi': '#334155',
    'text': '#F8FAFC',
    'text_muted': '#94A3B8',
    'text_faint': '#64748B',   # disabled / de-emphasized
    'primary': '#22C55E',      # autopilot-engaged green
    'primary_hov': '#16A34A',
    'primary_dim': '#0E3A22',  # green-tinted disabled fill
    'primary_subtle': '#0E3A22',
    'on_primary': '#04120A',   # near-black text on the green CTA
    'danger': '#EF4444',
    'danger_hov': '#DC2626',
    'success': '#22C55E',
    'warning': '#F59E0B',      # holding / idling amber
    'gold': '#FACC15',         # Clash Gold accent
    'elixir': '#E879F9',       # Clash Elixir magenta
    'dark_elixir': '#A78BFA',  # Clash Dark Elixir violet
    'info': '#38BDF8',         # Telemetry / info cyan
    'neutral': '#334155',
    'neutral_hov': '#3E4C63',
    'neutral_dark': '#1B2436',
    'neutral_dark_hov': '#28354A',
}
SPACING = {
    'xs': 4,
    'sm': 8,
    'md': 12,
    'lg': 16,
    'xl': 24,
}
RADIUS = 10
SIDEBAR_WIDTH = 176
WINDOW_DEFAULT = (1040, 680)
WINDOW_MIN = (860, 560)

_SANS_CANDIDATES = ('Fira Sans', 'Segoe UI')
_MONO_CANDIDATES = ('Fira Code', 'Cascadia Code', 'Cascadia Mono', 'Consolas')
SANS_FONT = 'Segoe UI'
MONO_FONT = 'Consolas'


def _resolve_fonts():
    global SANS_FONT, MONO_FONT
    try:
        families = set(QFontDatabase.families())
    except Exception:
        return
    for name in _SANS_CANDIDATES:
        if name in families:
            SANS_FONT = name
            break
    for name in _MONO_CANDIDATES:
        if name in families:
            MONO_FONT = name
            break


def build_stylesheet():
    t = TOKENS
    return f"""
QWidget {{
    background-color: {t['bg']};
    color: {t['text']};
    font-family: '{SANS_FONT}';
    font-size: 13px;
}}
QLabel {{
    background-color: transparent;
    padding: 0;
    margin: 0;
}}
QFrame#Card {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: {RADIUS}px;
}}
QFrame#Card[variant="status"] {{
    border: 1px solid #1F3D2B;
    border-left: 3px solid {t['primary']};
}}
QFrame#Card[variant="command"] {{
    background-color: {t['sidebar']};
    border: 1px solid {t['border_hi']};
    border-radius: {RADIUS}px;
}}
QFrame#SubCard {{
    background-color: {t['sidebar']};
    border: 1px solid {t['border']};
    border-radius: 8px;
}}
QFrame#MetricTile {{
    background-color: {t['sidebar']};
    border: 1px solid {t['border']};
    border-radius: 8px;
}}
QFrame#MetricTile[accent="gold"] {{
    border-left: 3px solid {t['gold']};
}}
QFrame#MetricTile[accent="elixir"] {{
    border-left: 3px solid {t['elixir']};
}}
QFrame#MetricTile[accent="dark"] {{
    border-left: 3px solid {t['dark_elixir']};
}}
QFrame#MetricTile[accent="primary"] {{
    border-left: 3px solid {t['primary']};
}}
QFrame#PlayerCard {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 8px;
}}
QFrame#PlayerCard[active="true"] {{
    border-left: 3px solid {t['primary']};
}}
QLabel#SectionTitle {{
    color: {t['text_muted']};
    font-weight: 600;
    font-size: 11px;
    background-color: transparent;
}}
QLabel#SectionTitle:disabled {{
    color: {t['text_faint']};
}}
QLabel#DurationUnit:disabled {{
    color: {t['text_faint']};
}}
QLabel#PageTitle {{
    font-size: 19px;
    font-weight: bold;
    color: {t['text']};
    background-color: transparent;
}}
QLabel#PageSubtitle {{
    font-size: 12px;
    color: {t['text_muted']};
    background-color: transparent;
}}
QLabel#StatValue {{
    font-family: '{MONO_FONT}';
    font-size: 14px;
    font-weight: 600;
    color: {t['text']};
    background-color: transparent;
}}
QLabel#StatTitle {{
    color: {t['text_faint']};
    font-size: 10px;
    font-weight: 600;
    background-color: transparent;
}}
QLabel#StatusBadge {{
    font-family: '{MONO_FONT}';
    font-size: 11px;
    font-weight: bold;
    padding: 4px 10px;
    border-radius: 6px;
    background-color: {t['surface_hi']};
    color: {t['text_muted']};
    border: 1px solid {t['border_hi']};
}}
QLabel#SessionTimer {{
    font-family: '{MONO_FONT}';
    font-size: 13px;
    font-weight: 600;
    color: {t['text_muted']};
    background-color: transparent;
}}
QLabel#Brand {{
    font-family: '{MONO_FONT}';
    font-size: 16px;
    font-weight: bold;
    background-color: transparent;
}}
QPushButton {{
    border: none;
    border-radius: 7px;
    padding: 5px 12px;
    min-height: 26px;
}}
QPushButton[role="primary"] {{
    background-color: {t['primary']};
    color: {t['on_primary']};
    font-weight: bold;
}}
QPushButton[role="primary"]:hover {{
    background-color: {t['primary_hov']};
}}
QPushButton[role="primary"]:disabled {{
    background-color: {t['primary_dim']};
    color: {t['text_faint']};
}}
QPushButton[role="danger"] {{
    background-color: {t['danger']};
    color: #fff;
    font-weight: bold;
}}
QPushButton[role="danger"]:hover {{
    background-color: {t['danger_hov']};
}}
QPushButton[role="danger"]:disabled {{
    background-color: #3A1212;
    color: {t['text_faint']};
}}
QPushButton[role="neutral"] {{
    background-color: {t['neutral']};
    color: {t['text']};
}}
QPushButton[role="neutral"]:hover {{
    background-color: {t['neutral_hov']};
}}
QPushButton[role="neutral"]:disabled {{
    background-color: {t['surface_hi']};
    color: {t['text_faint']};
}}
QPushButton[role="chip"] {{
    background-color: {t['neutral_dark']};
    color: {t['text_muted']};
    border: 1px solid {t['border']};
    padding: 3px 9px;
    min-height: 22px;
    border-radius: 6px;
    font-size: 12px;
}}
QPushButton[role="chip"]:hover {{
    background-color: {t['neutral_dark_hov']};
    color: {t['text']};
    border-color: {t['border_hi']};
}}
QPushButton[role="chip"]:checked, QPushButton[role="chip"][active="true"] {{
    background-color: rgba(34, 197, 94, 0.16);
    color: #4ADE80;
    border: 1px solid {t['primary']};
    font-weight: bold;
}}
QPushButton[role="chip"]:disabled {{
    background-color: {t['surface_hi']};
    color: {t['text_faint']};
}}
QPushButton[role="segment"] {{
    background-color: {t['sidebar']};
    border: 1px solid {t['border_hi']};
    color: {t['text_muted']};
    padding: 5px 10px;
}}
QPushButton[role="segment"]:hover:!checked {{
    border-color: {t['text_muted']};
    color: {t['text']};
}}
QPushButton[role="segment"]:checked {{
    background-color: rgba(34, 197, 94, 0.16);
    color: #4ADE80;
    border-color: {t['primary']};
    font-weight: bold;
}}
QPushButton[role="segment"][underDevelopment="true"] {{
    color: {t['text_faint']};
    border-color: {t['border']};
}}
QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background-color: {t['sidebar']};
    color: {t['text']};
    border: 1px solid {t['border_hi']};
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: {t['primary_hov']};
    selection-color: #ffffff;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {t['primary']};
}}
QComboBox QAbstractItemView {{
    background-color: {t['surface_hi']};
    color: {t['text']};
    border: 1px solid {t['border_hi']};
    selection-background-color: {t['neutral']};
}}
QSpinBox {{
    font-family: '{MONO_FONT}';
    font-weight: 600;
}}
QSpinBox#DurationSpin {{
    font-family: '{MONO_FONT}';
    font-size: 13px;
    font-weight: 600;
    color: {t['text']};
    background-color: {t['sidebar']};
    min-height: 28px;
    max-height: 28px;
    padding: 2px 6px;
}}
QSpinBox#DurationSpin QLineEdit {{
    background-color: transparent;
    color: {t['text']};
    border: none;
    padding: 0px;
    margin: 0px;
}}
QSpinBox#DurationSpin::up-button, QSpinBox#DurationSpin::down-button {{
    width: 0px;
    height: 0px;
    border: none;
}}
QSpinBox:disabled, QSpinBox#DurationSpin:disabled {{
    color: {t['text_faint']};
    background-color: {t['surface']};
    border-color: {t['border']};
}}
QPushButton[role="stepper"] {{
    background-color: {t['neutral_dark']};
    padding: 0;
    min-height: 13px;
    max-height: 13px;
    min-width: 22px;
    max-width: 22px;
    border-radius: 4px;
}}
QPushButton[role="stepper"]:hover {{
    background-color: {t['neutral_dark_hov']};
}}
QPushButton[role="stepper"]:pressed {{
    background-color: {t['neutral']};
}}
QPushButton[role="stepper"]:disabled {{
    background-color: {t['surface_hi']};
}}
QPushButton[role="help"] {{
    background-color: transparent;
    color: {t['text_muted']};
    padding: 0;
    min-height: 18px;
    min-width: 18px;
    max-height: 18px;
    max-width: 18px;
    border-radius: 9px;
    border: 1px solid {t['border_hi']};
    font-size: 11px;
    font-weight: bold;
}}
QPushButton[role="help"]:hover {{
    color: {t['text']};
    border-color: {t['text']};
}}
QPushButton[role="icon"] {{
    background-color: {t['neutral_dark']};
    color: {t['text']};
    padding: 0;
    min-height: 30px;
    min-width: 30px;
    max-height: 30px;
    max-width: 30px;
    border-radius: 6px;
}}
QPushButton[role="icon"]:hover {{
    background-color: {t['neutral_dark_hov']};
}}
QPushButton[role="icon"]:checked {{
    background-color: {t['neutral']};
}}
QListWidget#Sidebar {{
    background-color: {t['sidebar']};
    border: none;
    padding: 6px;
    outline: none;
}}
QListWidget#Sidebar::item {{
    padding: 10px 14px;
    margin-bottom: 2px;
    border-radius: 6px;
    border-left: 3px solid transparent;
    color: {t['text_muted']};
}}
QListWidget#Sidebar::item:selected {{
    background-color: {t['surface_hi']};
    border-left: 3px solid {t['primary']};
    color: {t['text']};
    font-weight: bold;
}}
QListWidget#Sidebar::item:hover:!selected {{
    background-color: {t['surface']};
    color: {t['text']};
}}
QStatusBar {{
    background-color: {t['sidebar']};
    border-top: 1px solid {t['border']};
    font-family: '{MONO_FONT}';
    font-size: 11px;
}}
QStatusBar::item {{
    border: none;
}}
QScrollArea {{
    border: none;
    background-color: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
}}
QScrollBar::handle:vertical {{
    background: {t['border_hi']};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
}}
QCheckBox#ToggleSwitch {{
    spacing: 8px;
    background-color: transparent;
}}
QCheckBox#ToggleSwitch::indicator {{
    width: 0;
    height: 0;
    border: none;
}}
QTabWidget::pane {{
    border: none;
    background: transparent;
}}
QTabBar::tab {{
    background: {t['sidebar']};
    color: {t['text_muted']};
    border: 1px solid {t['border']};
    border-radius: 6px;
    padding: 6px 14px;
    margin-right: 6px;
    font-weight: 600;
}}
QTabBar::tab:selected {{
    background: rgba(34, 197, 94, 0.16);
    color: #4ADE80;
    border-color: {t['primary']};
}}
QTabBar::tab:hover:!selected {{
    color: {t['text']};
    border-color: {t['border_hi']};
}}
"""


def apply_theme(app):
    app.setStyle('Fusion')
    _resolve_fonts()
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(TOKENS['bg']))
    pal.setColor(QPalette.Base, QColor(TOKENS['sidebar']))
    pal.setColor(QPalette.AlternateBase, QColor(TOKENS['surface']))
    pal.setColor(QPalette.Text, QColor(TOKENS['text']))
    pal.setColor(QPalette.WindowText, QColor(TOKENS['text']))
    pal.setColor(QPalette.Button, QColor(TOKENS['surface']))
    pal.setColor(QPalette.ButtonText, QColor(TOKENS['text']))
    pal.setColor(QPalette.ToolTipBase, QColor(TOKENS['surface_hi']))
    pal.setColor(QPalette.ToolTipText, QColor(TOKENS['text']))
    pal.setColor(QPalette.Highlight, QColor(TOKENS['primary_hov']))
    pal.setColor(QPalette.HighlightedText, QColor('#ffffff'))
    app.setPalette(pal)
    app.setStyleSheet(build_stylesheet())


STYLESHEET = build_stylesheet()
