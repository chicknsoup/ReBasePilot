"""Reusable Qt widgets for the BasePilot UI."""
from __future__ import annotations
from collections.abc import Callable
from typing import Optional
from PySide6.QtCore import Qt, QRectF, QSize, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from app.ui.qt.theme import SPACING, TOKENS


class Card(QFrame):
    def __init__(self, parent=None, *, variant: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName('Card')
        if variant:
            self.setProperty('variant', variant)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(SPACING['md'], SPACING['md'], SPACING['md'], SPACING['md'])
        self._layout.setSpacing(SPACING['sm'])

    @property
    def card_layout(self):
        return self._layout


class SubCard(QFrame):
    """Recessed inner panel for grouping sub-options inside a Card."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('SubCard')
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(SPACING['sm'] + 2, SPACING['sm'], SPACING['sm'] + 2, SPACING['sm'])
        self._layout.setSpacing(SPACING['xs'] + 2)

    @property
    def card_layout(self):
        return self._layout


class MetricTile(QFrame):
    """Compact telemetry readout tile with optional colored accent bar."""
    def __init__(self, title: str, initial: str = '—', *, accent: Optional[str] = None, value_color: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setObjectName('MetricTile')
        if accent:
            self.setProperty('accent', accent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName('StatTitle')
        font = self.title_label.font()
        font.setLetterSpacing(font.SpacingType.PercentageSpacing, 108)
        self.title_label.setFont(font)
        self.value_label = QLabel(initial)
        self.value_label.setObjectName('StatValue')
        self.value_label.setWordWrap(True)
        if value_color:
            self.value_label.setStyleSheet(f'color: {value_color};')
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def setText(self, text: str):
        self.value_label.setText(text)

    def setStyleSheet(self, style: str):
        self.value_label.setStyleSheet(style)

    def setToolTip(self, tip: str):
        super().setToolTip(tip)
        self.value_label.setToolTip(tip)
        self.title_label.setToolTip(tip)


class SectionTitle(QLabel):
    """Instrument-panel micro-label: uppercase + letter-spaced."""
    def __init__(self, text, parent=None):
        super().__init__(text.upper(), parent)
        self.setObjectName('SectionTitle')
        self.setAutoFillBackground(False)
        font = self.font()
        font.setLetterSpacing(font.SpacingType.PercentageSpacing, 108)
        self.setFont(font)


class PageTitle(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName('PageTitle')


class PageSubtitle(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName('PageSubtitle')
        self.setWordWrap(True)


class ToggleSwitch(QCheckBox):
    def __init__(self, text='', *, parent=None, danger=False, under_development=False):
        super().__init__(text, parent)
        self.setObjectName('ToggleSwitch')
        self._danger = danger
        self._under_development = under_development
        self.setFixedHeight(24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if under_development:
            self.setChecked(False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track_w, track_h = (38, 20)
        x0 = 0
        y0 = (self.height() - track_h) // 2
        if self._under_development:
            on_color = QColor(TOKENS['border_hi'])
            off_color = QColor(TOKENS['border'])
            text_color = QColor(TOKENS['text_faint'])
        else:
            on_color = QColor(TOKENS['danger'] if self._danger else TOKENS['primary'])
            off_color = QColor(TOKENS['neutral'])
            text_color = QColor(TOKENS['danger'] if self._danger and self.isChecked() else TOKENS['text'])
        track_color = on_color if self.isChecked() and (not self._under_development) else off_color
        painter.setPen(Qt.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(x0, y0, track_w, track_h, track_h / 2, track_h / 2)
        knob_d = 16
        knob_x = x0 + track_w - knob_d - 2 if self.isChecked() and (not self._under_development) else x0 + 2
        knob_y = y0 + (track_h - knob_d) // 2
        painter.setBrush(QColor('#a0a0a8' if self._under_development else '#ffffff'))
        painter.drawEllipse(knob_x, knob_y, knob_d, knob_d)
        if self.text():
            painter.setPen(text_color)
            font = painter.font()
            font.setPointSize(10)
            painter.setFont(font)
            text_rect = QRectF(track_w + 9, 0, self.width() - track_w - 9, self.height())
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text())

    def mousePressEvent(self, event):
        if self._under_development:
            from app.ui.qt.dialogs import show_under_development
            show_under_development(self.window())
        else:
            super().mousePressEvent(event)

    def sizeHint(self):
        text_w = self.fontMetrics().horizontalAdvance(self.text()) + 8 if self.text() else 0
        return QSize(38 + text_w + 9, 24)

    def hitButton(self, pos):
        return self.rect().contains(pos)


def _styled_button(text, role, parent=None):
    btn = QPushButton(text, parent)
    btn.setProperty('role', role)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.style().unpolish(btn)
    btn.style().polish(btn)
    return btn


def primary_button(text, *, parent=None):
    return _styled_button(text, 'primary', parent)


def danger_button(text, *, parent=None):
    return _styled_button(text, 'danger', parent)


def neutral_button(text, *, parent=None):
    return _styled_button(text, 'neutral', parent)


def chip_button(text, *, parent=None, checkable=False):
    btn = _styled_button(text, 'chip', parent)
    if checkable:
        btn.setCheckable(True)
    return btn


def segment_button(text, *, parent=None, under_development=False):
    btn = _styled_button(text, 'segment', parent)
    if under_development:
        btn.setCheckable(False)
        btn.setProperty('underDevelopment', True)
    else:
        btn.setCheckable(True)
    btn.style().unpolish(btn)
    btn.style().polish(btn)
    btn.setMinimumWidth(int(btn.sizeHint().width() * 1.05))
    return btn


class StepperButton(QPushButton):
    """Compact up/down arrow for numeric steppers."""
    def __init__(self, *, up, parent=None):
        super().__init__(parent)
        self._up = up
        self.setProperty('role', 'stepper')
        self.setFixedSize(22, 13)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.style().unpolish(self)
        self.style().polish(self)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isEnabled():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(TOKENS['text']))
        cx = self.width() / 2
        cy = self.height() / 2
        if self._up:
            points = [QPointF(cx, cy - 3), QPointF(cx - 4, cy + 2), QPointF(cx + 4, cy + 2)]
        else:
            points = [QPointF(cx, cy + 3), QPointF(cx - 4, cy - 2), QPointF(cx + 4, cy - 2)]
        painter.drawPolygon(QPolygonF(points))


class HelpButton(QPushButton):
    """Compact ? button that opens a help popup or shows a tooltip when clicked."""
    def __init__(self, on_click=None, *, tooltip: str = 'What does each option do?', parent=None):
        super().__init__('?', parent)
        self.setProperty('role', 'help')
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.style().unpolish(self)
        self.style().polish(self)
        if on_click is not None:
            self.clicked.connect(on_click)


class VisibilityToggleButton(QPushButton):
    """Minimal line-art eye toggle for password fields."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty('role', 'icon')
        self.setCheckable(True)
        self.setFixedSize(34, 34)
        self.setToolTip('Show password')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.style().unpolish(self)
        self.style().polish(self)
        self.toggled.connect(lambda _: self.update())

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(TOKENS['text'])
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx = self.width() / 2
        cy = self.height() / 2
        painter.drawEllipse(QRectF(cx - 9, cy - 6, 18, 12))
        if self.isChecked():
            painter.drawLine(int(cx - 8), int(cy + 6), int(cx + 8), int(cy - 6))
        else:
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(cx - 2.5, cy - 2.5, 5, 5))
