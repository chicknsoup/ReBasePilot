"""Settings page — profile preferences, combat filter, clan calibration, and game-window target."""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config import resolve_aspect_key
from app.core.clan import donate_troop_template_names, missing_templates
from app.services.display import DisplayService
from app.services.window import DescendantInfo, WindowCandidate, WindowService
from app.ui.qt.point_picker import PointPickerDialog
from app.ui.qt.template_capture import TemplateCaptureDialog
from app.ui.qt.theme import SPACING, TOKENS
from app.ui.qt.widgets import (
    Card,
    HelpButton,
    PageSubtitle,
    PageTitle,
    SectionTitle,
    SubCard,
    ToggleSwitch,
    neutral_button,
    primary_button,
)
from app.utils.logger import setup_logger
from app.utils.profile_settings_store import (
    ATTACK_MAX_SKIPS_MAX,
    ATTACK_MIN_LOOT_K_MAX,
    CLAN_DONATE_COUNT_MAX,
    CLAN_DONATE_INTERVAL_M_MAX,
    CLAN_DONATE_INTERVAL_M_MIN,
    CLAN_MIN_ELIXIR_K_MAX,
    CLAN_REQUEST_INTERVAL_M_MAX,
    CLAN_REQUEST_INTERVAL_M_MIN,
    EARTHQUAKE_METHOD_OPTIONS,
    RESERVE_BUILDERS_MAX,
    WALL_UPGRADE_THRESHOLD_M_MAX,
    ProfileSettings,
    load_profile_settings,
    save_profile_settings,
)
from app.utils.window_settings_store import (
    clear_window_selection,
    load_window_selection,
    save_window_selection,
)

logger = setup_logger('SettingsPage')

CLAN_TROOP_CHOICES = (
    ('Auto (first troop found)', 'auto'),
    ('Valkyries', 'valkyries'),
    ('Sneaky Goblins', 'sneaky goblins'),
    ('Super Minions', 'super minions'),
    ('Edrags', 'edrags'),
)


class WindowInfoDialog(QDialog):
    """Read-only view of every child window/surface under a selected top-level window."""

    def __init__(self, parent, candidate: WindowCandidate, descendants: List[DescendantInfo]):
        super().__init__(parent)
        self.setWindowTitle('Window Inspector')
        self.setModal(True)
        self.resize(640, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING['lg'], SPACING['lg'], SPACING['lg'], SPACING['lg'])
        layout.setSpacing(SPACING['sm'])

        header = QLabel(
            f"Top-level: {candidate.title or '(no title)'}\n"
            f"Class: {candidate.top_class}    hwnd={candidate.top_hwnd}"
        )
        header.setWordWrap(True)
        header.setStyleSheet(f"color: {TOKENS['text']}; font-weight: 600;")
        layout.addWidget(header)

        count = len(descendants)
        surfaces = sum(1 for d in descendants if d.is_surface)
        summary = QLabel(
            f'{count} child window(s), {surfaces} game surface(s). Surfaces are marked [surface].'
        )
        summary.setStyleSheet(f"color: {TOKENS['text_muted']};")
        layout.addWidget(summary)

        listing = QListWidget()
        listing.setObjectName('WindowInfoList')
        mono = QFont('Consolas')
        mono.setStyleHint(QFont.StyleHint.Monospace)
        listing.setFont(mono)
        if descendants:
            for d in descendants:
                item = QListWidgetItem(d.display_label())
                if d.is_surface:
                    item.setForeground(QColor(TOKENS['primary']))
                listing.addItem(item)
        else:
            listing.addItem(QListWidgetItem('No child windows found under this window.'))
        layout.addWidget(listing, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = neutral_button('Close', parent=self)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._candidates: List[WindowCandidate] = []
        self._display = DisplayService()
        self._clan_chat_point: Optional[List[float]] = None
        self._clan_chat_point_aspect: Optional[str] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING['lg'], SPACING['md'], SPACING['lg'], SPACING['md'])
        outer.setSpacing(SPACING['md'])

        # ── Sticky Top Header Bar (Single Save / Reset location) ──
        outer.addWidget(self._build_header_bar())

        # ── Scrollable 2-Column Content Grid ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        grid = QHBoxLayout(content)
        grid.setContentsMargins(0, 0, 4, 0)
        grid.setSpacing(SPACING['md'])

        # Left column: Game Window & Display + Combat / Loot / Upgrades
        left_col = QVBoxLayout()
        left_col.setSpacing(SPACING['md'])
        left_col.addWidget(self._build_window_card())
        left_col.addWidget(self._build_combat_and_upgrades_card())
        left_col.addStretch()

        # Right column: Clan Assist & Calibration
        right_col = QVBoxLayout()
        right_col.setSpacing(SPACING['md'])
        right_col.addWidget(self._build_clan_card())
        right_col.addStretch()

        grid.addLayout(left_col, stretch=11)
        grid.addLayout(right_col, stretch=10)

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

    # ------------------------------------------------------------------
    # Top Sticky Bar
    # ------------------------------------------------------------------
    def _build_header_bar(self) -> Card:
        bar = Card(variant='CommandBar')
        bar.card_layout.setContentsMargins(14, 10, 14, 10)
        row = QHBoxLayout()
        row.setSpacing(SPACING['md'])

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(PageTitle('Settings & Calibration', parent=bar))
        title_col.addWidget(
            PageSubtitle(
                'Configure window target, loot filters, builder policies, and clan assist templates.',
                parent=bar,
            )
        )
        row.addLayout(title_col)
        row.addStretch()

        self._save_badge = QLabel('')
        self._save_badge.setStyleSheet(
            f"color: {TOKENS['primary']}; font-size: 12px; font-weight: 600;"
        )
        row.addWidget(self._save_badge)

        self._btn_reset = neutral_button('Reset', parent=bar)
        self._btn_reset.setToolTip('Discard unsaved edits and reload saved profile settings.')
        self._btn_reset.clicked.connect(self._reload_earthquake)
        row.addWidget(self._btn_reset)

        self._btn_save = primary_button('Save Changes', parent=bar)
        self._btn_save.setMinimumWidth(130)
        self._btn_save.clicked.connect(self._on_save)
        row.addWidget(self._btn_save)

        bar.card_layout.addLayout(row)
        return bar

    # ------------------------------------------------------------------
    # Left Column: Game Window & Display
    # ------------------------------------------------------------------
    def _build_window_card(self) -> Card:
        card = Card()
        hdr = QHBoxLayout()
        hdr.addWidget(SectionTitle('Game Window & Display', parent=card))
        hdr.addStretch()
        hdr.addWidget(
            HelpButton(
                tooltip=(
                    'Pick the Google Play Games window if auto-detect cannot find Clash of Clans.\n'
                    'Windows with a detected CROSVM game surface are listed first.'
                ),
                parent=card,
            )
        )
        card.card_layout.addLayout(hdr)

        self._window_list = QListWidget()
        self._window_list.setObjectName('WindowList')
        self._window_list.setMinimumHeight(125)
        self._window_list.setMaximumHeight(155)
        self._window_list.currentRowChanged.connect(lambda _: self._update_window_buttons())
        card.card_layout.addWidget(self._window_list)

        status_box = SubCard(parent=card)
        status_box.card_layout.setContentsMargins(10, 6, 10, 6)
        self._window_status = QLabel('Using auto-detect.')
        self._window_status.setWordWrap(True)
        self._window_status.setStyleSheet(f"color: {TOKENS['text_muted']}; font-size: 12px;")
        status_box.card_layout.addWidget(self._window_status)
        card.card_layout.addWidget(status_box)

        row = QHBoxLayout()
        row.setSpacing(6)
        self._btn_refresh = neutral_button('Refresh', parent=card)
        self._btn_refresh.clicked.connect(self._refresh_windows)
        row.addWidget(self._btn_refresh)

        self._btn_test = neutral_button('Test', parent=card)
        self._btn_test.clicked.connect(self._on_test_window)
        row.addWidget(self._btn_test)

        self._btn_info = neutral_button('Info', parent=card)
        self._btn_info.clicked.connect(self._on_window_info)
        row.addWidget(self._btn_info)

        self._btn_auto = neutral_button('Auto-detect', parent=card)
        self._btn_auto.clicked.connect(self._on_auto_detect)
        row.addWidget(self._btn_auto)

        row.addStretch()

        self._btn_use = primary_button('Use Window', parent=card)
        self._btn_use.clicked.connect(self._on_use_window)
        row.addWidget(self._btn_use)
        card.card_layout.addLayout(row)

        # Ultrawide 16:9 helper inside a compact SubCard
        disp_sub = SubCard(parent=card)
        disp_row = QHBoxLayout()
        disp_row.setSpacing(8)
        disp_lbl = QLabel('Ultrawide (21:9) fix')
        disp_lbl.setStyleSheet(f"color: {TOKENS['text_muted']}; font-size: 12px; font-weight: 600;")
        disp_row.addWidget(disp_lbl)
        disp_row.addWidget(
            HelpButton(
                tooltip=(
                    'Google Play Games locks Clash to your display aspect ratio at launch.\n'
                    '1. Click "Switch to 16:9"\n'
                    '2. Fully close & relaunch Clash of Clans\n'
                    '3. Click "Restore display" — the running game stays 16:9.'
                ),
                parent=disp_sub,
            )
        )
        disp_row.addStretch()
        self._btn_disp_169 = neutral_button('Switch to 16:9', parent=disp_sub)
        self._btn_disp_169.clicked.connect(self._on_switch_display_169)
        disp_row.addWidget(self._btn_disp_169)

        self._btn_disp_restore = neutral_button('Restore display', parent=disp_sub)
        self._btn_disp_restore.clicked.connect(self._on_restore_display)
        disp_row.addWidget(self._btn_disp_restore)
        disp_sub.card_layout.addLayout(disp_row)
        card.card_layout.addWidget(disp_sub)

        return card

    # ------------------------------------------------------------------
    # Left Column: Combat, Loot Filter & Upgrades
    # ------------------------------------------------------------------
    def _build_combat_and_upgrades_card(self) -> Card:
        card = Card()
        card.card_layout.addWidget(SectionTitle('Combat, Loot Filter & Upgrades', parent=card))

        # 1. Loot Filter SubCard
        loot_sub = SubCard(parent=card)
        loot_hdr = QHBoxLayout()
        loot_title = QLabel('Minimum Loot Filter (Battle Prep OCR)')
        loot_title.setStyleSheet(f"color: {TOKENS['text']}; font-weight: 600;")
        loot_hdr.addWidget(loot_title)
        loot_hdr.addStretch()
        loot_hdr.addWidget(
            HelpButton(
                tooltip=(
                    'Reads enemy Gold/Elixir on the battle-prep screen and clicks Next while below threshold.\n'
                    'Set both to 0 ("any") to attack every base immediately.'
                ),
                parent=loot_sub,
            )
        )
        loot_sub.card_layout.addLayout(loot_hdr)

        loot_grid = QGridLayout()
        loot_grid.setHorizontalSpacing(SPACING['md'])
        loot_grid.setVerticalSpacing(8)

        g_lbl = QLabel('Min Gold:')
        g_lbl.setStyleSheet(f"color: {TOKENS['gold']}; font-weight: 600;")
        self._attack_min_gold = QSpinBox()
        self._attack_min_gold.setRange(0, ATTACK_MIN_LOOT_K_MAX)
        self._attack_min_gold.setSingleStep(50)
        self._attack_min_gold.setSuffix('k')
        self._attack_min_gold.setSpecialValueText('any')
        self._attack_min_gold.setFixedWidth(96)
        loot_grid.addWidget(g_lbl, 0, 0)
        loot_grid.addWidget(self._attack_min_gold, 0, 1)

        e_lbl = QLabel('Min Elixir:')
        e_lbl.setStyleSheet(f"color: {TOKENS['elixir']}; font-weight: 600;")
        self._attack_min_elixir = QSpinBox()
        self._attack_min_elixir.setRange(0, ATTACK_MIN_LOOT_K_MAX)
        self._attack_min_elixir.setSingleStep(50)
        self._attack_min_elixir.setSuffix('k')
        self._attack_min_elixir.setSpecialValueText('any')
        self._attack_min_elixir.setFixedWidth(96)
        loot_grid.addWidget(e_lbl, 0, 2)
        loot_grid.addWidget(self._attack_min_elixir, 0, 3)

        s_lbl = QLabel('Max skips / raid:')
        s_lbl.setStyleSheet(f"color: {TOKENS['text_muted']};")
        self._attack_max_skips = QSpinBox()
        self._attack_max_skips.setRange(0, ATTACK_MAX_SKIPS_MAX)
        self._attack_max_skips.setFixedWidth(96)
        loot_grid.addWidget(s_lbl, 1, 0)
        loot_grid.addWidget(self._attack_max_skips, 1, 1)

        eq_lbl = QLabel('Earthquake:')
        eq_lbl.setStyleSheet(f"color: {TOKENS['text_muted']};")
        self._earthquake = QComboBox()
        self._earthquake.addItems(list(EARTHQUAKE_METHOD_OPTIONS))
        loot_grid.addWidget(eq_lbl, 1, 2)
        loot_grid.addWidget(self._earthquake, 1, 3)

        loot_sub.card_layout.addLayout(loot_grid)
        card.card_layout.addWidget(loot_sub)

        # 2. Builder & Wall Policy SubCard
        upg_sub = SubCard(parent=card)
        upg_hdr = QHBoxLayout()
        upg_title = QLabel('Builder & Wall Upgrade Policy')
        upg_title.setStyleSheet(f"color: {TOKENS['text']}; font-weight: 600;")
        upg_hdr.addWidget(upg_title)
        upg_hdr.addStretch()
        upg_hdr.addWidget(
            HelpButton(
                tooltip=(
                    '• Upgrade Order: Priciest first dumps full storages into big upgrades; Dark Elixir (heroes) always goes first.\n'
                    '• Wall Threshold: Minimum Gold/Elixir (in Millions) before opening the builder menu for a wall pass (0 = full storage only).\n'
                    '• Reserve Builders: Builders kept idle for wall upgrades when Auto Upgrade is active.'
                ),
                parent=upg_sub,
            )
        )
        upg_sub.card_layout.addLayout(upg_hdr)

        upg_grid = QGridLayout()
        upg_grid.setHorizontalSpacing(SPACING['md'])
        upg_grid.setVerticalSpacing(8)

        ord_lbl = QLabel('Upgrade order:')
        ord_lbl.setStyleSheet(f"color: {TOKENS['text_muted']};")
        self._upgrade_order = QComboBox()
        self._upgrade_order.addItems(['Priciest first', 'Cheapest first'])
        upg_grid.addWidget(ord_lbl, 0, 0)
        upg_grid.addWidget(self._upgrade_order, 0, 1, 1, 3)

        wall_lbl = QLabel('Wall threshold:')
        wall_lbl.setStyleSheet(f"color: {TOKENS['text_muted']};")
        self._wall_threshold = QSpinBox()
        self._wall_threshold.setRange(0, WALL_UPGRADE_THRESHOLD_M_MAX)
        self._wall_threshold.setSuffix('M')
        self._wall_threshold.setFixedWidth(96)
        upg_grid.addWidget(wall_lbl, 1, 0)
        upg_grid.addWidget(self._wall_threshold, 1, 1)

        res_lbl = QLabel('Reserve builders:')
        res_lbl.setStyleSheet(f"color: {TOKENS['text_muted']};")
        self._reserve_builders = QSpinBox()
        self._reserve_builders.setRange(0, RESERVE_BUILDERS_MAX)
        self._reserve_builders.setSuffix(' bld')
        self._reserve_builders.setFixedWidth(96)
        upg_grid.addWidget(res_lbl, 1, 2)
        upg_grid.addWidget(self._reserve_builders, 1, 3)

        upg_sub.card_layout.addLayout(upg_grid)
        card.card_layout.addWidget(upg_sub)

        return card

    # ------------------------------------------------------------------
    # Right Column: Clan Assist & Calibration
    # ------------------------------------------------------------------
    def _build_clan_card(self) -> Card:
        card = Card()
        hdr = QHBoxLayout()
        hdr.addWidget(SectionTitle('Clan Assist & Calibration', parent=card))
        hdr.addStretch()
        hdr.addWidget(
            HelpButton(
                tooltip=(
                    'Configures how Clan Assist donates and requests troops between raids.\n'
                    'Enable "Auto donate" or "Request troops" per session on the Run page.'
                ),
                parent=card,
            )
        )
        card.card_layout.addLayout(hdr)

        # 1. Templates & Chat Button Calibration SubCard
        cal_sub = SubCard(parent=card)
        cal_title = QLabel('Templates & Chat Button Calibration')
        cal_title.setStyleSheet(f"color: {TOKENS['text']}; font-weight: 600;")
        cal_sub.card_layout.addWidget(cal_title)

        tpl_row = QHBoxLayout()
        self._clan_templates_label = QLabel('')
        self._clan_templates_label.setWordWrap(True)
        self._clan_templates_label.setStyleSheet(f"color: {TOKENS['text_muted']}; font-size: 12px;")
        tpl_row.addWidget(self._clan_templates_label, stretch=1)

        btn_capture = primary_button('Capture Templates', parent=cal_sub)
        btn_capture.clicked.connect(self._on_capture_templates)
        tpl_row.addWidget(btn_capture)
        cal_sub.card_layout.addLayout(tpl_row)

        chat_row = QHBoxLayout()
        chat_lbl = QLabel('Chat fallback:')
        chat_lbl.setStyleSheet(f"color: {TOKENS['text_muted']}; font-size: 12px;")
        chat_row.addWidget(chat_lbl)

        self._clan_chat_label = QLabel('Not set')
        self._clan_chat_label.setStyleSheet(f"color: {TOKENS['text']}; font-size: 12px; font-weight: 500;")
        chat_row.addWidget(self._clan_chat_label, stretch=1)

        btn_pick = neutral_button('Pick Chat Button', parent=cal_sub)
        btn_pick.clicked.connect(self._on_pick_chat_button)
        chat_row.addWidget(btn_pick)

        btn_clear = neutral_button('Clear', parent=cal_sub)
        btn_clear.clicked.connect(self._on_clear_chat_button)
        chat_row.addWidget(btn_clear)
        cal_sub.card_layout.addLayout(chat_row)

        dry_row = QHBoxLayout()
        self._clan_dry_run = ToggleSwitch(
            'Dry run — inspect chat and log matches without clicking', parent=cal_sub
        )
        dry_row.addWidget(self._clan_dry_run)
        dry_row.addStretch()
        cal_sub.card_layout.addLayout(dry_row)

        card.card_layout.addWidget(cal_sub)

        # 2. Donation & Schedule Policy SubCard
        pol_sub = SubCard(parent=card)
        pol_title = QLabel('Donation Troop & Cadence')
        pol_title.setStyleSheet(f"color: {TOKENS['text']}; font-weight: 600;")
        pol_sub.card_layout.addWidget(pol_title)

        troop_lbl = QLabel('Fallback donate troop (used when no custom donatetroop*.png is captured):')
        troop_lbl.setWordWrap(True)
        troop_lbl.setStyleSheet(f"color: {TOKENS['text_muted']}; font-size: 12px;")
        pol_sub.card_layout.addWidget(troop_lbl)

        self._clan_troop = QComboBox()
        self._clan_troop.addItems([label for label, _key in CLAN_TROOP_CHOICES])
        self._clan_troop.currentIndexChanged.connect(lambda _idx: self._update_clan_templates_label())
        pol_sub.card_layout.addWidget(self._clan_troop)

        pol_grid = QGridLayout()
        pol_grid.setHorizontalSpacing(SPACING['md'])
        pol_grid.setVerticalSpacing(8)

        cnt_lbl = QLabel('Troops per request:')
        cnt_lbl.setStyleSheet(f"color: {TOKENS['text_muted']};")
        self._clan_donate_count = QSpinBox()
        self._clan_donate_count.setRange(0, CLAN_DONATE_COUNT_MAX)
        self._clan_donate_count.setSpecialValueText('until grey')
        self._clan_donate_count.setFixedWidth(105)
        pol_grid.addWidget(cnt_lbl, 0, 0)
        pol_grid.addWidget(self._clan_donate_count, 0, 1)

        flr_lbl = QLabel('Elixir floor:')
        flr_lbl.setStyleSheet(f"color: {TOKENS['elixir']}; font-weight: 600;")
        self._clan_min_elixir = QSpinBox()
        self._clan_min_elixir.setRange(0, CLAN_MIN_ELIXIR_K_MAX)
        self._clan_min_elixir.setSingleStep(50)
        self._clan_min_elixir.setSuffix('k')
        self._clan_min_elixir.setSpecialValueText('off')
        self._clan_min_elixir.setFixedWidth(105)
        pol_grid.addWidget(flr_lbl, 1, 0)
        pol_grid.addWidget(self._clan_min_elixir, 1, 1)

        don_lbl = QLabel('Donate interval:')
        don_lbl.setStyleSheet(f"color: {TOKENS['text_muted']};")
        self._clan_donate_interval = QSpinBox()
        self._clan_donate_interval.setRange(CLAN_DONATE_INTERVAL_M_MIN, CLAN_DONATE_INTERVAL_M_MAX)
        self._clan_donate_interval.setSuffix('m')
        self._clan_donate_interval.setSpecialValueText('every raid')
        self._clan_donate_interval.setFixedWidth(105)
        pol_grid.addWidget(don_lbl, 2, 0)
        pol_grid.addWidget(self._clan_donate_interval, 2, 1)

        req_lbl = QLabel('Request interval:')
        req_lbl.setStyleSheet(f"color: {TOKENS['text_muted']};")
        self._clan_request_interval = QSpinBox()
        self._clan_request_interval.setRange(CLAN_REQUEST_INTERVAL_M_MIN, CLAN_REQUEST_INTERVAL_M_MAX)
        self._clan_request_interval.setSuffix('m')
        self._clan_request_interval.setFixedWidth(105)
        pol_grid.addWidget(req_lbl, 3, 0)
        pol_grid.addWidget(self._clan_request_interval, 3, 1)

        pol_grid.setColumnStretch(2, 1)
        pol_sub.card_layout.addLayout(pol_grid)
        card.card_layout.addWidget(pol_sub)

        return card

    # ------------------------------------------------------------------
    # Handlers & Persistence
    # ------------------------------------------------------------------
    def _on_capture_templates(self):
        TemplateCaptureDialog.open(self.window())
        self._update_clan_templates_label()

    def _update_clan_templates_label(self):
        troop = CLAN_TROOP_CHOICES[max(0, self._clan_troop.currentIndex())][1]
        missing = missing_templates(
            donate=True,
            request=True,
            donate_troop=troop,
            have_chat_point=bool(self._clan_chat_point),
        )
        troops = donate_troop_template_names()
        troop_note = f'{len(troops)} troop crop(s) ready' if troops else 'no custom troop crop'
        if not missing:
            self._clan_templates_label.setText(f'● All required templates ready ({troop_note}).')
            self._clan_templates_label.setStyleSheet(f"color: {TOKENS['primary']}; font-size: 12px;")
        else:
            self._clan_templates_label.setText(
                f"Missing: {', '.join(missing)} ({troop_note})"
            )
            self._clan_templates_label.setStyleSheet(f"color: {TOKENS['warning']}; font-size: 12px;")

    def _update_chat_point_label(self):
        point = self._clan_chat_point
        if not point:
            self._clan_chat_label.setText('Not set (uses template matching)')
            self._clan_chat_label.setStyleSheet(f"color: {TOKENS['text_muted']}; font-size: 12px;")
            return
        aspect = self._clan_chat_point_aspect
        pretty = {'16_9': '16:9', '16_10': '16:10'}.get(aspect or '', 'unknown aspect')
        self._clan_chat_label.setText(f'{point[0] * 100:.1f}% x, {point[1] * 100:.1f}% y ({pretty})')
        self._clan_chat_label.setStyleSheet(f"color: {TOKENS['primary']}; font-size: 12px; font-weight: 600;")

    def _on_pick_chat_button(self):
        picked = PointPickerDialog.pick(
            self.window(),
            title='Pick the clan chat button',
            hint=(
                'Click the clan chat button in the screenshot below — the one that opens '
                'the chat panel on the left of the game. Press Refresh if the game moved.'
            ),
        )
        if picked is None:
            return
        fx, fy, aspect = picked
        self._clan_chat_point = [float(fx), float(fy)]
        self._clan_chat_point_aspect = aspect
        self._update_chat_point_label()
        self._update_clan_templates_label()
        self._flash_status_bar('Chat button picked — press Save Changes')

    def _on_clear_chat_button(self):
        self._clan_chat_point = None
        self._clan_chat_point_aspect = None
        self._update_chat_point_label()
        self._update_clan_templates_label()
        self._flash_status_bar('Chat button cleared — press Save Changes')

    def showEvent(self, event):
        super().showEvent(event)
        self._reload_earthquake()
        self._refresh_windows()

    def _reload_earthquake(self):
        settings = load_profile_settings()
        idx = self._earthquake.findText(settings.earthquake_method)
        if idx >= 0:
            self._earthquake.setCurrentIndex(idx)
        self._wall_threshold.setValue(settings.wall_upgrade_threshold_m)
        self._reserve_builders.setValue(settings.reserve_builders)
        self._upgrade_order.setCurrentIndex(1 if settings.upgrade_order == 'cheapest' else 0)
        self._clan_chat_point = list(settings.clan_chat_point) if settings.clan_chat_point else None
        self._clan_chat_point_aspect = settings.clan_chat_point_aspect
        self._update_chat_point_label()
        self._clan_dry_run.setChecked(bool(settings.clan_dry_run))
        keys = [key for _label, key in CLAN_TROOP_CHOICES]
        self._clan_troop.setCurrentIndex(
            keys.index(settings.clan_donate_troop) if settings.clan_donate_troop in keys else 0
        )
        self._clan_donate_count.setValue(settings.clan_donate_count)
        self._clan_min_elixir.setValue(settings.clan_min_elixir_k)
        self._clan_donate_interval.setValue(settings.clan_donate_interval_m)
        self._clan_request_interval.setValue(settings.clan_request_interval_m)
        self._update_clan_templates_label()
        self._attack_min_gold.setValue(settings.attack_min_gold_k)
        self._attack_min_elixir.setValue(settings.attack_min_elixir_k)
        self._attack_max_skips.setValue(settings.attack_max_skips)

    def _on_save(self):
        save_profile_settings(
            ProfileSettings(
                earthquake_method=self._earthquake.currentText(),
                wall_upgrade_threshold_m=self._wall_threshold.value(),
                reserve_builders=self._reserve_builders.value(),
                upgrade_order='cheapest' if self._upgrade_order.currentIndex() == 1 else 'priciest',
                clan_dry_run=self._clan_dry_run.isChecked(),
                clan_chat_point=self._clan_chat_point,
                clan_chat_point_aspect=self._clan_chat_point_aspect,
                clan_donate_troop=CLAN_TROOP_CHOICES[max(0, self._clan_troop.currentIndex())][1],
                clan_donate_count=self._clan_donate_count.value(),
                clan_min_elixir_k=self._clan_min_elixir.value(),
                clan_donate_interval_m=self._clan_donate_interval.value(),
                clan_request_interval_m=self._clan_request_interval.value(),
                attack_min_gold_k=self._attack_min_gold.value(),
                attack_min_elixir_k=self._attack_min_elixir.value(),
                attack_max_skips=self._attack_max_skips.value(),
            )
        )
        self._save_badge.setText('✓ Saved')
        QTimer.singleShot(2000, lambda: self._save_badge.setText(''))
        self._flash_status_bar('Settings saved')

    # ------------------------------------------------------------------
    # Window Selection Helpers
    # ------------------------------------------------------------------
    def _selected_candidate(self) -> Optional[WindowCandidate]:
        row = self._window_list.currentRow()
        if 0 <= row < len(self._candidates):
            return self._candidates[row]
        return None

    def _update_window_buttons(self):
        cand = self._selected_candidate()
        has_sel = cand is not None
        self._btn_test.setEnabled(True)
        self._btn_use.setEnabled(has_sel)
        self._btn_info.setEnabled(has_sel)

    def _refresh_windows(self):
        try:
            self._candidates = WindowService().enumerate_windows()
            saved = load_window_selection()
            self._window_list.clear()
            selected_row = -1
            for i, cand in enumerate(self._candidates):
                item = QListWidgetItem(cand.display_label())
                if not cand.is_game:
                    item.setForeground(self._muted_brush())
                self._window_list.addItem(item)
                if saved.is_set():
                    title_match = cand.title.strip().lower() == saved.title.strip().lower()
                    class_match = (not saved.top_class) or (cand.top_class == saved.top_class)
                    if title_match and class_match:
                        selected_row = i
            if selected_row >= 0:
                self._window_list.setCurrentRow(selected_row)
            if not self._candidates:
                self._window_status.setText('No visible windows found. Open the game, then press Refresh.')
            elif saved.is_set():
                self._window_status.setText(f"Pinned window: {saved.title or '(saved)'}")
            else:
                self._window_status.setText('Mode: Auto-detecting Google Play Games surface.')
            self._update_window_buttons()
        except Exception as exc:
            logger.warning(f'Could not enumerate windows: {exc}')
            self._candidates = []

    def _muted_brush(self):
        return QColor(TOKENS['text_muted'])

    @staticmethod
    def _aspect_label(w: int, h: int) -> str:
        if not w or not h:
            return 'size unavailable'
        aspect = resolve_aspect_key(w, h)
        if aspect is None:
            return f'{w}x{h} (not ~16:9/16:10)'
        pretty = '16:9' if aspect == '16_9' else '16:10'
        return f'{w}x{h} ({pretty})'

    def _on_test_window(self):
        cand = self._selected_candidate()
        if cand is None or not cand.is_game:
            from app.services.play_games_diagnostics import recent_play_games_problem

            problem = recent_play_games_problem()
            if problem:
                self._window_status.setText(problem.message)
                return
            self._window_status.setText(
                'No Google Play Games surface (CROSVM) under this window — open Clash, then Refresh and pick the game window.'
            )
            return
        ws = WindowService()
        surface_size = ws.window_pixel_size(cand.child_hwnd)
        if surface_size is None:
            self._window_status.setText('Could not read the window size. Is the game minimized?')
            return
        sub_size = None
        try:
            for d in ws.enumerate_descendants(cand.top_hwnd):
                if d.cls.lower() == 'subwin':
                    sub_size = (d.width, d.height)
                    break
            sw, sh = surface_size
            lines = [f'Capture target {cand.child_class}: {self._aspect_label(sw, sh)}']
            if sub_size is not None:
                lines.append(f'Inner subWin: {self._aspect_label(*sub_size)}')
            if resolve_aspect_key(sw, sh) is None:
                lines.append('Surface aspect unsupported — try resizing the game window.')
            else:
                lines.append('✓ Surface OK to use.')
            self._window_status.setText('  |  '.join(lines))
        except Exception as exc:
            logger.warning(f'Could not inspect subWin: {exc}')

    def _on_switch_display_169(self):
        ok, size, reason = self._display.switch_to_16_9()
        if ok and reason == 'already_16_9':
            self._window_status.setText(
                'Your display is already 16:9 — just (re)launch Clash in Google Play Games.'
            )
        elif ok:
            self._window_status.setText(
                f'Display set to {size[0]}x{size[1]} (16:9). Fully close & reopen Clash, then click "Restore display".'
            )
        elif reason == 'no_16_9_mode':
            self._window_status.setText(
                'Your display driver offers no 16:9 mode — use a 16:9 monitor for the game instead.'
            )
        else:
            self._window_status.setText('Could not switch the display resolution.')
        self._flash_status_bar('Display set to 16:9' if ok else 'Display switch failed')

    def _on_restore_display(self):
        ok, size, reason = self._display.restore()
        if ok:
            self._window_status.setText(
                f'Display restored to {size[0]}x{size[1]}. Press Test to confirm 16:9 surface.'
            )
        elif reason == 'nothing_to_restore':
            self._window_status.setText('Nothing to restore — display was not switched.')
        else:
            self._window_status.setText(
                'Could not restore the display. Use Windows Settings → Display to set it back.'
            )
        self._flash_status_bar('Display restored' if ok else 'Restore failed')

    def _on_window_info(self):
        cand = self._selected_candidate()
        if cand is None:
            return
        try:
            descendants = WindowService().enumerate_descendants(cand.top_hwnd)
            WindowInfoDialog(self.window(), cand, descendants).exec()
        except Exception as exc:
            logger.warning(f'Could not enumerate descendants: {exc}')

    def _on_use_window(self):
        cand = self._selected_candidate()
        if cand is None:
            return
        save_window_selection(cand.to_selection())
        self._window_status.setText(f"Pinned: {cand.title or '(no title)'}. Press Test to verify.")
        self._flash_status_bar('Window saved')

    def _on_auto_detect(self):
        clear_window_selection()
        self._window_status.setText('Cleared — using auto-detect.')
        self._flash_status_bar('Auto-detect enabled')
        self._refresh_windows()

    def _flash_status_bar(self, msg: str):
        win = self.window()
        if isinstance(win, QMainWindow) and win.statusBar() is not None:
            win.statusBar().showMessage(msg, 1800)
