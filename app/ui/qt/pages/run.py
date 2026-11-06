"""Run page — tactical 2-column mission-control dashboard."""
from __future__ import annotations
import time
from typing import Callable, List, Optional
from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from app.config import check_game_window_aspect_for_start
from app.ui.qt._constants import (
    ATTACK_STRATEGIES,
    BUILDER_BASE_ATTACK_STRATEGIES,
    BUILDER_BASE_ATTACK_STRATEGIES_UNDER_DEV,
    BUILDER_BASE_PRIORITISE_LABELS,
)
from app.ui.qt.bot_controller import BotController
from app.ui.qt.dialogs import RankedAttackConfirmDialog, show_bb_prioritise_help, show_error, show_under_development
from app.ui.qt.theme import MONO_FONT, SPACING, TOKENS
from app.ui.qt.widgets import (
    Card,
    HelpButton,
    MetricTile,
    SectionTitle,
    StepperButton,
    SubCard,
    ToggleSwitch,
    chip_button,
    danger_button,
    neutral_button,
    primary_button,
    segment_button,
)
from app.utils.common import get_autoloot_log_path
from app.utils.player_list_store import PlayerEntry, load_players
from app.utils.profile_settings_store import (
    RESERVE_BUILDERS_MAX,
    WALL_UPGRADE_THRESHOLD_M_MAX,
    load_profile_settings,
    save_profile_settings,
)


class RunPage(QWidget):
    _STRATEGY_LABELS = ['Edrags', 'Valkyries', 'Sneaky Goblins', 'Super Minions']
    _VILLAGE_LABELS = ['Home Village', 'Builder Base']
    _AUTO_UPGRADE_LABELS = [('Off', 'off'), ('Dry Run', 'dry'), ('Maxer', 'maxer'), ('Rusher', 'rusher')]
    _PRESET_MINUTES = [(15, '15m'), (30, '30m'), (60, '1h'), (120, '2h'), (240, '4h'), (999, 'Max')]

    _STATE_COLORS = {
        'farming': '#4ADE80',
        'running': '#4ADE80',
        'idling': '#F59E0B',
        'stopped': '#94A3B8',
        'error': '#EF4444',
    }

    def __init__(self, controller: BotController, navigate_to: Callable[[str], None], parent=None):
        super().__init__(parent)
        self._controller = controller
        self._navigate_to = navigate_to
        self._settings = QSettings('BaseLooter', 'UI')
        self._preset_buttons: list[tuple[int, object]] = []
        self._session_start_mono: Optional[float] = None
        self._log_tail_pos = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING['lg'], SPACING['md'], SPACING['lg'], SPACING['md'])
        outer.setSpacing(SPACING['md'])

        # 1. Sticky Top Command Bar (Village Switcher + Live State Badge + Session Clock + Start/Stop)
        outer.addWidget(self._build_command_bar())

        # 2. Main 2-Column Scrollable Workspace
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        columns = QHBoxLayout(content)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(SPACING['md'])

        # Left Column: Configuration (Attack + Schedule + Automation Modes)
        left_col = QVBoxLayout()
        left_col.setSpacing(SPACING['md'])
        self._attack_card = self._build_attack_card()
        left_col.addWidget(self._attack_card)
        self._bb_attack_card = self._build_bb_attack_card()
        left_col.addWidget(self._bb_attack_card)
        left_col.addWidget(self._build_schedule_card())
        self._modes_card = self._build_modes_card()
        left_col.addWidget(self._modes_card)
        self._bb_modes_card = self._build_bb_modes_card()
        left_col.addWidget(self._bb_modes_card)
        left_col.addStretch()

        # Right Column: Live Telemetry + Activity Feed
        right_col = QVBoxLayout()
        right_col.setSpacing(SPACING['md'])
        self._status_card = self._build_status_card()
        right_col.addWidget(self._status_card)
        self._feed_card = self._build_activity_feed_card()
        right_col.addWidget(self._feed_card, stretch=1)

        columns.addLayout(left_col, stretch=11)
        columns.addLayout(right_col, stretch=10)
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        # Timers for session clock and mini activity feed
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_session_clock)
        self._clock_timer.start()

        self._apply_village_ui()
        self._restore_choices()
        self._load_inline_profile_knobs()

        self._controller.botStarted.connect(self._on_bot_started)
        self._controller.botFinished.connect(self._on_bot_finished_ui)
        self._controller.runningChanged.connect(self._on_running_changed)
        self._controller.stateChanged.connect(self._on_state_changed)
        self._controller.lootChanged.connect(self._on_loot_changed)
        self._controller.statusChanged.connect(self._on_status_line)

    # ------------------------------------------------------------------
    # Top Command Bar
    # ------------------------------------------------------------------
    def _build_command_bar(self):
        card = Card(variant='command')
        card.card_layout.setContentsMargins(SPACING['md'], SPACING['sm'] + 2, SPACING['md'], SPACING['sm'] + 2)
        row = QHBoxLayout()
        row.setSpacing(SPACING['sm'])

        self._village_group = QButtonGroup(self)
        self._village_group.setExclusive(True)
        for i, label in enumerate(self._VILLAGE_LABELS):
            btn = segment_button(label, parent=card)
            if label == 'Home Village':
                btn.setChecked(True)
            self._village_group.addButton(btn, i)
            row.addWidget(btn)
        self._village_group.idClicked.connect(self._on_village_changed)

        row.addStretch()

        self._state_badge = QLabel('● STOPPED')
        self._state_badge.setObjectName('StatusBadge')
        row.addWidget(self._state_badge)

        self._session_timer_lbl = QLabel('00:00:00')
        self._session_timer_lbl.setObjectName('SessionTimer')
        self._session_timer_lbl.setToolTip('Elapsed session time')
        row.addWidget(self._session_timer_lbl)

        row.addSpacing(SPACING['sm'])

        self._btn_start = primary_button('▶  Start Autopilot', parent=card)
        self._btn_start.setMinimumSize(142, 34)
        self._btn_start.clicked.connect(self.start_bot)
        row.addWidget(self._btn_start)

        self._btn_stop = danger_button('■  Stop', parent=card)
        self._btn_stop.setMinimumSize(88, 34)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._controller.stop)
        row.addWidget(self._btn_stop)

        card.card_layout.addLayout(row)
        return card

    def _set_badge_state(self, label: str, kind: str = 'stopped'):
        color = self._STATE_COLORS.get(kind, TOKENS['text_muted'])
        bg_map = {
            'farming': 'rgba(34, 197, 94, 0.14)',
            'running': 'rgba(34, 197, 94, 0.14)',
            'idling': 'rgba(245, 158, 11, 0.16)',
            'error': 'rgba(239, 68, 68, 0.16)',
            'stopped': TOKENS['surface_hi'],
        }
        border_map = {
            'farming': TOKENS['primary'],
            'running': TOKENS['primary'],
            'idling': TOKENS['warning'],
            'error': TOKENS['danger'],
            'stopped': TOKENS['border_hi'],
        }
        bg = bg_map.get(kind, TOKENS['surface_hi'])
        border = border_map.get(kind, TOKENS['border_hi'])
        self._state_badge.setText(f'● {label.upper()}')
        self._state_badge.setStyleSheet(
            f'color: {color}; background-color: {bg}; border: 1px solid {border}; border-radius: 6px; padding: 4px 10px;'
        )
        if hasattr(self, '_stat_state'):
            self._stat_state.setText(label.upper())
            self._stat_state.setStyleSheet(f'color: {color};')

    def _tick_session_clock(self):
        if self._controller.is_running() and self._session_start_mono is not None:
            elapsed = max(0, int(time.monotonic() - self._session_start_mono))
            hrs, rem = divmod(elapsed, 3600)
            mins, secs = divmod(rem, 60)
            self._session_timer_lbl.setText(f'{hrs:02d}:{mins:02d}:{secs:02d}')
            self._session_timer_lbl.setStyleSheet(f"color: {TOKENS['primary']};")
        elif not self._controller.is_running():
            self._session_timer_lbl.setStyleSheet(f"color: {TOKENS['text_muted']};")
        self._poll_activity_feed()

    # ------------------------------------------------------------------
    # Left Column Cards
    # ------------------------------------------------------------------
    def _is_builder_base(self):
        btn = self._village_group.checkedButton()
        return btn is not None and btn.text() == 'Builder Base'

    def _on_village_changed(self, _button_id):
        self._apply_village_ui()

    def _apply_village_ui(self):
        builder_base = self._is_builder_base()
        self._attack_card.setVisible(not builder_base)
        self._modes_card.setVisible(not builder_base)
        self._bb_attack_card.setVisible(builder_base)
        self._bb_modes_card.setVisible(builder_base)
        self._until_maxed.setVisible(not builder_base)
        if builder_base and self._until_maxed.isChecked():
            self._until_maxed.setChecked(False)

    def _build_attack_card(self):
        card = Card()
        header = QHBoxLayout()
        header.addWidget(SectionTitle('Attack Strategy'))
        header.addStretch()
        cfg_link = chip_button('Combat & Loot Filter ⚙', parent=card)
        cfg_link.setToolTip('Configure Earthquake placement and minimum enemy loot filter')
        cfg_link.clicked.connect(lambda: self._navigate_to('settings'))
        header.addWidget(cfg_link)
        card.card_layout.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACING['sm'])
        grid.setVerticalSpacing(SPACING['sm'])
        self._strategy_group = QButtonGroup(self)
        self._strategy_group.setExclusive(True)
        for i, label in enumerate(self._STRATEGY_LABELS):
            btn = segment_button(label, parent=card)
            if label == 'Edrags':
                btn.setChecked(True)
            self._strategy_group.addButton(btn, i)
            grid.addWidget(btn, i // 2, i % 2)
        card.card_layout.addLayout(grid)
        return card

    def _build_bb_attack_card(self):
        card = Card()
        card.card_layout.addWidget(SectionTitle('Builder Base Strategy'))
        row = QHBoxLayout()
        self._bb_strategy_group = QButtonGroup(self)
        self._bb_strategy_group.setExclusive(True)
        for i, label in enumerate(BUILDER_BASE_ATTACK_STRATEGIES):
            btn = segment_button(label, parent=card)
            if label == 'Baby Dragon':
                btn.setChecked(True)
            self._bb_strategy_group.addButton(btn, i)
            row.addWidget(btn)
        for label in BUILDER_BASE_ATTACK_STRATEGIES_UNDER_DEV:
            btn = segment_button(label, parent=card, under_development=True)
            btn.clicked.connect(lambda _checked=False: show_under_development(self.window()))
            row.addWidget(btn)
        row.addStretch()
        card.card_layout.addLayout(row)
        return card

    def _build_schedule_card(self):
        card = Card()
        header = QHBoxLayout()
        header.addWidget(SectionTitle('Run Schedule & Duration'))
        header.addStretch()
        help_btn = HelpButton(
            tooltip=(
                '• Timer: Stops automatically after N minutes (1–999).\n'
                '• Run until maxed (∞): Farms, upgrades, and idles in a loop until you press Stop.\n'
                '• Star Bonus: Runs just long enough to claim the daily Star Bonus.'
            ),
            parent=card,
        )
        header.addWidget(help_btn)
        card.card_layout.addLayout(header)

        # Duration row + presets
        dur_row = QHBoxLayout()
        dur_row.setSpacing(SPACING['sm'])
        self._duration_label = QLabel('Timer:')
        self._duration_label.setStyleSheet(f"color: {TOKENS['text_muted']}; font-weight: 600;")
        dur_row.addWidget(self._duration_label)

        self._minutes_spin = QSpinBox()
        self._minutes_spin.setObjectName('DurationSpin')
        self._minutes_spin.setRange(0, 999)
        self._minutes_spin.setSpecialValueText('∞')
        self._minutes_spin.setValue(15)
        self._minutes_spin.setMinimumWidth(74)
        self._minutes_spin.setFixedHeight(28)
        self._minutes_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._minutes_spin.setKeyboardTracking(False)
        self._minutes_editor = self._minutes_spin.lineEdit()
        if self._minutes_editor is not None:
            self._minutes_editor.setReadOnly(False)
        self._minutes_spin.installEventFilter(self)
        if self._minutes_editor is not None:
            self._minutes_editor.installEventFilter(self)
        self._minutes_spin.valueChanged.connect(self._on_minutes_spin_changed)

        step_col = QVBoxLayout()
        step_col.setSpacing(2)
        step_col.setContentsMargins(0, 0, 0, 0)
        self._btn_spin_up = StepperButton(up=True, parent=card)
        self._btn_spin_up.clicked.connect(lambda: self._step_minutes(5))
        self._btn_spin_down = StepperButton(up=False, parent=card)
        self._btn_spin_down.clicked.connect(lambda: self._step_minutes(-5))
        step_col.addWidget(self._btn_spin_up)
        step_col.addWidget(self._btn_spin_down)

        self._minutes_unit = QLabel('min')
        self._minutes_unit.setObjectName('DurationUnit')
        self._minutes_unit.setStyleSheet(f"color: {TOKENS['text_muted']};")

        dur_row.addWidget(self._minutes_spin)
        dur_row.addLayout(step_col)
        dur_row.addWidget(self._minutes_unit)
        dur_row.addSpacing(SPACING['xs'])

        for mins, text in self._PRESET_MINUTES:
            btn = chip_button(text, parent=card)
            btn.clicked.connect(lambda _checked=False, m=mins: self._set_minutes(m))
            dur_row.addWidget(btn)
            self._preset_buttons.append((mins, btn))
        dur_row.addStretch()
        card.card_layout.addLayout(dur_row)

        # Toggle row: Star Bonus & Run until maxed side-by-side
        toggles_row = QHBoxLayout()
        toggles_row.setSpacing(SPACING['lg'])
        self._until_maxed = ToggleSwitch('Run until maxed (∞)', parent=card)
        self._until_maxed.setToolTip('No time limit. Pauses and rechecks every 5m when storages are full and no builder is free.')
        self._until_maxed.toggled.connect(self._on_until_maxed_toggle)
        toggles_row.addWidget(self._until_maxed)

        self._star_bonus = ToggleSwitch('Star Bonus only', parent=card)
        self._star_bonus.setToolTip('Stop automatically once the daily Star Bonus is claimed.')
        self._star_bonus.toggled.connect(self._on_star_bonus_toggle)
        toggles_row.addWidget(self._star_bonus)
        toggles_row.addStretch()
        card.card_layout.addLayout(toggles_row)

        self._on_star_bonus_toggle(self._star_bonus.isChecked())
        self._sync_preset_highlight()
        return card

    def _build_modes_card(self):
        card = Card()
        card.card_layout.addWidget(SectionTitle('Automation & Upgrades'))

        # SubCard 1: Auto Upgrade + Reserve Builders inline
        upg_box = SubCard(parent=card)
        upg_top = QHBoxLayout()
        upg_top.addWidget(SectionTitle('Auto Upgrade'))
        upg_top.addStretch()
        res_lbl = QLabel('Keep free:')
        res_lbl.setStyleSheet(f"color: {TOKENS['text_muted']}; font-size: 12px;")
        upg_top.addWidget(res_lbl)
        self._reserve_builders_spin = QSpinBox()
        self._reserve_builders_spin.setRange(0, RESERVE_BUILDERS_MAX)
        self._reserve_builders_spin.setSuffix(' bld')
        self._reserve_builders_spin.setFixedWidth(74)
        self._reserve_builders_spin.setToolTip('Builders kept free for wall upgrades (set 0 if walls are maxed)')
        self._reserve_builders_spin.valueChanged.connect(self._save_inline_profile_knobs)
        upg_top.addWidget(self._reserve_builders_spin)
        upg_box.card_layout.addLayout(upg_top)

        upg_seg_row = QHBoxLayout()
        upg_seg_row.setSpacing(SPACING['xs'])
        self._auto_upgrade_group = QButtonGroup(self)
        self._auto_upgrade_group.setExclusive(True)
        for i, (label, _key) in enumerate(self._AUTO_UPGRADE_LABELS):
            btn = segment_button(label, parent=upg_box)
            if label == 'Off':
                btn.setChecked(True)
            self._auto_upgrade_group.addButton(btn, i)
            upg_seg_row.addWidget(btn)
        upg_box.card_layout.addLayout(upg_seg_row)
        card.card_layout.addWidget(upg_box)

        # SubCard 2: Wall Upgrades + Threshold inline
        wall_box = SubCard(parent=card)
        wall_row = QHBoxLayout()
        self._upgrade_walls = ToggleSwitch('Upgrade walls (1 by 1)', parent=wall_box)
        self._upgrade_walls.setToolTip('Upgrades walls one by one between raids using whichever resource has the higher balance.')
        self._upgrade_walls.toggled.connect(self._on_wall_toggle_changed)
        wall_row.addWidget(self._upgrade_walls)
        wall_row.addStretch()
        self._wall_thresh_lbl = QLabel('Min loot:')
        self._wall_thresh_lbl.setStyleSheet(f"color: {TOKENS['text_muted']}; font-size: 12px;")
        wall_row.addWidget(self._wall_thresh_lbl)
        self._wall_threshold_spin = QSpinBox()
        self._wall_threshold_spin.setRange(0, WALL_UPGRADE_THRESHOLD_M_MAX)
        self._wall_threshold_spin.setSuffix('M')
        self._wall_threshold_spin.setSpecialValueText('Full')
        self._wall_threshold_spin.setFixedWidth(74)
        self._wall_threshold_spin.setToolTip('Only run a wall upgrade pass when Gold or Elixir reaches this amount (0 = only when storage is full)')
        self._wall_threshold_spin.valueChanged.connect(self._save_inline_profile_knobs)
        wall_row.addWidget(self._wall_threshold_spin)
        wall_box.card_layout.addLayout(wall_row)
        card.card_layout.addWidget(wall_box)

        # SubCard 3: Clan Assist, Ranked Fill & Multi-run
        extra_box = SubCard(parent=card)
        clan_row = QHBoxLayout()
        self._auto_donate = ToggleSwitch('Auto donate', parent=extra_box)
        clan_row.addWidget(self._auto_donate)
        self._auto_request = ToggleSwitch('Request troops', parent=extra_box)
        clan_row.addWidget(self._auto_request)
        clan_row.addStretch()
        clan_cfg = chip_button('Clan setup ⚙', parent=extra_box)
        clan_cfg.clicked.connect(lambda: self._navigate_to('settings'))
        clan_row.addWidget(clan_cfg)
        extra_box.card_layout.addLayout(clan_row)

        mr_row = QHBoxLayout()
        self._multi_run = ToggleSwitch('Multi-account rotation', parent=extra_box)
        mr_row.addWidget(self._multi_run)
        mr_row.addStretch()
        edit_players = chip_button('Accounts…', parent=extra_box)
        edit_players.clicked.connect(lambda: self._navigate_to('players'))
        mr_row.addWidget(edit_players)
        extra_box.card_layout.addLayout(mr_row)

        rk_row = QHBoxLayout()
        self._ranked = ToggleSwitch('Ranked battle fill (uses daily tournament attacks)', parent=extra_box, danger=True)
        rk_row.addWidget(self._ranked)
        rk_row.addStretch()
        extra_box.card_layout.addLayout(rk_row)

        card.card_layout.addWidget(extra_box)
        return card

    def _build_bb_modes_card(self):
        card = Card()
        card.card_layout.addWidget(SectionTitle('Builder Base Modes'))
        prior_row = QHBoxLayout()
        prior_row.addWidget(SectionTitle('Prioritise'))
        prior_help = HelpButton(lambda: show_bb_prioritise_help(self.window()), parent=card)
        prior_row.addWidget(prior_help, alignment=Qt.AlignmentFlag.AlignVCenter)
        prior_row.addSpacing(SPACING['xs'])
        self._bb_prioritise_group = QButtonGroup(self)
        self._bb_prioritise_group.setExclusive(True)
        for i, label in enumerate(BUILDER_BASE_PRIORITISE_LABELS):
            btn = segment_button(label, parent=card)
            if label == 'Both':
                btn.setChecked(True)
            self._bb_prioritise_group.addButton(btn, i)
            prior_row.addWidget(btn)
        prior_row.addStretch()
        card.card_layout.addLayout(prior_row)
        self._bb_upgrade_walls = ToggleSwitch('Upgrade walls', parent=card, under_development=True)
        card.card_layout.addWidget(self._bb_upgrade_walls)
        mr_row = QHBoxLayout()
        self._bb_multi_run = ToggleSwitch('Multi-run', parent=card, under_development=True)
        mr_row.addWidget(self._bb_multi_run)
        mr_row.addStretch()
        edit_players = chip_button('Accounts…', parent=card)
        edit_players.clicked.connect(lambda: show_under_development(self.window()))
        mr_row.addWidget(edit_players)
        card.card_layout.addLayout(mr_row)
        return card

    # ------------------------------------------------------------------
    # Right Column Cards: Telemetry & Mini Live Log
    # ------------------------------------------------------------------
    def _build_status_card(self):
        card = Card(variant='status')
        header = QHBoxLayout()
        header.addWidget(SectionTitle('Live Session Telemetry'))
        header.addStretch()
        self._stat_raid_results = QLabel('0 raids')
        self._stat_raid_results.setStyleSheet(f"color: {TOKENS['text_muted']}; font-family: '{MONO_FONT}'; font-size: 11px;")
        header.addWidget(self._stat_raid_results)
        card.card_layout.addLayout(header)

        # Row 1: Resource Loot Cards (Gold, Elixir, Dark Elixir)
        loot_grid = QGridLayout()
        loot_grid.setHorizontalSpacing(SPACING['sm'])
        loot_grid.setVerticalSpacing(SPACING['sm'])

        self._tile_gold = MetricTile('Gold Looted', '0', accent='gold', value_color=TOKENS['gold'], parent=card)
        self._tile_elixir = MetricTile('Elixir Looted', '0', accent='elixir', value_color=TOKENS['elixir'], parent=card)
        self._tile_dark = MetricTile('Dark Looted', '0', accent='dark', value_color=TOKENS['dark_elixir'], parent=card)
        loot_grid.addWidget(self._tile_gold, 0, 0)
        loot_grid.addWidget(self._tile_elixir, 0, 1)
        loot_grid.addWidget(self._tile_dark, 0, 2)
        for col in range(3):
            loot_grid.setColumnStretch(col, 1)
        card.card_layout.addLayout(loot_grid)

        # Row 2: Operational Metrics
        ops_grid = QGridLayout()
        ops_grid.setHorizontalSpacing(SPACING['sm'])
        ops_grid.setVerticalSpacing(SPACING['sm'])

        self._stat_state = MetricTile('Bot State', 'STOPPED', accent='primary', parent=card)
        self._stat_builders = MetricTile('Builders Free', '—', parent=card)
        self._stat_lab = MetricTile('Lab Status', '—', parent=card)
        self._stat_skipped = MetricTile('Bases Skipped', '0', parent=card)
        self._stat_storages = MetricTile('Current Storages (G / E / DE)', '—', parent=card)
        self._stat_loot_rate = MetricTile('Hourly Rate (G / E / DE)', '— (needs 10m)', parent=card)

        ops_grid.addWidget(self._stat_state, 0, 0)
        ops_grid.addWidget(self._stat_builders, 0, 1)
        ops_grid.addWidget(self._stat_lab, 0, 2)
        ops_grid.addWidget(self._stat_skipped, 0, 3)
        ops_grid.addWidget(self._stat_storages, 1, 0, 1, 2)
        ops_grid.addWidget(self._stat_loot_rate, 1, 2, 1, 2)
        for col in range(4):
            ops_grid.setColumnStretch(col, 1)
        card.card_layout.addLayout(ops_grid)

        # Keep hidden compat label for any external callers expecting _stat_loot
        self._stat_loot = QLabel('')
        self._stat_loot.setVisible(False)

        self._stat_note = QLabel('Ready — configure your strategy on the left and press Start Autopilot.')
        self._stat_note.setWordWrap(True)
        self._stat_note.setStyleSheet(f"color: {TOKENS['text_muted']}; font-size: 12px;")
        card.card_layout.addWidget(self._stat_note)
        return card

    def _build_activity_feed_card(self):
        card = Card()
        header = QHBoxLayout()
        header.addWidget(SectionTitle('Recent Activity Feed'))
        header.addStretch()
        full_logs_btn = chip_button('Full Logs →', parent=card)
        full_logs_btn.clicked.connect(lambda: self._navigate_to('logs'))
        header.addWidget(full_logs_btn)
        card.card_layout.addLayout(header)

        self._mini_log = QPlainTextEdit()
        self._mini_log.setReadOnly(True)
        self._mini_log.setMaximumBlockCount(150)
        self._mini_log.setMinimumHeight(130)
        mono = QFont(MONO_FONT, 9)
        self._mini_log.setFont(mono)
        self._mini_log.setStyleSheet(
            f"background-color: {TOKENS['sidebar']}; border: 1px solid {TOKENS['border']}; border-radius: 6px; padding: 6px;"
        )
        card.card_layout.addWidget(self._mini_log, stretch=1)
        return card

    def _poll_activity_feed(self):
        path = get_autoloot_log_path()
        try:
            if not path.is_file():
                return
            size = path.stat().st_size
            if self._log_tail_pos == 0 and size > 6144:
                self._log_tail_pos = size - 6144
            elif size < self._log_tail_pos:
                self._log_tail_pos = 0
            if size == self._log_tail_pos:
                return
            with path.open('rb') as fh:
                fh.seek(self._log_tail_pos)
                chunk = fh.read()
                self._log_tail_pos = fh.tell()
            if not chunk:
                return
            text = chunk.decode('utf-8', errors='replace')
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            # Filter out noisy traceback continuation lines in the compact feed
            important = []
            for ln in lines:
                if ' - ' in ln:
                    parts = ln.split(' - ', 3)
                    if len(parts) == 4:
                        ts = parts[0].split(' ')[-1]
                        lvl = parts[2].strip()
                        msg = parts[3].strip()
                        prefix = '⚠ ' if lvl in ('WARNING', 'ERROR') else ('✓ ' if 'confirmed' in msg else '• ')
                        important.append(f'[{ts}] {prefix}{msg}')
            if important:
                for item in important[-20:]:
                    self._mini_log.appendPlainText(item)
                sb = self._mini_log.verticalScrollBar()
                if sb is not None:
                    sb.setValue(sb.maximum())
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Telemetry & Formatting
    # ------------------------------------------------------------------
    @staticmethod
    def _fmt_amount(value):
        value = int(value)
        if value >= 1000000:
            return f'{value / 1000000:.2f}M'
        if value >= 10000:
            return f'{value / 1000:.0f}k'
        return f'{value:,}'

    def _on_state_changed(self, payload):
        state = payload.get('state')
        if state:
            s_low = str(state).lower()
            self._set_badge_state(str(state), s_low if s_low in self._STATE_COLORS else 'running')
        builders = payload.get('builders')
        if builders:
            self._stat_builders.setText(f'{builders[0]} / {builders[1]}')
        lab = payload.get('lab')
        if lab:
            self._stat_lab.setText(f'{lab[0]} / {lab[1]}')
        storages = payload.get('storages')
        if storages:
            self._stat_storages.setText(str(storages))
        skipped = payload.get('skipped')
        if skipped is not None:
            self._stat_skipped.setText(str(skipped))
        if payload.get('raid_loot') is not None:
            self._raid_loot_state = payload['raid_loot']
            self._render_raid_loot()
        note = payload.get('note')
        if note:
            self._stat_note.setText(f'Upgrades: {note}')

    def _on_loot_changed(self, gold, elixir, dark, elapsed):
        self._raid_totals = (int(gold), int(elixir), int(dark))
        self._raid_elapsed = float(elapsed)
        self._render_raid_loot()

    def _render_raid_loot(self):
        state = getattr(self, '_raid_loot_state', {})
        totals = getattr(self, '_raid_totals', (0, 0, 0))
        elapsed = getattr(self, '_raid_elapsed', 0)
        if state.get('supported') is False:
            self._tile_gold.setText('BB Mode')
            self._tile_elixir.setText('BB Mode')
            self._tile_dark.setText('—')
            self._stat_loot_rate.setText('—')
            self._stat_raid_results.setText('Builder Base')
            return
        finished = int(state.get('finished', 0))
        complete = int(state.get('complete', 0))
        missing = state.get('missing', [0, 0, 0])
        known = state.get('known', [finished - value for value in missing])
        self._stat_raid_results.setText(f'{complete}/{finished} raids read')
        history = state.get('history')
        self._stat_raid_results.setToolTip(
            f'Unread gold / elixir / dark results: {missing[0]} / {missing[1]} / {missing[2]}'
            + (f'\nLocal raid history: {history}' if history else '')
        )
        if not finished:
            self._tile_gold.setText('0')
            self._tile_elixir.setText('0')
            self._tile_dark.setText('0')
            self._stat_loot_rate.setText('— (needs 10m)')
            return

        for tile, value, unknown, read_count in zip(
            (self._tile_gold, self._tile_elixir, self._tile_dark),
            totals,
            missing,
            known,
        ):
            if not read_count:
                tile.setText('—')
            else:
                prefix = '≥' if unknown else ''
                tile.setText(f'{prefix}{self._fmt_amount(value)}')
                tile.setToolTip(f'Exact total: {value:,}')

        if elapsed < 600:
            self._stat_loot_rate.setText('— (needs 10m)')
            return
        rates = [self._fmt_amount(int(v * 3600 / elapsed)) for v in totals]
        self._stat_loot_rate.setText(f'G {rates[0]} · E {rates[1]} · DE {rates[2]}')

    def _on_status_line(self, msg, warning):
        if warning:
            self._stat_note.setText(msg)
            self._stat_note.setStyleSheet(f"color: {TOKENS['warning']}; font-size: 12px;")
        else:
            self._stat_note.setText(msg)
            self._stat_note.setStyleSheet(f"color: {TOKENS['text_muted']}; font-size: 12px;")
        if msg.lower().startswith(('idling', 'resuming')):
            idling = msg.lower().startswith('idling')
            self._set_badge_state('IDLING' if idling else 'FARMING', 'idling' if idling else 'farming')

    # ------------------------------------------------------------------
    # Duration & Inline Profile Sync
    # ------------------------------------------------------------------
    def _load_inline_profile_knobs(self):
        profile = load_profile_settings()
        self._wall_threshold_spin.blockSignals(True)
        self._reserve_builders_spin.blockSignals(True)
        self._wall_threshold_spin.setValue(profile.wall_upgrade_threshold_m)
        self._reserve_builders_spin.setValue(profile.reserve_builders)
        self._wall_threshold_spin.blockSignals(False)
        self._reserve_builders_spin.blockSignals(False)
        self._on_wall_toggle_changed(self._upgrade_walls.isChecked())

    def _save_inline_profile_knobs(self):
        profile = load_profile_settings()
        profile.wall_upgrade_threshold_m = self._wall_threshold_spin.value()
        profile.reserve_builders = self._reserve_builders_spin.value()
        save_profile_settings(profile)

    def _on_wall_toggle_changed(self, checked: bool):
        self._wall_threshold_spin.setEnabled(checked)
        self._wall_thresh_lbl.setEnabled(checked)

    def showEvent(self, event):
        super().showEvent(event)
        self._load_inline_profile_knobs()

    def _clear_minutes_selection(self):
        editor = self._minutes_spin.lineEdit()
        if editor is not None:
            editor.deselect()
            editor.setCursorPosition(len(editor.text()))

    def _duration_locked(self):
        return self._star_bonus.isChecked() or self._until_maxed.isChecked()

    def _unlock_duration(self):
        if self._star_bonus.isChecked():
            self._star_bonus.blockSignals(True)
            self._star_bonus.setChecked(False)
            self._star_bonus.blockSignals(False)
        if self._until_maxed.isChecked():
            self._until_maxed.blockSignals(True)
            self._until_maxed.setChecked(False)
            self._until_maxed.blockSignals(False)
        if self._minutes_spin.value() <= 0:
            self._minutes_spin.blockSignals(True)
            self._minutes_spin.setValue(15)
            self._minutes_spin.blockSignals(False)
        self._update_duration_enabled()

    def eventFilter(self, watched, event):
        try:
            if watched is getattr(self, '_minutes_spin', None) or watched is getattr(self, '_minutes_editor', None):
                if event.type() in (
                    QEvent.Type.MouseButtonPress,
                    QEvent.Type.MouseButtonDblClick,
                    QEvent.Type.FocusIn,
                    QEvent.Type.KeyPress,
                    QEvent.Type.Wheel,
                ):
                    if self._duration_locked():
                        self._unlock_duration()
                    if event.type() == QEvent.Type.MouseButtonPress and self._minutes_editor is not None:
                        QTimer.singleShot(0, self._focus_minutes_editor)
        except RuntimeError:
            pass
        return super().eventFilter(watched, event)

    def _focus_minutes_editor(self):
        if self._minutes_editor is not None and not self._duration_locked():
            self._minutes_spin.setFocus(Qt.FocusReason.MouseFocusReason)
            self._minutes_editor.setFocus(Qt.FocusReason.MouseFocusReason)
            self._minutes_editor.selectAll()

    def _on_minutes_spin_changed(self, value: int):
        if value > 0 and self._duration_locked():
            self._unlock_duration()
        elif value == 0 and not self._until_maxed.isChecked() and not self._star_bonus.isChecked():
            self._until_maxed.setChecked(True)
            return
        self._sync_preset_highlight()

    def _sync_preset_highlight(self):
        locked = self._duration_locked()
        current = self._minutes_spin.value()
        for mins, btn in getattr(self, '_preset_buttons', ()):
            active = (not locked) and (current == mins)
            btn.setProperty('active', 'true' if active else 'false')
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _step_minutes(self, delta: int):
        if self._duration_locked():
            self._unlock_duration()
        cur = max(1, self._minutes_spin.value())
        nxt = max(1, min(999, cur + delta))
        self._minutes_spin.setValue(nxt)
        self._clear_minutes_selection()
        QTimer.singleShot(0, self._clear_minutes_selection)

    def _set_minutes(self, value: int):
        if self._duration_locked():
            self._unlock_duration()
        self._minutes_spin.setValue(max(1, min(999, int(value))))
        self._sync_preset_highlight()
        self._clear_minutes_selection()

    def _update_duration_enabled(self):
        enabled = not self._duration_locked()
        self._duration_label.setEnabled(enabled)
        if enabled:
            if self._minutes_spin.value() <= 0:
                last_mins = int(self._settings.value('run/minutes', 15, type=int) or 15)
                self._minutes_spin.blockSignals(True)
                self._minutes_spin.setValue(max(1, min(999, last_mins)))
                self._minutes_spin.blockSignals(False)
            self._minutes_spin.setStyleSheet('')
            self._minutes_unit.setStyleSheet(f"color: {TOKENS['text_muted']};")
        else:
            if self._until_maxed.isChecked():
                self._minutes_spin.blockSignals(True)
                self._minutes_spin.setValue(0)
                self._minutes_spin.blockSignals(False)
            self._minutes_spin.setStyleSheet(f"color: {TOKENS['text_faint']};")
            self._minutes_unit.setStyleSheet(f"color: {TOKENS['text_faint']};")
        self._sync_preset_highlight()

    def _on_star_bonus_toggle(self, checked: bool):
        if checked and getattr(self, '_until_maxed', None) is not None and self._until_maxed.isChecked():
            self._until_maxed.blockSignals(True)
            self._until_maxed.setChecked(False)
            self._until_maxed.blockSignals(False)
        self._update_duration_enabled()

    def _on_until_maxed_toggle(self, checked: bool):
        if checked and self._star_bonus.isChecked():
            self._star_bonus.blockSignals(True)
            self._star_bonus.setChecked(False)
            self._star_bonus.blockSignals(False)
        self._update_duration_enabled()

    # ------------------------------------------------------------------
    # State Persistence & Start/Stop Actions
    # ------------------------------------------------------------------
    def _confirm_bb_elixir_prioritise(self):
        reply = QMessageBox.warning(
            self.window(),
            'Elixir priority',
            'Prioritising Elixir will deplete your trophies.\n\nDo you want to continue?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _get_method(self):
        if self._is_builder_base():
            btn = self._bb_strategy_group.checkedButton()
            if btn is None:
                return BUILDER_BASE_ATTACK_STRATEGIES['Baby Dragon']
            return BUILDER_BASE_ATTACK_STRATEGIES.get(btn.text(), 5)
        btn = self._strategy_group.checkedButton()
        if btn is None:
            return ATTACK_STRATEGIES['Edrags']
        return ATTACK_STRATEGIES.get(btn.text(), ATTACK_STRATEGIES['Edrags'])

    def _get_bb_prioritise(self):
        btn = self._bb_prioritise_group.checkedButton()
        if btn is None:
            return 'both'
        return btn.text().lower()

    def _get_auto_upgrade_mode(self):
        checked_id = self._auto_upgrade_group.checkedId()
        if 0 <= checked_id < len(self._AUTO_UPGRADE_LABELS):
            return self._AUTO_UPGRADE_LABELS[checked_id][1]
        return 'off'

    def _set_auto_upgrade_mode(self, mode):
        for i, (_label, key) in enumerate(self._AUTO_UPGRADE_LABELS):
            if key == mode:
                btn = self._auto_upgrade_group.button(i)
                if btn is not None:
                    btn.setChecked(True)
                return

    def _restore_choices(self):
        self._set_auto_upgrade_mode(str(self._settings.value('run/autoUpgrade', 'off')))
        self._upgrade_walls.setChecked(self._settings.value('run/upgradeWalls', False, type=bool))
        self._auto_donate.setChecked(self._settings.value('run/autoDonate', False, type=bool))
        self._auto_request.setChecked(self._settings.value('run/autoRequest', False, type=bool))
        minutes = self._settings.value('run/minutes', 15, type=int)
        if 1 <= minutes <= 999:
            self._minutes_spin.setValue(minutes)
        self._until_maxed.setChecked(self._settings.value('run/untilMaxed', False, type=bool))
        self._sync_preset_highlight()

    def _save_choices(self):
        self._settings.setValue('run/autoUpgrade', self._get_auto_upgrade_mode())
        self._settings.setValue('run/upgradeWalls', self._upgrade_walls.isChecked())
        self._settings.setValue('run/autoDonate', self._auto_donate.isChecked())
        self._settings.setValue('run/autoRequest', self._auto_request.isChecked())
        self._settings.setValue('run/untilMaxed', self._until_maxed.isChecked())
        if self._minutes_spin.value() > 0:
            self._settings.setValue('run/minutes', self._minutes_spin.value())
        self._save_inline_profile_knobs()

    def _get_minutes(self):
        self._minutes_spin.interpretText()
        return self._minutes_spin.value()

    def _multi_run_players_for_start(self):
        if self._is_builder_base() or not self._multi_run.isChecked():
            return None
        players = load_players()
        if not any(p.enabled and p.name.strip() for p in players):
            return []
        return players

    def start_bot(self):
        if self._controller.is_running():
            return
        if not check_game_window_aspect_for_start(parent=self.window(), on_configure=lambda: self._navigate_to('settings')):
            return
        builder_base = self._is_builder_base()
        method = self._get_method()
        multi_arg = self._multi_run_players_for_start()
        if not builder_base and self._multi_run.isChecked() and (not multi_arg):
            show_error(self.window(), 'Multi-run', 'Enable at least one player with Run and a non-empty name.\nOpen Accounts… to edit.')
            return
        star_bonus = self._star_bonus.isChecked()
        until_maxed = not builder_base and (not star_bonus) and (multi_arg is None) and self._until_maxed.isChecked()
        if star_bonus:
            mins = 5
        elif until_maxed:
            mins = 0
        else:
            mins = self._get_minutes()
            if mins <= 0 or mins > 999:
                show_error(self.window(), 'Error', 'Invalid time duration. Enter 1-999 minutes.')
                return
        ranked_fill = False if builder_base else self._ranked.isChecked()
        if ranked_fill:
            confirm_mins = self._get_minutes() or 15
            if not RankedAttackConfirmDialog.ask(self.window(), confirm_mins):
                return
        if builder_base and self._get_bb_prioritise() == 'elixir' and (not self._confirm_bb_elixir_prioritise()):
            return
        self._save_choices()
        self._controller.start(
            method=method,
            minutes=mins,
            star_bonus=star_bonus,
            ranked_fill=ranked_fill,
            upgrade_walls=False if builder_base else self._upgrade_walls.isChecked(),
            multi_run_players=multi_arg,
            builder_base=builder_base,
            loot_prioritise=self._get_bb_prioritise() if builder_base else 'both',
            auto_upgrade='off' if builder_base else self._get_auto_upgrade_mode(),
            auto_donate=False if builder_base else self._auto_donate.isChecked(),
            auto_request=False if builder_base else self._auto_request.isChecked(),
        )

    def is_star_bonus_enabled(self):
        return self._star_bonus.isChecked()

    def request_start_from_taskbar(self):
        if not self._controller.is_running():
            self.start_bot()

    def apply_autostart(self, minutes, upgrade_walls, auto_upgrade='off', auto_donate=False, auto_request=False):
        if self._controller.is_running():
            return
        self._star_bonus.setChecked(False)
        self._until_maxed.setChecked(minutes <= 0)
        if minutes > 0:
            self._minutes_spin.setValue(minutes)
        self._upgrade_walls.setChecked(bool(upgrade_walls))
        self._auto_donate.setChecked(bool(auto_donate))
        self._auto_request.setChecked(bool(auto_request))
        self._set_auto_upgrade_mode(auto_upgrade)
        self.start_bot()

    def request_stop_from_taskbar(self):
        if self._controller.is_running():
            self._controller.stop()

    def _on_bot_started(self):
        self._session_start_mono = time.monotonic()
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._set_badge_state('FARMING', 'farming')

    def _on_bot_finished_ui(self, error):
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        if error:
            self._set_badge_state('ERROR', 'error')
        else:
            self._set_badge_state('STOPPED', 'stopped')

    def _on_running_changed(self, running):
        self._btn_start.setEnabled(not running)
        self._btn_stop.setEnabled(running)
        if not running and self._state_badge.text().endswith(('FARMING', 'RUNNING', 'IDLING')):
            self._set_badge_state('STOPPED', 'stopped')
