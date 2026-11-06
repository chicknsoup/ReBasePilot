"""Players page — multi-run Supercell ID account rotation list."""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
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
    danger_button,
    primary_button,
)
from app.utils.player_list_store import PlayerEntry, load_players, save_players


class _PlayerRow(QFrame):
    """Card-styled account row with rotation order badge and Active toggle."""

    def __init__(
        self,
        entry: PlayerEntry,
        index: int,
        on_change,
        on_move_up,
        on_move_down,
        on_remove,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName('PlayerCard')
        self._on_change = on_change
        self._enabled = bool(entry.enabled)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(SPACING['md'])

        self._badge = QLabel(f'#{index + 1}')
        self._badge.setFixedWidth(32)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._badge)

        self._name = QLineEdit(entry.name)
        self._name.setPlaceholderText('In-game username (must match Supercell ID list)...')
        self._name.setMinimumWidth(240)
        self._name.editingFinished.connect(on_change)
        layout.addWidget(self._name, stretch=1)

        self._active_toggle = ToggleSwitch('Farm account', parent=self)
        self._active_toggle.setChecked(self._enabled)
        self._active_toggle.toggled.connect(self._on_toggled)
        layout.addWidget(self._active_toggle)

        up = chip_button('▲', parent=self)
        up.setToolTip('Move earlier in rotation')
        up.clicked.connect(on_move_up)
        layout.addWidget(up)

        down = chip_button('▼', parent=self)
        down.setToolTip('Move later in rotation')
        down.clicked.connect(on_move_down)
        layout.addWidget(down)

        remove = danger_button('Remove', parent=self)
        remove.clicked.connect(on_remove)
        layout.addWidget(remove)

        self._apply_active_visual()

    def set_index(self, index: int) -> None:
        self._badge.setText(f'#{index + 1}')

    def _on_toggled(self, checked: bool) -> None:
        self._enabled = bool(checked)
        self._apply_active_visual()
        self._on_change()

    def _apply_active_visual(self) -> None:
        self.setProperty('active', 'true' if self._enabled else 'false')
        self.style().unpolish(self)
        self.style().polish(self)
        if self._enabled:
            self._badge.setStyleSheet(
                f"background-color: {TOKENS['primary_subtle']}; "
                f"color: {TOKENS['primary']}; "
                f"border-radius: 6px; padding: 4px; font-weight: 700;"
            )
            self._active_toggle.setText('Active')
        else:
            self._badge.setStyleSheet(
                f"background-color: {TOKENS['surface_raised']}; "
                f"color: {TOKENS['text_faint']}; "
                f"border-radius: 6px; padding: 4px; font-weight: 600;"
            )
            self._active_toggle.setText('Skipped')

    def to_entry(self) -> Optional[PlayerEntry]:
        name = self._name.text().strip()
        if not name:
            return None
        return PlayerEntry(name=name, enabled=self._enabled)

    def is_enabled(self) -> bool:
        return self._enabled


class PlayersPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: List[_PlayerRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING['lg'], SPACING['md'], SPACING['lg'], SPACING['md'])
        outer.setSpacing(SPACING['md'])

        # ── Sticky Top Header Bar ──
        header_card = Card(variant='CommandBar')
        header_card.card_layout.setContentsMargins(14, 10, 14, 10)
        top_row = QHBoxLayout()
        top_row.setSpacing(SPACING['md'])

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(PageTitle('Accounts & Multi-Run Rotation', parent=header_card))
        title_col.addWidget(
            PageSubtitle(
                'Order determines Supercell ID switch sequence (▲ / ▼). Toggle Active to include an account in Multi-Run.',
                parent=header_card,
            )
        )
        top_row.addLayout(title_col)
        top_row.addStretch()

        self._count_badge = QLabel('0 / 0 active')
        self._count_badge.setObjectName('StatusBadge')
        self._count_badge.setProperty('state', 'idle')
        top_row.addWidget(self._count_badge)

        add_btn = primary_button('+ Add Account', parent=header_card)
        add_btn.clicked.connect(lambda: self._add_row(PlayerEntry(name='', enabled=True)))
        top_row.addWidget(add_btn)

        header_card.card_layout.addLayout(top_row)
        outer.addWidget(header_card)

        # ── Scrollable Account List ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 4, 0)
        self._list_layout.setSpacing(SPACING['sm'])

        self._empty_label = QLabel(
            'No accounts configured yet. Click "+ Add Account" above to add your Supercell ID usernames.'
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color: {TOKENS['text_muted']}; padding: 36px; font-size: 13px;"
        )
        self._list_layout.addWidget(self._empty_label)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_host)
        outer.addWidget(scroll, stretch=1)

    def showEvent(self, event):
        super().showEvent(event)
        self._reload()

    def _reload(self):
        while self._rows:
            row = self._rows.pop()
            self._list_layout.removeWidget(row)
            row.deleteLater()
        stretch_idx = self._list_layout.count() - 1
        for entry in load_players():
            self._insert_row(entry, stretch_idx)
            stretch_idx += 1
        self._refresh_indices_and_summary()

    def _insert_row(self, entry: PlayerEntry, before_stretch: int):
        row_holder: List[Optional[_PlayerRow]] = [None]

        def move_up():
            if row_holder[0] is not None:
                self._move_row(row_holder[0], -1)

        def move_down():
            if row_holder[0] is not None:
                self._move_row(row_holder[0], 1)

        def remove():
            if row_holder[0] is not None:
                self._remove_row(row_holder[0])

        row = _PlayerRow(
            entry,
            index=len(self._rows),
            on_change=self._save,
            on_move_up=move_up,
            on_move_down=move_down,
            on_remove=remove,
        )
        row_holder[0] = row
        self._rows.append(row)
        self._list_layout.insertWidget(before_stretch, row)

    def _add_row(self, entry: PlayerEntry):
        stretch_idx = max(0, self._list_layout.count() - 1)
        self._insert_row(entry, stretch_idx)
        self._refresh_indices_and_summary()
        self._save()

    def _row_index(self, row: _PlayerRow) -> int:
        return self._rows.index(row)

    def _move_row(self, row: _PlayerRow, delta: int):
        idx = self._row_index(row)
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self._rows):
            return
        self._rows[idx], self._rows[new_idx] = self._rows[new_idx], self._rows[idx]
        self._list_layout.removeWidget(row)
        # +1 because _empty_label is at index 0 in _list_layout
        self._list_layout.insertWidget(new_idx + 1, row)
        self._refresh_indices_and_summary()
        self._save()

    def _remove_row(self, row: _PlayerRow):
        if row in self._rows:
            self._rows.remove(row)
        self._list_layout.removeWidget(row)
        row.deleteLater()
        self._refresh_indices_and_summary()
        self._save()

    def _refresh_indices_and_summary(self) -> None:
        total = len(self._rows)
        active = sum(1 for r in self._rows if r.is_enabled())
        for i, r in enumerate(self._rows):
            r.set_index(i)
        self._empty_label.setVisible(total == 0)
        self._count_badge.setText(f'{active} / {total} active')
        self._count_badge.setProperty('state', 'running' if active > 0 else 'stopped')
        self._count_badge.style().unpolish(self._count_badge)
        self._count_badge.style().polish(self._count_badge)

    def _save(self):
        self._refresh_indices_and_summary()
        players = []
        for row in self._rows:
            entry = row.to_entry()
            if entry is None:
                continue
            players.append(entry)
        save_players(players)
