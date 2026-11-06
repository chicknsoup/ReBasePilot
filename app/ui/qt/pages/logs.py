"""Logs page — live syntax-highlighted log viewer with category & search filters."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import List

from PySide6.QtCore import QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.ui.qt.theme import SPACING, TOKENS
from app.ui.qt.widgets import (
    Card,
    PageSubtitle,
    PageTitle,
    ToggleSwitch,
    chip_button,
    neutral_button,
)
from app.utils.common import get_autoloot_log_path

_TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}')
_LOGGER_RE = re.compile(r'\s-\s([A-Za-z0-9_]+)\s-\s(INFO|WARNING|ERROR|CRITICAL|DEBUG)\s-\s')


class _LogSyntaxHighlighter(QSyntaxHighlighter):
    """Colors log lines by severity and domain (RaidLoot, Wall/Upgrade, Warning, Error)."""

    def __init__(self, doc: QTextDocument):
        super().__init__(doc)
        self._fmt_ts = QTextCharFormat()
        self._fmt_ts.setForeground(QColor(TOKENS['text_faint']))

        self._fmt_logger = QTextCharFormat()
        self._fmt_logger.setForeground(QColor(TOKENS['info']))
        self._fmt_logger.setFontWeight(QFont.Weight.DemiBold)

        self._fmt_error = QTextCharFormat()
        self._fmt_error.setForeground(QColor(TOKENS['danger']))
        self._fmt_error.setFontWeight(QFont.Weight.DemiBold)

        self._fmt_warn = QTextCharFormat()
        self._fmt_warn.setForeground(QColor(TOKENS['warning']))

        self._fmt_success = QTextCharFormat()
        self._fmt_success.setForeground(QColor(TOKENS['primary']))
        self._fmt_success.setFontWeight(QFont.Weight.DemiBold)

        self._fmt_gold = QTextCharFormat()
        self._fmt_gold.setForeground(QColor(TOKENS['gold']))

    def highlightBlock(self, text: str) -> None:
        if not text:
            return
        lower = text.lower()
        # Whole-line severity tinting first
        if ' - error - ' in lower or ' - critical - ' in lower or 'traceback' in lower:
            self.setFormat(0, len(text), self._fmt_error)
        elif ' - warning - ' in lower:
            self.setFormat(0, len(text), self._fmt_warn)
        elif (
            'confirmed loot' in lower
            or 'raid #' in lower
            or 'upgraded wall' in lower
            or 'starting upgrade' in lower
        ):
            self.setFormat(0, len(text), self._fmt_success)

        # Timestamp prefix always muted
        m_ts = _TS_RE.match(text)
        if m_ts:
            self.setFormat(m_ts.start(), m_ts.end() - m_ts.start(), self._fmt_ts)

        # Logger tag
        m_log = _LOGGER_RE.search(text)
        if m_log:
            self.setFormat(m_log.start(1), m_log.end(1) - m_log.start(1), self._fmt_logger)


FILTER_MODES = (
    ('all', 'All Events'),
    ('loot', 'Raids & Loot'),
    ('upgrades', 'Upgrades & Walls'),
    ('warnings', 'Warnings & Errors'),
)


class LogsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pos = 0
        self._all_lines: List[str] = []
        self._active_category = 'all'
        self._search_query = ''

        self._tail_timer = QTimer(self)
        self._tail_timer.setInterval(1000)
        self._tail_timer.timeout.connect(self._tail_log)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING['lg'], SPACING['md'], SPACING['lg'], SPACING['md'])
        layout.setSpacing(SPACING['md'])

        # ── Top Command Bar ──
        header_card = Card(variant='CommandBar')
        header_card.card_layout.setContentsMargins(14, 10, 14, 10)
        top_row = QHBoxLayout()
        top_row.setSpacing(SPACING['md'])

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(PageTitle('Activity & Diagnostic Logs', parent=header_card))
        title_col.addWidget(
            PageSubtitle('Live tail of basepilot.log with category & keyword filtering.', parent=header_card)
        )
        top_row.addLayout(title_col)
        top_row.addStretch()

        self._auto_scroll_toggle = ToggleSwitch('Auto-scroll', parent=header_card)
        self._auto_scroll_toggle.setChecked(True)
        top_row.addWidget(self._auto_scroll_toggle)

        clear_btn = neutral_button('Clear View', parent=header_card)
        clear_btn.clicked.connect(self._clear_view)
        top_row.addWidget(clear_btn)

        open_btn = neutral_button('Open in Explorer', parent=header_card)
        open_btn.clicked.connect(self._open_in_explorer)
        top_row.addWidget(open_btn)

        header_card.card_layout.addLayout(top_row)
        layout.addWidget(header_card)

        # ── Filter & Search Toolbar ──
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        self._category_chips = {}
        for key, label in FILTER_MODES:
            btn = chip_button(label, parent=self, checkable=True)
            btn.setChecked(key == 'all')
            btn.clicked.connect(lambda _checked=False, k=key: self._set_category(k))
            self._category_chips[key] = btn
            filter_row.addWidget(btn)

        filter_row.addStretch()

        self._line_count_label = QLabel('0 lines')
        self._line_count_label.setStyleSheet(f"color: {TOKENS['text_faint']}; font-size: 12px;")
        filter_row.addWidget(self._line_count_label)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText('Search logs (e.g. RaidLoot, Wall, timeout)...')
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumWidth(240)
        self._search_input.textChanged.connect(self._on_search_changed)
        filter_row.addWidget(self._search_input)

        layout.addLayout(filter_row)

        # ── Log View ──
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(5000)
        mono = QFont('Consolas', 10)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._log_view.setFont(mono)
        self._highlighter = _LogSyntaxHighlighter(self._log_view.document())
        layout.addWidget(self._log_view, stretch=1)

    def showEvent(self, event):
        super().showEvent(event)
        self._pos = 0
        self._all_lines.clear()
        self._log_view.clear()
        self._tail_log()
        self._tail_timer.start()

    def hideEvent(self, event):
        self._tail_timer.stop()
        super().hideEvent(event)

    def _set_category(self, category: str) -> None:
        self._active_category = category
        for k, btn in self._category_chips.items():
            btn.setChecked(k == category)
        self._rebuild_filtered_view()

    def _on_search_changed(self, text: str) -> None:
        self._search_query = text.strip().lower()
        self._rebuild_filtered_view()

    def _matches_filter(self, line: str) -> bool:
        lower = line.lower()
        if self._active_category == 'loot':
            if not any(
                kw in lower
                for kw in ('raidloot', 'raid #', 'battle', 'loot', 'attack', 'star bonus')
            ):
                return False
        elif self._active_category == 'upgrades':
            if not any(
                kw in lower for kw in ('wall', 'upgrade', 'builder', 'laboratory', 'lab ')
            ):
                return False
        elif self._active_category == 'warnings':
            if not any(kw in lower for kw in (' - warning - ', ' - error - ', ' - critical - ', 'traceback')):
                return False

        if self._search_query and self._search_query not in lower:
            return False
        return True

    def _rebuild_filtered_view(self) -> None:
        filtered = [ln for ln in self._all_lines if self._matches_filter(ln)]
        self._log_view.setPlainText('\n'.join(filtered))
        self._line_count_label.setText(f'{len(filtered)} / {len(self._all_lines)} lines')
        if self._auto_scroll_toggle.isChecked():
            self._log_view.moveCursor(QTextCursor.MoveOperation.End)

    def _tail_log(self):
        path = get_autoloot_log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_file():
                path.touch()
            with path.open('rb') as fh:
                fh.seek(self._pos)
                chunk = fh.read()
                self._pos = fh.tell()
            if chunk:
                text = chunk.decode('utf-8', errors='replace')
                new_lines = [ln for ln in text.splitlines() if ln.strip()]
                if not new_lines:
                    return
                self._all_lines.extend(new_lines)
                if len(self._all_lines) > 5000:
                    self._all_lines = self._all_lines[-5000:]

                matching_new = [ln for ln in new_lines if self._matches_filter(ln)]
                if matching_new:
                    block = '\n'.join(matching_new)
                    if self._log_view.document().characterCount() > 1:
                        block = '\n' + block
                    self._log_view.moveCursor(QTextCursor.MoveOperation.End)
                    self._log_view.insertPlainText(block)
                    if self._auto_scroll_toggle.isChecked():
                        self._log_view.moveCursor(QTextCursor.MoveOperation.End)
                self._line_count_label.setText(
                    f'{self._log_view.document().blockCount()} / {len(self._all_lines)} lines'
                )
        except OSError:
            return

    def _clear_view(self):
        self._all_lines.clear()
        self._log_view.clear()
        self._line_count_label.setText('0 lines')

    def _open_in_explorer(self):
        path = get_autoloot_log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_file():
                path.touch()
            if sys.platform == 'win32':
                os.startfile(path.parent)
                return
            if sys.platform == 'darwin':
                subprocess.run(['open', str(path.parent)], check=False)
                return
            subprocess.run(['xdg-open', str(path.parent)], check=False)
        except OSError:
            return
