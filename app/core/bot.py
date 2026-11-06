# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\core\\bot.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

import time
from app.core.battle_rewards import BattleRewards
from app.core.ui_navigation import find_battle_button, find_navigation_fallback, find_ui_label, is_village_hud
import random
import threading
import cv2
import numpy as np
from typing import Callable, List, Optional, Tuple
from app.config import ASPECT_16_10, ASPECT_16_9, Config
from app.core.clan import ClanAssistant, ClanOptions, missing_templates
from app.core.loot_filter import TEMPLATE_NEXT_BASE, LootFilter, combine_reads, meets, read_enemy_loot
from app.core.loot_tracker import LootLedger, UNKNOWN, corroborate_results, read_raid_result, read_raid_bonus
from app.core.strategies import AttackStrategy, EdragStrategy, TroopSpamStrategy, _EDRAG_DELAY
from app.core.upgrader import AUTO_UPGRADE_MODES, LIVE_UPGRADE_MODES, MODE_OFF, UpgradeAdvisor
from app.core.research import ResearchAdvisor
from app.core.upgrade_menu import UpgradeRow, detect_build_menu, is_wall_row, parse_builder_menu
from app.core.ui_navigation import find_profile_button, find_settings_button
from app.core.village_state import read_hud_triplet_stable, read_village_state, read_village_state_stable
from app.services.input import InputService
from app.services.vision import BOTTOM_HALF_BOT_TEMPLATES, TOP_HALF_BOT_TEMPLATES, VisionService
from app.services.window import WindowService
from app.utils.common import get_template_path, get_user_app_data_dir
from app.utils.logger import setup_logger
from app.utils.player_list_store import PlayerEntry
from app.utils.profile_settings_store import EARTHQUAKE_METHOD_CURVE
logger = setup_logger('BotCore')
_HOME_VILLAGE_BUILDER_TEMPLATES = ('builder.png', 'gbuilder.png')
_WALL_OCR_RETRY_DRAG_BASELINE: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {ASPECT_16_9: ((1300, 280), (1300, 830)), ASPECT_16_10: ((1300, 280), (1300, 980))}
_BB_BABY_DRAGON_RECLICK_WAIT = 10
_BB_RETURN_HOME_TEMPLATE = 'breturnhome.png'
_BB_RETURN_HOME_TEMPLATE_1080P = 'breturnhome1920.png'
_BB_1080P_CAPTURE_SIZE = (1920, 1080)
_BB_1080P_SIZE_TOLERANCE = 40
_BB_TEMPLATE_SUPPRESS_PAD = 0.1
_BB_FULL_ELIXIR_CART_TEMPLATES = ('fullecart.png', 'fullecart2.png', 'fullecart3.png')
_BB_COLLECT_TEMPLATE = 'collect.png'
_BB_STAR_BONUS_TEMPLATE = 'bstar.png'
_BB_BATTLE_STAR_TEMPLATE = 'bbstar.png'
_BB_BATTLE_MACHINE_TEMPLATE = 'battlemachine.png'
_BB_FLYING_MACHINE_TEMPLATE = 'flyingmachine.png'
_BB_GOLD_PRIORITISE_MIN_STARS = 2
_BB_RETURN_HOME_END_BATTLE_RETRIES = 5
_STAR_BONUS_THRESHOLD = 0.75
_TROOP_FAILURE_LIMIT = 4
_TROOP_RETRY_WAIT_SECONDS = 15
_RELOAD_GRACE_SECONDS = 180
_WALL_BATCH_MAX_ADDS = 15
_WALL_MENU_SCROLL_STEPS = 20
_WALL_MENU_BOTTOM_DIFF = 4.0
_WALL_MENU_OPEN_SECONDS = 1.2
_WALL_MENU_WHEEL_CLICKS = 2
_WALL_ROW_STABLE_TOL = 30
_AUTO_UPGRADE_SCAN_INTERVAL_SECONDS = 600
_IDLE_RECHECK_SECONDS = 300
_CLAN_UNCALIBRATED_BACKOFF_SECONDS = 3600
_CLAN_CLOCK_NEVER = float('-inf')
_NEXT_CLEAR_POLLS = 8
_NEXT_SETTLE_SECONDS = 1.0
_LOOT_READ_POLLS = 6
_LOOT_READ_SAMPLES = 3
_WALL_POPUP_SETTLE_POLLS = 6
_WALL_REMOVE_ATTEMPTS = 2
_WALL_ADD_ATTEMPTS = 3
_WALL_POPUP_ANIMATION_SECONDS = 0.6
_WALL_ADD_CLICK_PAUSE = 0.9
_WALL_COST_CHANGE_DIFF = 0.5
_WALL_MENU_SCROLL_BASELINE: dict[str, tuple[int, int]] = {ASPECT_16_9: (1305, 605), ASPECT_16_10: (1305, 672)}
def _dump_debug_frame(frame, prefix):
    """Save a frame to the debug dir; returns the path or None. Never raises."""
    if frame is None:
        return
    else:
        try:
            import cv2 as _cv2
            from app.utils.common import get_user_app_data_dir
            dbg = get_user_app_data_dir() / 'debug'
            dbg.mkdir(parents=True, exist_ok=True)
            path = dbg / f'{prefix}_{int(time.time())}.jpg'
            _cv2.imwrite(str(path), frame, [_cv2.IMWRITE_JPEG_QUALITY, 88])
            logger.info('Debug frame saved to %s', path)
            return path
        except Exception:
            logger.debug('Debug frame dump failed', exc_info=True)
            return None
class Bot:
    # ***<module>.Bot: Failure: Different bytecode
    """Main Bot Logic."""
    def __init__(self):
        self.config = Config()
        self.window = WindowService()
        self.stop_event = threading.Event()
        self.input = InputService(self.window, self.stop_event)
        self.vision = VisionService()
        VisionService._stop_event = self.stop_event
        self._battle_rewards = BattleRewards(self.window, self.input, self.stop_event)
        self.running = False
        self._earthquake_method = EARTHQUAKE_METHOD_CURVE
        self._loot_ledger = None
        self._inventory_prev = None
        self._loot_session_start_mono = 0
        self._loot_callback = None
    def bind_stop_event(self, event):
        """Bind a fresh stop event for a new run session across all subsystems."""
        self.stop_event = event
        self.input.stop_event = event
        self._battle_rewards.stop_event = event
        VisionService._stop_event = event
        if getattr(self, '_advisor', None) is not None:
            self._advisor.stop_event = event
        if getattr(self, '_research_advisor', None) is not None:
            self._research_advisor.stop_event = event
    def start(self, method, run_time_minutes, star_bonus=False, status_callback=None, loot_callback=None, multi_run_players=None, ranked_fill=False, upgrade_walls=False, earthquake_method=EARTHQUAKE_METHOD_CURVE, builder_base=False, loot_prioritise='both', wall_upgrade_threshold=0, auto_upgrade=MODE_OFF, reserve_builders=1, state_callback=None, upgrade_order=None, clan_options=None, loot_filter=None):
        """Starts the bot loop. With ``multi_run_players``, runs a full session per enabled player.\n``run_time_minutes <= 0`` (single Home Village runs only) means UNLIMITED — farm,\nupgrade and idle until the user stops the bot (\"run until maxed\")."""
        self._status_callback = status_callback
        self._loot_callback = loot_callback
        self._state_callback = state_callback
        self._earthquake_method = earthquake_method
        if not self.window.find_window():
            raise RuntimeError('Clash of Clans window not found. Please ensure the game is open.')
        self._builder_base = builder_base
        self._reset_loot_session()
        self._emit_loot_update()
        self.running = True
        self.stop_event.clear()
        unlimited = not star_bonus and multi_run_players is None and (not builder_base) and (int(run_time_minutes or 0) <= 0)
        duration = 900 if star_bonus else 0 if unlimited else max(1, int(run_time_minutes)) * 60
        mr = multi_run_players is not None
        self._loot_prioritise = loot_prioritise
        self._wall_upgrade_threshold = max(0, int(wall_upgrade_threshold or 0))
        self._auto_upgrade_mode = auto_upgrade if auto_upgrade in AUTO_UPGRADE_MODES else MODE_OFF
        self._auto_upgrade_last_scan = 0
        self._upgrade_retry_pending = False
        self._reserve_builders = max(0, int(reserve_builders or 0))
        self._upgrade_order = upgrade_order
        self._advisor = None
        self._research_advisor = None
        self._clan_options = clan_options if clan_options is not None else ClanOptions()
        self._loot_filter = loot_filter if loot_filter is not None else LootFilter()
        self._loot_filter_off = False
        self._bases_skipped = 0
        self._clan = None
        self._clan_last_donate = _CLAN_CLOCK_NEVER
        self._clan_last_request = _CLAN_CLOCK_NEVER
        logger.info(''.join(['Bot started. Method: ', f'{method}', ', Time: ', f'{("unlimited" if unlimited else f"{run_time_minutes}m")}', ', StarBonus: ', f'{star_bonus}', ', MultiRun: ', f'{mr}', ', RankedFill: ', f'{ranked_fill}', ', UpgradeWalls: ', f'{upgrade_walls}', ', WallThreshold: ', f'{self._wall_upgrade_threshold}', ', AutoUpgrade: ', f'{self._auto_upgrade_mode}', ', ReserveBuilders: ', f'{self._reserve_builders}', ', Earthquake: ', f'{earthquake_method}', ', BuilderBase: ', f'{builder_base}', ', LootPrioritise: ', f'{loot_prioritise}', ', ClanDonate: ', f'{self._clan_options.donate}', ', ClanRequest: ', f'{self._clan_options.request}', ', ClanDryRun: ', f'{self._clan_options.dry_run}', ', LootFilter: ', f'{(self._loot_filter.summary() if self._loot_filter.enabled else "off")}']))
        try:
            if multi_run_players is not None:
                self._ensure_correct_village(builder_base=False)
                queue = [p for p in multi_run_players if p.enabled]
                if not queue:
                    raise RuntimeError('Multi-run: no players marked Run')
                for player in queue:
                    self._check_stop()
                    self._switch_account_and_load_home(player.name)
                    self._clan_last_donate = _CLAN_CLOCK_NEVER
                    self._clan_last_request = _CLAN_CLOCK_NEVER
                    self._check_stop()
                    self._run_loop(method, duration, star_bonus, ranked_fill, upgrade_walls)
                    self._check_stop()
                    self._multi_run_builder_base_after_session()
                    self._check_stop()
            else:
                if self._builder_base:
                    self._ensure_correct_village(builder_base=True)
                    self._run_builder_base_loop(duration, star_bonus=star_bonus)
                else:
                    self._ensure_correct_village(builder_base=False)
                    self._run_loop(method, duration, star_bonus, ranked_fill, upgrade_walls)
        except InterruptedError:
            logger.info('Bot stopped by user.')
        except Exception as e:
            logger.error(f'Bot crashed: {e}', exc_info=True)
            raise
        finally:
            self._finish_unread_raid('session ended before a result could be recorded')
            self.running = False
            self._earthquake_method = EARTHQUAKE_METHOD_CURVE
            logger.info('Bot stopped.')

    def stop(self):
        '''Signals the bot to stop.'''
        self.running = False
        self.stop_event.set()
    def _reset_loot_session(self):
        """Start a fresh raid ledger; inventory is a separate observation."""
        self._loot_ledger = LootLedger(get_user_app_data_dir() / 'loot_history')
        self._loot_session_start_mono = time.monotonic()
        self._loot_account = None
        self._inventory_prev = None
        self._last_hud_triplet = None
        self._gold_pinned = False
        self._elixir_pinned = False
    def _emit_loot_update(self):
        ledger = getattr(self, '_loot_ledger', None)
        cb = getattr(self, '_loot_callback', None)
        if cb:
            elapsed = max(0, time.monotonic() - float(self._loot_session_start_mono))
            cb(*(ledger.totals if ledger is not None else (0, 0, 0)), elapsed)
        if ledger is not None:
            self._emit_state(raid_loot=dict(ledger.summary(), supported=not getattr(self, '_builder_base', False)))
    def _begin_raid(self, ranked=False):
        ledger = getattr(self, '_loot_ledger', None)
        if ledger is not None:
            ledger.begin(mode='ranked' if ranked else 'farming', account=getattr(self, '_loot_account', None))
            self._emit_loot_update()
    def _finish_unread_raid(self, reason):
        ledger = getattr(self, '_loot_ledger', None)
        if ledger is not None and ledger.finish(reason=reason):
                self._emit_loot_update()
    def _record_raid_result(self, first_frame):
        ledger = getattr(self, '_loot_ledger', None)
        if ledger is None or ledger.pending is None or getattr(self, '_builder_base', False):
            return None
        else:
            reads, bonus_reads = ([], [])
            amounts = bonus = UNKNOWN
            frame = first_frame
            interrupted = False
            debug_dumped = False
            for attempt in range(4):
                if self.stop_event.is_set():
                    interrupted = True
                    break
                else:
                    if attempt:
                        if self.stop_event.wait(0.25):
                            interrupted = True
                            break
                        else:
                            frame = self.window.screenshot()
                    try:
                        read = read_raid_result(frame, self.vision)
                    except InterruptedError:
                        interrupted = True
                        break
                    except Exception:
                        logger.debug('Raid result reader failed; keeping the read unknown', exc_info=True)
                        read = None
                    try:
                        bonus_read = read_raid_bonus(frame, self.vision) if read is not None else None
                    except InterruptedError:
                        interrupted = True
                        break
                    except Exception:
                        logger.debug('League bonus reader failed; keeping that component unknown', exc_info=True)
                        bonus_read = None
                    reads.append(read)
                    bonus_reads.append(bonus_read)
                    amounts = corroborate_results(reads)
                    bonus = corroborate_results(bonus_reads)
                    if all((value is not None for value in amounts)):
                        break
            if amounts == UNKNOWN or any(value is None for value in amounts):
                # Every read failed — the 'You got' anchor or number geometry does not
                # match this screen. Dump one frame so the pass can be recalibrated
                # from the saved capture instead of silently logging ?/?/? forever.
                _dump_debug_frame(frame, 'raidresult_unreadable')
                debug_dumped = True
                logger.warning('Raid result: all %s reads failed — frame dumped to debug dir for calibration', len(reads))
            reason = 'result read interrupted' if interrupted else 'battle result'
            if ledger.finish(amounts, bonus=bonus, reason=reason):
                self._emit_loot_update()
            if interrupted:
                raise InterruptedError('Bot stopped by user')
    def _refresh_home_resources(self):
        """Refresh spendable inventory and cap detection; never add raid earnings."""
        triplet = self._read_hud_triplet_stable()
        if triplet is None:
            self._last_hud_triplet = None
            self._gold_pinned = self._elixir_pinned = False
            self._emit_state(storages='unreadable')
            return
        else:
            previous = getattr(self, '_inventory_prev', None)
            self._gold_pinned = previous is not None and triplet[0] == previous[0]
            self._elixir_pinned = previous is not None and triplet[1] == previous[1]
            self._inventory_prev = self._last_hud_triplet = triplet
            ledger = getattr(self, '_loot_ledger', None)
            if ledger is not None:
                ledger.record_inventory(triplet, account=getattr(self, '_loot_account', None))
            self._emit_state(storages=' / '.join((self._fmt_resource(value) for value in triplet)))
    def _check_stop(self):
        if self.stop_event.is_set():
            raise InterruptedError('Bot stopped by user')
    def _update_config_size(self, frame):
        '''Reload aspect profile if screenshot size implies a different template folder.'''
        self.config.set_target_size_from_frame(frame)
    def _scroll_point(self):
        '''Scroll anchor in authoring space [1000,1000]; scaled to current capture.'''
        (x, y) = self.config.scale_point([
            1000,
            1000])
        return (x, y)
    def _nudge_view_to_reveal_attack(self):
        '''Click ``empty`` (``data.json``) and scroll at anchor to nudge village until Attack is findable.
        ``empty`` sits in the bottom-right forest outside the obstacle border, so tapping it
        also dismisses any selected obstacle/building popup or unplaced-items bar.'''
        self.input.click(pause = 0.15, *self.config.get_point('empty'))
        self.input.scroll(*self._scroll_point(), 5)
    def _find_home_village_builder(self, frame, region):
        for name in _HOME_VILLAGE_BUILDER_TEMPLATES:
            (x, y) = self.vision.find_template(frame, name, region = region)
            if not x:
                continue
            return (x, y)
        return (None, None)
    def _wall_menu_drag_to_bottom(self):
        '''Drag the builder upgrade list to its bottom so ``Wall`` (list end) is revealed.

Touch-style swipe up: click low in the list, drag to the top, release — the reverse of
the old wheel scroll. Baseline coords are scaled via :meth:`Config.scale_point`. Dragging
past the bottom is a harmless no-op, so this can be called repeatedly.
'''
        pair = _WALL_OCR_RETRY_DRAG_BASELINE.get(self.config.aspect_key)
        if pair is None:
            logger.warning('wall menu drag: unknown aspect %r; skipping drag', self.config.aspect_key)
            return None
        (top_ref, bottom_ref) = pair
        top = self.config.scale_point([
            top_ref[0],
            top_ref[1]])
        bottom = self.config.scale_point([
            bottom_ref[0],
            bottom_ref[1]])
        (x_top, y_top) = (int(top[0]), int(top[1]))  # [recovered: pycdc reversed STORE_FAST_STORE_FAST pairs]
        (x_bot, y_bot) = (int(bottom[0]), int(bottom[1]))
        self.input.move(x_bot, y_bot)
        self.input.mouse_down(x_bot, y_bot)
        
        try:
            self.input.human_move(x_bot, y_bot, x_top, y_top, duration = 0.5)
            self.input.mouse_up(x_top, y_top)
            return None
        except:
            self.input.mouse_up(x_top, y_top)
    def _wall_menu_drag_retry_nudge(self):
        '''Vertical drag in the builder list when ``wall`` OCR misses (~0.5s eased move + hold pause).'''
        pair = _WALL_OCR_RETRY_DRAG_BASELINE.get(self.config.aspect_key)
        if pair is None:
            logger.warning('wall OCR retry drag: unknown aspect %r; skipping drag', self.config.aspect_key)
            return None
        (p1_ref, p2_ref) = pair
        p1 = self.config.scale_point([
            p1_ref[0],
            p1_ref[1]])
        p2 = self.config.scale_point([
            p2_ref[0],
            p2_ref[1]])
        (x1, y1) = (int(p1[0]), int(p1[1]))  # [recovered: pycdc reversed STORE_FAST_STORE_FAST pairs]
        (x2, y2) = (int(p2[0]), int(p2[1]))
        self.input.move(x1, y1)
        self.input.mouse_down(x1, y1)
        
        try:
            self.input.human_move(x1, y1, x2, y2, duration = 0.5)
            if self.stop_event.wait(0.3):
                self.input.mouse_up(x2, y2)
                return None
            self.input.mouse_up(x2, y2)
            return None
        except:
            self.input.mouse_up(x2, y2)
    def _find_wall_row_once(self):
        """One fresh screenshot → Wall label OCR, both polarities (dark popup text first).\n\nReturns ``(frame, point)``; the frame comes back so the caller can tell a list\nthat is still travelling from one that has hit its bottom without paying for a\nsecond capture.\n"""
        frame = self.window.screenshot()
        if frame is None:
            return (None, None)
        else:
            self._update_config_size(frame)
            layout = detect_build_menu(frame)
            self._last_wall_build_layout = layout
            if layout is not None:
                wall_target = UpgradeRow('Wall', '', None, None, None, (0, 0), 0)
                for row in parse_builder_menu(frame, batch_labels=True, target=wall_target, layout=layout):
                    if is_wall_row(row) and row.section != 'in_progress':
                        if row.cost is not None:
                            self._last_wall_unit_cost = int(row.cost)
                        return (frame, row.center)
                return (frame, None)
            else:
                for white in [False, True]:
                    pt = VisionService.find_wall_labels_top_center_ocr(frame, white_text=white, cc_filter_blobs=False)
                    if pt:
                        return (frame, pt)
                return (frame, None)
    def _wall_menu_signature(self, frame):
        """A thumbnail of the builder-menu ROI, for telling \"the list moved\" from \"the\nlist is at its end\". None when there is no frame.\n\nThe same ROI the row OCR reads, so no new geometry: the village animating behind\nthe translucent popup is the only thing that changes once the list has stopped,\nand that is worth about 1.5 in mean abs difference against 10-24 for a nudge.\n"""
        if frame is None:
            return
        else:
            h, w = frame.shape[:2]
            layout = getattr(self, '_last_wall_build_layout', None)
            if layout is None:
                layout = detect_build_menu(frame)
            if layout is not None:
                rx = min((card[0] for card in layout.cards)) + 3
                ry = layout.title_bottom + 3
                rw = max(1, w - 3 - rx)
                rh = max(1, round(h * 0.87) - ry)
            else:
                rx, ry, rw, rh = VisionService.top_middle_square_roi(w, h)
            roi = frame[ry:ry + rh, rx:rx + rw]
            if not roi.size:
                return
            else:
                return cv2.resize(roi, (48, 48)).astype(np.int16)
    def _wall_menu_scroll_point(self):
        """Wheel target inside the builder popup body (authoring space, per aspect)."""
        advisor = getattr(self, '_advisor', None)
        if advisor is not None and advisor._menu_scroll_point is not None:
            return advisor._menu_scroll_point
        else:
            ref = _WALL_MENU_SCROLL_BASELINE.get(self.config.aspect_key)
            if ref is None:
                ref = _WALL_MENU_SCROLL_BASELINE[ASPECT_16_10]
            x, y = self.config.scale_point([ref[0], ref[1]])
            return (int(x), int(y))
    def _should_upgrade_walls(self):
        """True when a wall pass is worth what it costs.\n\nA pass is expensive: it opens the builder menu, OCR-scrolls to find the Wall row,\nbatches walls and confirms — far longer than a raid cycle. So the threshold the\nuser set (*Settings → Wall upgrade threshold*) is the gate, and the HUD numbers\ndecide against it.\n\nA full-storage icon on its own is NOT enough to spend that time: those fire from\n~85% of a storage and say nothing about what a wall costs. The icons only stand\nin when the HUD cannot be read, and when the threshold is 0 — which the UI\ndefines as \"only upgrade when storages are full\".\n"""
        frame = self.window.screenshot()
        if frame is None:
            return False
        else:
            self._update_config_size(frame)
            gx, _gy = VisionService.find_active_hgoldfull(frame)
            ex, _ey = VisionService.find_active_helixirfull(frame)
            icons_full = gx is not None or ex is not None
            threshold = int(getattr(self, '_wall_upgrade_threshold', 0) or 0)
            if threshold <= 0:
                if icons_full:
                    logger.info('Wall upgrades: full-storage icon detected (threshold is 0)')
                return icons_full
            else:
                triplet = self._read_hud_triplet_stable()
                if triplet is None:
                    if icons_full:
                        logger.info('Wall upgrades: HUD unreadable but a full-storage icon shows — running the pass')
                    return icons_full
                else:
                    gold, elixir, _dark = triplet
                    self._last_wall_hud = (gold, elixir)
                    self._last_hud_triplet = triplet
                    if gold >= threshold or elixir >= threshold:
                        logger.info('Wall upgrades: loot gold=%s elixir=%s reached threshold %s', gold, elixir, threshold)
                        return True
                    else:
                        logger.info('Wall upgrades: gold=%s elixir=%s below threshold %s — skipping the pass%s', gold, elixir, threshold, ' (full-storage icon ignored)' if icons_full else '')
                        return False
    def _deselect_wall_ui(self):
        '''Tap empty ground to close the wall selection bar / gem dialog remnants. The wall
bar has no X button — while it is open, the next tap anywhere is swallowed by the
deselect, which would eat the upcoming Attack click.'''
        frame = self.window.screenshot()
        if frame is not None and getattr(frame, 'size', 0) > 0:
            self._update_config_size(frame)
            rkind, rlx, rly = self._find_reload_button(frame)
            if rlx:
                logger.warning('Wall upgrade cleanup: reload dialog (%s) detected — clicking (%d, %d)', rkind, rlx, rly)
                self.input.click(rlx, rly, pause=1.0)
                self._wait_for_village_after_reload()
                return
        self._dismiss_open_popup()
        self.input.click(pause = 0.35, *self.config.get_point('empty'))
        self._dismiss_open_popup()
    def _maybe_upgrade_walls(self, upgrade_walls):
        """Upgrade walls on home when enabled and loot is high enough (threshold or full storages)."""
        if not upgrade_walls or not self._should_upgrade_walls():
            return None
        else:
            cb = getattr(self, '_status_callback', None)
            if cb:
                cb('Upgrading walls...')
            if getattr(self, '_last_hud_triplet', None) is None:
                self._refresh_home_resources()
            for attempt in range(2):
                self._check_stop()
                try:
                    done = self._upgrade_walls()
                except InterruptedError:
                    raise
                except Exception:
                    logger.warning('Wall upgrade: pass %d errored; %s', attempt + 1, 'retrying' if attempt == 0 else 'giving up this round', exc_info=True)
                    done = False
                self._deselect_wall_ui()
                if done:
                    break
                if attempt == 0:
                    # the builder panel read failed — re-ground on home before retrying
                    self._home_screen_recovery()
    def _maybe_auto_research(self, force=False):
        mode = getattr(self, '_auto_upgrade_mode', MODE_OFF)
        if mode == MODE_OFF:
            return
        else:
            try:
                self._check_stop()
                if getattr(self, '_research_advisor', None) is None:
                    self._research_advisor = ResearchAdvisor(self.window, self.input, self.vision, self.config, self.stop_event)
                result = self._research_advisor.run_pass(mode, force=force)
                if result is not None:
                    logger.info('Laboratory: %s', result.note)
                    cb = getattr(self, '_status_callback', None)
                    if cb:
                        cb('Laboratory: ' + result.note)
                    self._emit_state(note=result.note, lab=result.lab)
                return result
            except InterruptedError:
                raise
            except Exception:
                logger.warning('Laboratory check failed; research deferred', exc_info=True)
                return None
    def _maybe_auto_upgrade(self, force=False):
        """Spend across free builders, refreshing budgets instead of full scans."""
        # ***<module>.Bot._maybe_auto_upgrade: Failure: Different bytecode
        mode = getattr(self, '_auto_upgrade_mode', MODE_OFF)
        if mode == MODE_OFF:
            return
        else:
            now = time.monotonic()
            live = mode in LIVE_UPGRADE_MODES
            if not live and (not force) and (now - getattr(self, '_auto_upgrade_last_scan', 0) < _AUTO_UPGRADE_SCAN_INTERVAL_SECONDS):
                return
            else:
                self._auto_upgrade_last_scan = now
                self._upgrade_retry_pending = False
                latest = None
                retries = 0
                try:
                    if getattr(self, '_advisor', None) is None:
                        self._advisor = UpgradeAdvisor(self.window, self.input, self.vision, self.config, self.stop_event)
                    for _attempt in range(12 if live else 1):
                        self._check_stop()
                        latest = self._advisor.run_pass(mode, reserve_builders=getattr(self, '_reserve_builders', 1), order=getattr(self, '_upgrade_order', None))
                        cb = getattr(self, '_status_callback', None)
                        if cb and latest is not None:
                                cb('Upgrades: ' + latest.note)
                        self._emit_state(note=latest.note if latest else None, builders=latest.scan.chip if latest and latest.scan else None)
                        if not live or latest is None:
                            return latest
                        else:
                            if latest.retryable:
                                retries += 1
                                if retries >= 3:
                                    self._upgrade_retry_pending = True
                                    return latest
                                else:
                                    if self.stop_event.wait(0.6):
                                        self._check_stop()
                                    continue
                            else:
                                retries = 0
                                if latest.pick is None and (not latest.collecting):
                                        return latest
                    self._upgrade_retry_pending = True
                    return latest
                except InterruptedError:
                    raise
                except Exception:
                    self._upgrade_retry_pending = live
                    logger.warning('Auto-upgrade pass failed; waiting before another attack', exc_info=True)
                    return latest
    def _clan_assistant(self):
        """The session\'s single ClanAssistant (holds the failure counter that\ndisables the feature on a mis-calibrated screen)."""
        if getattr(self, '_clan', None) is None:
            self._clan = ClanAssistant(self.window, self.input, self.vision, self.config, self.stop_event, self._clan_options, status_callback=getattr(self, '_status_callback', None))
        return self._clan
    def _clan_elixir_ok(self, opts):
        """True when there is enough elixir on the HUD to be giving troops away.\n\nRead before the clan menu is opened — the menu covers the HUD, and a donation\nthe user cannot afford to replace is worse than a late one.\n\nAn unreadable HUD does NOT block donating: this is a courtesy floor, not a\nspend guard (nothing here can spend anything), and the reading needs OCR, which\nis absent on installs without Tesseract. It is logged either way.\n"""
        floor = int(getattr(opts, 'min_elixir', 0) or 0)
        if floor <= 0:
            return True
        else:
            triplet = self._read_hud_triplet_stable()
            if triplet is None:
                logger.info('Clan assist: no trustworthy HUD reading — donating without the elixir check (the bars animate for a second or two after a raid; earlier lines say if a reading was rejected)')
                return True
            else:
                elixir = triplet[1]
                if elixir >= floor:
                    return True
                else:
                    logger.info('Clan assist: elixir %s is below the %s floor — skipping donations this pass', elixir, floor)
                    cb = getattr(self, '_status_callback', None)
                    if cb:
                        cb(f'Clan: elixir {self._fmt_resource(elixir)} below {self._fmt_resource(floor)} — not donating yet')
                    return False
    def _maybe_clan_assist(self):
        """Interval-gated clan chat visit: donate to open requests, ask for\nreinforcements. Home Village only, and called from the home screen — the chat\npanel would swallow the Attack tap if it were opened any later.\n\nDonations and requests are independent clocks (troops are asked for far less\noften than clanmates ask for them), but a pass that is due on either clock\nopens the chat once and does both errands that are due. Like the upgrade pass,\na failure here must never take down the farm loop."""
        opts = getattr(self, '_clan_options', None)
        if opts is None or not opts.enabled or getattr(self, '_builder_base', False):
            return None
        now = time.monotonic()
        donate_due = opts.donate and (opts.donate_interval_s <= 0 or now - getattr(self, '_clan_last_donate', _CLAN_CLOCK_NEVER) >= opts.donate_interval_s)
        request_due = opts.request and now - getattr(self, '_clan_last_request', _CLAN_CLOCK_NEVER) >= opts.request_interval_s
        if not donate_due and (not request_due):
            return
        missing = missing_templates(donate=donate_due, request=request_due, donate_troop=opts.donate_troop, have_chat_point=opts.chat_point is not None)
        if missing:
            self._clan_last_donate = now + _CLAN_UNCALIBRATED_BACKOFF_SECONDS
            self._clan_last_request = now + _CLAN_UNCALIBRATED_BACKOFF_SECONDS
            msg = f'Clan assist: missing template(s) {", ".join(missing)} — capture them in Settings → Clan assist.'
            logger.warning(msg)
            cb = getattr(self, '_status_callback', None)
            if cb:
                cb(msg)
            return None
        if donate_due and (not self._clan_elixir_ok(opts)):
            donate_due = False
            self._clan_last_donate = now
            if not request_due:
                return
        try:
            assistant = self._clan_assistant()
            if assistant.disabled:
                return None
            cb = getattr(self, '_status_callback', None)
            if cb:
                cb('Clan chat: donating...' if donate_due else 'Clan chat: requesting troops...')
            result = assistant.run_pass(donate=donate_due, request=request_due)
            if result.opened:
                if donate_due:
                    self._clan_last_donate = now
                if request_due:
                    self._clan_last_request = now
            if cb:
                cb(f'Clan: {result.note}')
            return result
        except InterruptedError:
            raise
        except Exception:
            logger.warning('Clan assist pass failed; continuing farm loop', exc_info=True)
            return None

    def _emit_state(self, state=None, builders=None, lab=None, storages=None, note=None, skipped=None, raid_loot=None):
        """Push a live-state update to the UI panel (best-effort, never raises)."""
        cb = getattr(self, '_state_callback', None)
        if not cb:
            return
        else:
            try:
                cb({'state': state, 'builders': builders, 'lab': lab, 'storages': storages, 'note': note, 'skipped': skipped, 'raid_loot': raid_loot})
            except Exception:
                logger.debug('State callback failed', exc_info=True)
    def _read_state_stable(self, attempts = 3):
        def _capture():
            frame = self.window.screenshot()
            if frame is not None and frame.size:
                    self._update_config_size(frame)
            return frame
        return read_village_state_stable(_capture, self.stop_event.wait, attempts)
    def _read_hud_triplet_stable(self):
        def _capture():
            frame = self.window.screenshot()
            if frame is not None and frame.size:
                self._update_config_size(frame)
            return frame
        return read_hud_triplet_stable(_capture, self.stop_event.wait)
    @staticmethod
    def _fmt_resource(value):
        value = int(value)
        if value >= 1000000:
            return f'''{value / 1000000:.1f}M'''
        if value >= 10000:
            return f'''{value / 1000:.0f}k'''
        return str(value)
    def _emit_village_state(self, state, mode_label):
        if state is None:
            return None
        # Storages field: real OCR'd amounts (from the last stable loot snapshot) plus
        # the game's bar-fill flags. The flat-swatch "full" templates fire from ~90%
        # up, so they are labeled "~full", never "full".
        flags = []
        if state.gold_full:
            flags.append('gold ~full')
        if state.elixir_full:
            flags.append('elixir ~full')
        triplet = getattr(self, '_last_hud_triplet', None)
        if triplet is not None:
            storages = f'''{self._fmt_resource(triplet[0])} / {self._fmt_resource(triplet[1])} / {self._fmt_resource(triplet[2])}'''
            if flags:
                storages += f''' ({', '.join(flags)})'''
        else:
            storages = ', '.join(flags) if flags else 'filling'
        self._emit_state(state = mode_label, builders = state.builders, lab = state.lab, storages = storages)
    def _wait_for_upgrade_state(self, deadline):
        while getattr(self, '_upgrade_retry_pending', False):
            self._check_stop()
            if deadline and time.time() >= deadline:
                return False
            self._emit_state(note='waiting for readable upgrade balances/builders')
            if self.stop_event.wait(2):
                return False
            self._home_screen_recovery()
            self._maybe_auto_upgrade(force=True)
        return True

    def _maybe_idle(self, deadline):
        """The \"run until maxed\" idle gate. When both main storages are full and a\nforced upgrade pass could not spend anything, attacking earns nothing — wait\nin place (game session kept alive via recovery on each wake) until a builder\nfrees up or loot is spent, instead of stopping or attacking pointlessly.\n\nDark elixir has no full-storage indicator; while gold/elixir still have room\nthe normal farm loop is what fills DE. Pet House state is invisible on the\nhome screen (no HUD chip, no builder-popup row — verified live), so pets are\nthe user\'s manual job and never block idling; the status line calls out an\nidle lab when its first suggested research cannot be started."""
        # ***<module>.Bot._maybe_idle: Failure: Different control flow
        if getattr(self, '_auto_upgrade_mode', MODE_OFF) not in LIVE_UPGRADE_MODES:
            return
        else:
            if not self._wait_for_upgrade_state(deadline):
                return
            else:
                frame = self.window.screenshot()
                if frame is None:
                    return
                else:
                    self._update_config_size(frame)
                    state = read_village_state(frame)
                    self._emit_village_state(state, 'farming')
                    if state is None:
                        return
                    else:
                        state = self._read_state_stable()
                        if state is None or not state.storages_full:
                            return None
                        else:
                            if not getattr(self, '_gold_pinned', False) or not getattr(self, '_elixir_pinned', False):
                                logger.info('Idle gate: storages ~full but still filling (gold pinned=%s, elixir pinned=%s) — farming on', getattr(self, '_gold_pinned', False), getattr(self, '_elixir_pinned', False))
                                return
                            else:
                                reserve = getattr(self, '_reserve_builders', 1)
                                cb = getattr(self, '_status_callback', None)
                                logger.info('Idle gate: nothing startable with storages full (%s) — pausing attacks, rechecking every %ds', state.summary(), _IDLE_RECHECK_SECONDS)
                                while True:
                                    self._check_stop()
                                    if deadline and time.time() >= deadline:
                                        return
                                    else:
                                        msg = f'Idling — {(state.summary() if state else "state unreadable")}; recheck in {_IDLE_RECHECK_SECONDS // 60}m'
                                        if state is not None and state.lab_idle:
                                                msg += ' — lab idle; first suggested research deferred'
                                        if cb:
                                            cb(msg)
                                        self._emit_village_state(state, 'idling')
                                        if self.stop_event.wait(_IDLE_RECHECK_SECONDS):
                                            return
                                        else:
                                            self._home_screen_recovery()
                                            self._maybe_clan_assist()
                                            self._maybe_auto_research(force=True)
                                            state = self._read_state_stable()
                                            if state is None:
                                                continue
                                            if not state.storages_full:
                                                if state.builders is None:
                                                    logger.info('Idle: degraded read (%s) — staying idle', state.summary())
                                                    continue
                                                else:
                                                    logger.info('Idle over: storages have room again (%s)', state.summary())
                                            else:
                                                free = state.free_builders
                                                if free is not None and free > reserve:
                                                    logger.info('Idle over: %d builder(s) free (%s)', free, state.summary())
                                                else:
                                                    continue
                                            self._auto_upgrade_last_scan = 0
                                            self._maybe_auto_upgrade(force=True)
                                            if not self._wait_for_upgrade_state(deadline):
                                                return
                                            else:
                                                if cb:
                                                    cb('Resuming — ' + state.summary())
                                                self._emit_village_state(state, 'farming')
                                                return None
    def _upgrade_walls_pick_resource_and_okay(self):
        """Settled frame → click an affordable resource slot → Okay. If neither resource can\npay, close the popup instead (never confirm a red cost)."""
        frame, settled = self._wall_batch_state()
        if frame is None:
            return
        else:
            if settled is None:
                logger.info('Wall upgrade: batch popup gone before the confirm — nothing bought this pass')
                _dump_debug_frame(frame, 'wallfail')
                self._deselect_wall_ui()
                return
            else:
                self._update_config_size(frame)
                pair = VisionService.upgrade_cost_redness_by_resource_icons(frame)
                for _ in range(2):
                    if pair.gold.cost_roi_xywh or pair.elixir.cost_roi_xywh:
                        break
                    else:
                        if self.stop_event.wait(0.4):
                            return
                        else:
                            frame = self.window.screenshot()
                            if frame is None:
                                return
                            else:
                                self._update_config_size(frame)
                                pair = VisionService.upgrade_cost_redness_by_resource_icons(frame)
                picked = False
                if pair.elixir.redness < 0.2 and pair.elixir.cost_roi_xywh and pair.elixir.center:
                    logger.info('Wall upgrade: paying with elixir (redness %.2f)', pair.elixir.redness)
                    self.input.click(*pair.elixir.center, pause=0.3, rand=False)
                    picked = True
                else:
                    if pair.gold.redness < 0.2 and pair.gold.cost_roi_xywh and pair.gold.center:
                                logger.info('Wall upgrade: paying with gold (redness %.2f)', pair.gold.redness)
                                self.input.click(*pair.gold.center, pause=0.3, rand=False)
                                picked = True
                frame = self.window.screenshot()
                if frame is None:
                    return
                else:
                    self._update_config_size(frame)
                    if not picked:
                        logger.info('Wall upgrade: no affordable resource slot (gold %.2f, elixir %.2f) — dismissing without confirming', pair.gold.redness, pair.elixir.redness)
                        if not pair.gold.cost_roi_xywh and (not pair.elixir.cost_roi_xywh):
                                try:
                                    import time as _time
                                    import cv2 as _cv2
                                    from app.utils.common import get_user_app_data_dir
                                    dbg = get_user_app_data_dir() / 'debug'
                                    dbg.mkdir(parents=True, exist_ok=True)
                                    path = dbg / f'wallfail_{int(_time.time())}.jpg'
                                    _cv2.imwrite(str(path), frame, [_cv2.IMWRITE_JPEG_QUALITY, 88])
                                    logger.info('Wall upgrade: confirm-time frame saved to %s', path)
                                except Exception:
                                    logger.debug('Wall upgrade: debug frame dump failed', exc_info=True)
                        self._deselect_wall_ui()
                    else:
                        ox, oy = self.vision.find_template(frame, 'okay.png')
                        if ox:
                            self.input.click(ox, oy, pause=0.3, rand=False)
                            logger.info('Wall upgrade batch confirmed')
    def _wall_batch_state(self, polls=_WALL_POPUP_SETTLE_POLLS):
        """Fresh frame plus its cost-button reading, once the batch popup is readable.\n\nReturns ``(frame, pair)``, or ``(frame, None)`` when the popup stayed unreadable.\n\nOne frame is not evidence. The popup animates for around a second after every add\nor remove, and its cost icons do not match while it does — so treating the first\nunreadable frame as \"the popup is closed\" ended batches one frame after a\nsuccessful add and threw the whole batch away unconfirmed (live repro: \"cost\nbuttons not visible — popup not open\" immediately after \"1 wall(s) added\").\n"""
        frame = None
        for _ in range(max(1, int(polls))):
            self._check_stop()
            frame = self.window.screenshot()
            if frame is not None:
                self._update_config_size(frame)
                pair = VisionService.upgrade_cost_redness_by_resource_icons(frame)
                if pair.gold.cost_roi_xywh or pair.elixir.cost_roi_xywh:
                    return (frame, pair)
            if self.stop_event.wait(0.5):
                return (frame, None)
        return (frame, None)
    def _wall_button_strip_roi(self, frame, pair):
        """ROI over the batch popup\'s button row — Remove Wall, Add Wall, the two costs.\n\nAnchored on the cost text, because that is the one part of the popup that is read\nreliably. The add/remove art is a nine-pixel-wide sliver at the authoring\nresolution, so on a small window it matches dozens of places at 0.75+ — including\nthe \"Wall (Level 14) x 1\" title one row above the buttons. The rightmost /\nleftmost tie-break inside those finders then picks exactly that false hit: live at\n1299x731 both Add Wall and Remove Wall resolved to (620, 519), 63 px above the\nreal buttons, so the add clicks sailed over the top of the popup while the batch\ncounted them as added. Nothing above the cost text is a button.\n"""
        fh, fw = frame.shape[:2]
        top = fh // 2
        rois = []
        if pair is not None:
            rois = [r for r in [pair.gold.cost_roi_xywh, pair.elixir.cost_roi_xywh] if r]
        if rois:
            top = max(top, min((max(fh // 2, int(r[1]) - int(r[3])) for r in rois)))
        if pair is not None and pair.gold.center and pair.elixir.center:
            pitch = int(pair.elixir.center[0]) - int(pair.gold.center[0])
            if pitch >= int(fw * 0.04):
                x0 = max(0, int(pair.gold.center[0] - 2.45 * pitch))
                x1 = min(fw, int(pair.gold.center[0] - 0.55 * pitch))
                if x1 > x0:
                    return (x0, top, x1 - x0, max(1, fh - top))
        return (int(fw * 0.18), top, int(fw * 0.56), max(1, fh - top))
    def _wall_cost_signature(self, frame, pair):
        """The batch price as pixels. It changes with every wall added or removed, so it\nis how a click that landed is told from one the popup swallowed.\n\nWidened around the cost ROI on purpose: that ROI is a 41x9 slice of the middle of\nthe number, and a batch going from 2,000,000 to 4,000,000 changes only the leading\ndigit, which falls outside it — three adds in a row read as \"nothing happened\"\nwhile the popup counted happily up to x4. Three ROI widths across covers the whole\nprice, and two settled frames of an untouched popup differ by exactly 0.00, so\nthere is no noise to leave headroom for.\n"""
        if frame is None or pair is None:
            return None
        else:
            fh, fw = frame.shape[:2]
            parts = []
            for slot in (pair.gold, pair.elixir):
                roi = getattr(slot, 'cost_roi_xywh', None)
                if not roi:
                    continue
                else:
                    x, y, w, h = (int(roi[0]), int(roi[1]), max(1, int(roi[2])), max(1, int(roi[3])))
                    crop = frame[max(0, y - h):min(fh, y + 2 * h), max(0, x - w):min(fw, x + 2 * w)]
                    if crop.size:
                        parts.append(cv2.resize(crop, (72, 24)).astype(np.int16))
            return parts or None
    @staticmethod
    def _wall_cost_changed(before, after):
        """True when the two price readings differ (or either could not be taken — an\nunreadable price is not evidence that the click was lost)."""
        if not before or not after or len(before) != len(after):
            return True
        else:
            return any((float(np.mean(np.abs(a - b))) >= _WALL_COST_CHANGE_DIFF for b, a in zip(before, after)))
    def _wait_for_wall_batch_popup(self, polls=_WALL_POPUP_SETTLE_POLLS):
        """True once the batch popup is up, judged by its Add Wall button."""
        frame = None
        for _ in range(max(1, int(polls))):
            self._check_stop()
            frame, pair = self._wall_batch_state(polls=1)
            if frame is not None and pair is not None:
                strip = self._wall_button_strip_roi(frame, pair)
                if VisionService.find_active_addwall(frame, region=strip)[0]:
                    return True
            if self.stop_event.wait(0.4):
                return False
        _dump_debug_frame(frame if frame is not None else None, 'wallbatch')
        return False
    def _add_one_wall(self, frame, pair, strip_roi):
        """Put one wall into the batch, verified. Returns ``(frame, pair, added)``.\n\nVerified for the same reason removing is: the popup animates for about a second\nafter each add and a click sent into that window is dropped silently. Counting the\nclicks instead of the walls is what let a pass report \"3 wall(s) added to batch\"\nand buy one.\n"""
        for attempt in range(_WALL_ADD_ATTEMPTS):
            awx, awy = VisionService.find_active_addwall(frame, region=strip_roi)
            if not awx:
                return (frame, pair, False)
            else:
                before = self._wall_cost_signature(frame, pair)
                self.input.click(awx, awy, pause=_WALL_ADD_CLICK_PAUSE, rand=False)
                new_frame, new_pair = self._wall_batch_state()
                if new_pair is None:
                    return (new_frame if new_frame is not None else frame, pair, False)
                else:
                    after = self._wall_cost_signature(new_frame, new_pair)
                    frame, pair = (new_frame, new_pair)
                    if self._wall_cost_changed(before, after):
                        return (frame, pair, True)
                    else:
                        logger.info('Wall upgrade: add click %d/%d left the batch unchanged — retrying', attempt + 1, _WALL_ADD_ATTEMPTS)
                        _dump_debug_frame(frame, 'walladd')
        return (frame, pair, False)
    def _remove_one_wall(self, frame, pair, mid_roi, why):
        """Take one wall back out of the batch, verified. Returns the new (frame, pair).\n\nVerified because a single click is not reliable here: one sent while the popup is\nstill animating is swallowed silently (live repro: the count stayed at x2 and the\nbatch was discarded, then the same click at the same spot worked a second later).\n"""
        logger.info('Wall upgrade: removing one wall — %s', why)
        for attempt in range(_WALL_REMOVE_ATTEMPTS):
            rwx, rwy = VisionService.find_active_removewall(frame, region=mid_roi)
            if not rwx:
                logger.info('Wall upgrade: no Remove Wall button to click')
                _dump_debug_frame(frame, 'wallremove')
                return (frame, pair)
            else:
                self.input.click(rwx, rwy, pause=0.4, rand=False)
                new_frame, new_pair = self._wall_batch_state()
                if new_pair is None:
                    return (new_frame if new_frame is not None else frame, pair)
                else:
                    frame, pair = (new_frame, new_pair)
                    if pair.gold.redness < 0.2 or pair.elixir.redness < 0.2:
                        return (frame, pair)
                    else:
                        logger.info('Wall upgrade: batch still unaffordable after remove %d/%d', attempt + 1, _WALL_REMOVE_ATTEMPTS)
        return (frame, pair)
    def _upgrade_walls(self):
        """Open builder menu, scroll to Wall, add walls → remove if both red → Okay."""
        if getattr(self, '_advisor', None) is None:
            self._advisor = UpgradeAdvisor(self.window, self.input, self.vision, self.config, self.stop_event)
        if not self._advisor._open_builder_popup():
            logger.info('Wall upgrade: builder list / Buildings & Heroes panel unavailable — skipping this pass')
            return
        if self.stop_event.wait(_WALL_MENU_OPEN_SECONDS):
            return
        self._last_wall_unit_cost = None
        wall_pt = None
        candidate = None
        scrolls = 0
        signature = None
        stalls = 0
        for _ in range(_WALL_MENU_SCROLL_STEPS * 2):
            self._check_stop()
            frame, found = self._find_wall_row_once()
            if found:
                if candidate and abs(found[1] - candidate[1]) <= self.config.scale_scalar(_WALL_ROW_STABLE_TOL):
                    wall_pt = found
                    if scrolls:
                        logger.info('Wall upgrade: Wall row found after %d scroll nudge(s)', scrolls)
                    break
                candidate = found
                if self.stop_event.wait(0.25):
                    return
            else:
                candidate = None
                new_signature = self._wall_menu_signature(frame)
                if signature is not None and new_signature is not None:
                    if float(np.mean(np.abs(new_signature - signature))) < _WALL_MENU_BOTTOM_DIFF:
                        stalls += 1
                        if stalls >= 2:
                            logger.info('Wall upgrade: reached the end of the builder list after %d nudge(s) with no Wall row', scrolls)
                            break
                else:
                    stalls = 0
                signature = new_signature
                scrolls += 1
                if scrolls >= _WALL_MENU_SCROLL_STEPS:
                    break
                self.input.scroll(*self._wall_menu_scroll_point(), _WALL_MENU_WHEEL_CLICKS)
                if self.stop_event.wait(0.5):
                    return
        if not wall_pt:
            logger.info('Wall upgrade: no Wall row in the builder list — skipping this pass')
            self._deselect_wall_ui()
            return
        self.input.click(*wall_pt, pause=0.6, rand=False)
        umx, umy = (None, None)
        frame = None
        for poll_idx in range(6):
            self._check_stop()
            frame = self.window.screenshot()
            if frame is None:
                return
            self._update_config_size(frame)
            fh, fw = frame.shape[:2]
            bar_roi = (int(fw * 0.18), int(fh * 0.72), int(fw * 0.62), int(fh * 0.25))
            umx, umy = self.vision.find_template(frame, 'upgrademore.png', threshold=0.72, region=bar_roi)
            if not umx and poll_idx == 5:
                umx, umy = find_ui_label(frame, self.vision, ('Upgrade More', 'More'), region=bar_roi, min_confidence=50)
            if umx:
                break
            if self.stop_event.wait(0.4):
                self._check_stop()
                return
        if not umx:
            logger.info('Wall upgrade: upgrademore.png not visible after Wall row click — dismissing and skipping this pass')
            if frame is not None:
                try:
                    import time as _time
                    import cv2 as _cv2
                    from app.utils.common import get_user_app_data_dir
                    dbg = get_user_app_data_dir() / 'debug'
                    dbg.mkdir(parents=True, exist_ok=True)
                    path = dbg / f'wallrow_{int(_time.time())}.jpg'
                    _cv2.imwrite(str(path), frame, [_cv2.IMWRITE_JPEG_QUALITY, 88])
                    logger.info('Wall upgrade: post-click frame saved to %s', path)
                except Exception:
                    logger.debug('Wall upgrade: wallrow debug dump failed', exc_info=True)
            self._dismiss_okay_or_exit_on_frame(frame)
            return
        self.input.click(umx, umy, pause=0.4, rand=False)
        if not self._wait_for_wall_batch_popup():
            logger.info('Wall upgrade: the batch popup never opened after Upgrade More — dismissing')
            self._deselect_wall_ui()
            return
        if self.stop_event.wait(_WALL_POPUP_ANIMATION_SECONDS):
            return
        added = 0
        max_adds = _WALL_BATCH_MAX_ADDS
        unit_cost = getattr(self, '_last_wall_unit_cost', None)
        hud_pair = getattr(self, '_last_wall_hud', None)
        if hud_pair is None and getattr(self, '_last_hud_triplet', None) is not None:
            hud_pair = self._last_hud_triplet[:2]
        if unit_cost and unit_cost > 0 and hud_pair is not None:
            best_budget = max(int(hud_pair[0] or 0), int(hud_pair[1] or 0))
            if best_budget > 0:
                budget_adds = max(0, best_budget // int(unit_cost) - 1)
                max_adds = min(max_adds, budget_adds)
                logger.info('Wall upgrade: unit_cost=%s, max_budget=%s -> max additional walls=%d (batch=%d)', unit_cost, best_budget, max_adds, max_adds + 1)
        for _ in range(max_adds):
            self._check_stop()
            frame, pair = self._wall_batch_state()
            if pair is None:
                logger.info('Wall upgrade: batch popup unreadable after %d polls — stopping batch', _WALL_POPUP_SETTLE_POLLS)
                _dump_debug_frame(frame, 'wallbatch')
                break
            if pair.gold.redness >= 0.2 and pair.elixir.redness >= 0.2:
                break
            strip_roi = self._wall_button_strip_roi(frame, pair)
            frame, pair, ok = self._add_one_wall(frame, pair, strip_roi)
            if not ok:
                break
            added += 1
        self._check_stop()
        frame, pair = self._wall_batch_state()
        if pair is None:
            logger.info('Wall upgrade: batch popup unreadable before confirming — dismissing')
            _dump_debug_frame(frame, 'wallbatch')
            self._deselect_wall_ui()
            return
        mid_roi = self._wall_button_strip_roi(frame, pair)
        if pair.gold.redness >= 0.2 and pair.elixir.redness >= 0.2:
            frame, pair = self._remove_one_wall(frame, pair, mid_roi, 'unaffordable on both resources')
        elif added > 1 and pair.elixir.redness >= 0.2 and pair.gold.redness < 0.2 and pair.gold.cost_roi_xywh:
            frame, pair = self._remove_one_wall(frame, pair, mid_roi, 'gold is the payer — keeping the attack entry fee')
        logger.info('Wall upgrade: %d wall(s) added to batch', added)
        self._upgrade_walls_pick_resource_and_okay()
        return True
    def _find_reload_button(self, frame):
        """Return ``(kind, x, y)`` when a reload dialog is visible, or ``(None, None, None)``.
        ``reload_game.png`` is the Google Play Games inactivity dialog ('Anyone there? You have
        been disconnected due to inactivity. RELOAD GAME') and ``try_again.png`` is the server
        disconnect dialog ('Connection lost... TRY AGAIN'); both should be clicked immediately.
        ``reload.png`` is the legacy connection-lost / another-device button."""
        if frame is None or getattr(frame, 'size', 0) == 0:
            return (None, None, None)
        if get_template_path('reload_game.png').exists():
            lx, ly = self.vision.find_template(frame, 'reload_game.png')
            if lx:
                return ('inactivity', lx, ly)
        if get_template_path('try_again.png').exists():
            lx, ly = self.vision.find_template(frame, 'try_again.png')
            if lx:
                return ('try_again', lx, ly)
        if get_template_path('reload.png').exists():
            lx, ly = self.vision.find_template(frame, 'reload.png')
            if lx:
                return ('connection', lx, ly)
        return (None, None, None)

    def _wait_for_village_after_reload(self, timeout=30):
        """Wait for the Supercell loading screen to finish after clicking RELOAD / RELOAD GAME."""
        if self.stop_event.wait(2.0):
            return False
        deadline = time.time() + max(2.0, float(timeout) - 2.0)
        while time.time() < deadline:
            self._check_stop()
            frame = self.window.screenshot()
            if frame is not None and getattr(frame, 'size', 0) > 0:
                self._update_config_size(frame)
                rkind, rlx, rly = self._find_reload_button(frame)
                if rlx:
                    self.input.click(rlx, rly, pause=1.0)
                    continue
                self._dismiss_okay_or_exit_on_frame(frame)
                bot_roi = VisionService.bottom_half_region(frame)
                ax, _ = self.vision.find_template(frame, 'attack.png', region=bot_roi)
                if ax or self._detect_village_type(frame) is not None or is_village_hud(frame, self.vision):
                    if self.stop_event.wait(1.0):
                        return False
                    frame2 = self.window.screenshot()
                    if frame2 is not None and getattr(frame2, 'size', 0) > 0:
                        self._dismiss_okay_or_exit_on_frame(frame2)
                    return True
            if self.stop_event.wait(1.0):
                return False
        return False

    def _dismiss_okay_or_exit_on_frame(self, frame):
        '''If ``okay.png``, ``exit.png``, or a known dialog-specific control is visible,
        click it. Returns True if dismissed. The Daily Reward popup (blocked the bot a
        whole night) must be CLAIMED before its X works ("Claim reward first!"), so the
        green Claim button outranks its grey X; both have their own art (exit.png does
        not match).'''
        if get_template_path('rankedattackconfirm.png').exists():
            rx, ry = self.vision.find_template(frame, 'rankedattackconfirm.png')
            if rx:
                h, w = frame.shape[:2]
                cancel_x = max(10, min(w - 10, w - int(rx)))
                logger.info('Dismissing Confirm Attack modal via Cancel at (%d, %d)', cancel_x, int(ry))
                self.input.click(cancel_x, int(ry), pause=0.25)
                return True
        names = ['okay.png', 'exit.png']
        for extra in ('claim_btn.png', 'dailyreward_x.png', 'needgold_x.png', 'panel_x.png'):
            if get_template_path(extra).exists():
                names.append(extra)
        for name in names:
            (x, y) = self.vision.find_template(frame, name)
            if not x:
                continue
            self.input.click(x, y, pause = 0.15)
            return True
        return False

    def _wait_for_attack_with_nudge(self, timeout=10, error=True):
        """Poll bottom-half ``attack.png``; dismiss ``okay`` / ``exit`` popups first; else empty + scroll."""
        start = time.time()
        while time.time() - start < timeout:
            self._check_stop()
            frame = self.window.screenshot()
            if frame is None:
                if self.stop_event.wait(0.5):
                    return (None, None)
                else:
                    continue
            self._update_config_size(frame)
            rkind, rlx, rly = self._find_reload_button(frame)
            if rlx:
                logger.warning('Attack wait: reload dialog (%s) detected — clicking RELOAD', rkind)
                self.input.click(rlx, rly, pause=1.0)
                if not self._wait_for_village_after_reload() and self.stop_event.is_set():
                    return (None, None)
                start = time.time()
                continue
            if self._dismiss_okay_or_exit_on_frame(frame):
                if self.stop_event.wait(0.25):
                    return (None, None)
                else:
                    continue
            bot_roi = VisionService.bottom_half_region(frame)
            search_region = self._search_region_for_template(frame, 'attack.png', None, None, 200)
            ax, ay = self.vision.find_template(frame, 'attack.png', region=search_region)
            if ax:
                return (ax, ay)
            sx, sy = self._match_ui_control(frame, 'surrender.png', region=bot_roi)
            if sx:
                logger.warning('Attack wait: live battle detected (surrender visible) — leaving it to recovery')
                return (None, None)
            ax, ay = self._match_ui_control(frame, 'attack.png', region=search_region)
            if ax:
                return (ax, ay)
            self._nudge_view_to_reveal_attack()
            if self.stop_event.wait(0.35):
                self._check_stop()
                return (None, None)
        if error:
            logger.warning('Timeout waiting for attack.png')
        return (None, None)
    def _run_loop(self, method_id, duration_seconds, star_bonus=False, ranked_fill=False, upgrade_walls=False):
        start_time = time.time()
        if self.stop_event.wait(1):
            self._check_stop()
            return None
        frame = self.window.screenshot()
        self._update_config_size(frame)
        empty_pt = self.config.get_point('empty')
        self.input.click(empty_pt[0], empty_pt[1], **{'pause': 0.2})
        self.input.scroll(*self._scroll_point(), 20)
        delay = random.uniform(0.1, 0.3)
        if self.stop_event.wait(delay):
            self._check_stop()
            return None
        if star_bonus and self._is_star_bonus_claimed():
            logger.info('Star bonus mode: no star bonus template matched on home — nothing to collect. Finishing without attacks.')
            return None
        deadline = start_time + duration_seconds if duration_seconds else None
        self._maybe_clan_assist()
        self._maybe_auto_research()
        self._maybe_upgrade_walls(upgrade_walls)
        self._maybe_auto_upgrade()
        self._maybe_idle(deadline)
        cb = getattr(self, '_status_callback', None)
        troop_failures = 0
        while deadline is None or time.time() < deadline:
            self._check_stop()
            outcome = self._find_match_and_attack(method_id, ranked_fill)
            self._check_stop()
            if outcome != 'no_attack':
                self._return_home()
                self._check_stop()
            self._home_screen_recovery()
            self._check_stop()
            # random between-round settle (anti-pattern timing); skipped when a
            # wall/upgrade pass follows anyway
            if self.stop_event.wait(random.uniform(0.4, 1.1)):
                self._check_stop()
                return None
            if outcome == 'ranked_limit':
                return None
            if outcome == 'troop':
                troop_failures += 1
                if troop_failures >= _TROOP_FAILURE_LIMIT:
                    if deadline is None:
                        msg = f'Troops not found {troop_failures} times in a row — backing off {_IDLE_RECHECK_SECONDS // 60}m, then retrying (run until maxed).'
                        logger.warning(msg)
                        if cb:
                            cb(msg)
                        troop_failures = 0
                        if self.stop_event.wait(_IDLE_RECHECK_SECONDS):
                            return None
                        self._home_screen_recovery()
                        continue
                    msg = f'Troops not found {troop_failures} times in a row — stopping. Check the army / saved recipe.'
                    logger.warning(msg)
                    if cb:
                        cb(msg)
                    return None
                msg = f'Troops not found ({troop_failures}/{_TROOP_FAILURE_LIMIT}) — retrying in {_TROOP_RETRY_WAIT_SECONDS}s.'
                logger.warning(msg)
                if cb:
                    cb(msg)
                self._multi_run_collect_home_village_resources()
                if self.stop_event.wait(_TROOP_RETRY_WAIT_SECONDS):
                    return None
            else:
                troop_failures = 0
            self._maybe_clan_assist()
            self._maybe_auto_research()
            self._maybe_upgrade_walls(upgrade_walls)
            self._maybe_auto_upgrade()
            self._maybe_idle(deadline)
            self.input.scroll(*self._scroll_point(), 5)
            if self.stop_event.wait(random.uniform(0.15, 0.25)):
                return None
            if star_bonus and self._is_star_bonus_claimed():
                logger.info('Star bonus claimed (star icons no longer visible). Stopping.')
                return None
        return None
    def _run_builder_base_loop(self, duration_seconds, star_bonus = False):
        '''Builder Base farming: Attack → Find Now → Baby Dragon deploy → end battle or surrender → Return Home.'''
        start_time = time.time()
        cb = getattr(self, '_status_callback', None)
        if self.stop_event.wait(1):
            return None
        frame = self.window.screenshot()
        if not frame is None:
            self._update_config_size(frame)
        empty_pt = self.config.get_point('empty')
        self.input.click(pause = 0.2, *empty_pt)
        self.input.scroll(*self._scroll_point(), 20)
        if self.stop_event.wait(random.uniform(0.1, 0.3)):
            return None
        if star_bonus and self._is_bb_star_bonus_finished():
            msg = 'Builder Base star bonus: bstar.png not visible — nothing to collect. Finishing without attacks.'
            logger.info(msg)
            if cb:
                cb(msg)
            return None
        while time.time() - start_time < duration_seconds:
            self._check_stop()
            self.input.scroll(*self._scroll_point(), 20)
            if self.stop_event.wait(random.uniform(0.1, 0.25)):
                return None
            (ax, ay) = self._wait_for_image('attack.png', timeout = 10)
            if not ax:
                logger.warning('Builder Base: attack.png not found')
                continue
            self.input.click(ax, ay, pause = 0.15)
            (fx, fy) = self._wait_for_image('findnow.png', timeout = 10)
            if not fx:
                logger.warning('Builder Base: findnow.png not found')
                continue
            self.input.click(fx, fy, pause = 0.15)
            (bx, by) = self._wait_for_image('babydragon.png', timeout = 30, error = False)
            if not bx:
                msg = 'Builder Base: babydragon.png not found after Find Now'
                logger.warning(msg)
                if cb:
                    cb(msg)
                continue
            frame = self.window.screenshot()
            if frame is None:
                continue
            self._update_config_size(frame)
            (h, w) = frame.shape[:2]
            cy = h // 2
            cx = w // 2
            self.input.move(cx, cy)
            if self.stop_event.wait(0.05):
                return None
            self.input.scroll(cx, cy, 4)
            if self.stop_event.wait(0.15):
                return None
            frame = self.window.screenshot()
            if frame is None:
                continue
            self._update_config_size(frame)
            if not self._deploy_bb_baby_dragons(frame):
                logger.warning('Builder Base: babydragon.png lost after zoom nudge / deploy failed')
                continue
            if self._loot_prioritise == 'elixir':
                self._bb_surrender_and_return_home()
            elif self._loot_prioritise == 'gold':
                self._bb_gold_prioritise_end_battle_and_return_home()
            else:
                self._bb_end_battle_and_return_home()
            self._bb_collect_elixir_cart_after_attack()
            if star_bonus and self._is_bb_star_bonus_finished():
                logger.info('Builder Base star bonus finished (bstar.png no longer visible). Stopping.')
                return None
            if self.stop_event.wait(random.uniform(0.35, 0.6)):
                return None
        return None


    def _bb_pan_down_left_from_center(self):
        '''Pan BB view down-left from screen center (~500px) to reveal the boat / elixir cart.'''
        self._check_stop()
        frame = self.window.screenshot()
        if frame is None or frame.size == 0:
            return None
        self._update_config_size(frame)
        (h, w) = frame.shape[:2]
        cx = w // 2 + random.randint(-25, 25)
        cy = h // 2 + random.randint(-25, 25)
        cx = max(8, min(w - 8, cx))
        cy = max(8, min(h - 8, cy))
        self.input.move(cx, cy, 0)
        self.input.mouse_up(cx, cy)
        if self.stop_event.wait(0.06):
            self._check_stop()
        step = 500
        x2 = max(8, min(w - 8, cx - step))
        y2 = max(8, min(h - 8, cy + step))
        self.input.mouse_down(cx, cy)
        self.input.human_move(cx, cy, x2, y2, duration = random.uniform(0.35, 0.55))
        self.input.mouse_up(x2, y2)
        if self.stop_event.wait(0.45):
            self._check_stop()
            return None
    def _find_bb_full_elixir_cart(self, attempts = 3):
        '''Up to ``attempts`` screenshots; try fullecart templates in order (top-right quadrant).'''
        empty_pt = self.config.get_point('empty')
        for _ in range(attempts):
            self._check_stop()
            frame = self.window.screenshot()
            if not frame is None:  # [recovered: decompiler turned this `if` into a `while` and made the return unconditional]
                self._update_config_size(frame)
                roi = VisionService.top_right_quadrant_region(frame)
                for template in _BB_FULL_ELIXIR_CART_TEMPLATES:
                    (x, y) = self.vision.find_template(frame, template, threshold = 0.8, region = roi)
                    if x:
                        return (x, y)
            self.input.click(pause = 0.15, *empty_pt)
        return (None, None)
    def _bb_collect_elixir_cart_after_attack(self):
        '''After a BB raid: dismiss popups, pan to cart, collect full elixir cart if visible.'''
        if self.stop_event.wait(0.35):
            return None
        self.input.click(pause = 0.15, *self.config.get_point('empty'))
        if self.stop_event.wait(0.15):
            return None
        self._bb_pan_down_left_from_center()
        (cx, cy) = self._find_bb_full_elixir_cart(attempts = 3)
        if cx:
            self.input.click(cx, cy, pause = 0.2)
            (coll_x, coll_y) = self._wait_for_image(_BB_COLLECT_TEMPLATE, timeout = 8, error = False)
            if coll_x:
                self.input.click(coll_x, coll_y, pause = 0.2)
            else:
                logger.warning('Builder Base: collect.png not found after elixir cart')
        else:
            logger.debug('Builder Base: fullecart not visible — skipping cart collect')
        self.input.click(pause = 0.15, *self.config.get_point('empty'))
    def _deploy_bb_baby_dragons(self, frame):
        '''Find all Baby Dragon icons, deploy on diamond edges, wait, then re-click saved icons.'''
        strategy = AttackStrategy(self.input, self.vision, self.config, self.stop_event)
        roi = VisionService.bottom_half_region(frame)
        centers = VisionService.find_all_template_centers(frame, 'babydragon.png', region = roi, max_matches = 12, suppress_pad_frac = _BB_TEMPLATE_SUPPRESS_PAD)
        if not centers:
            return False
        deploy_count = len(centers)
        self.input.click(centers[0][0], centers[0][1], pause = 0.3, rand = False)
        if self.stop_event.wait(0.2):
            return True
        for px, py in strategy._even_diamond_top_perimeter_points(frame, deploy_count, reserve_top_corner_slot = True):
            self._check_stop()
            self.input.click(px, py, pause = _EDRAG_DELAY, rand = False)
        self._deploy_bb_battle_or_flying_machine(strategy)
        if self._loot_prioritise != 'elixir':
            if self.stop_event.wait(_BB_BABY_DRAGON_RECLICK_WAIT):
                return True
            for x, y in centers:
                self._check_stop()
                self.input.click(x, y, pause = 0.15, rand = False)
        return True
    def _deploy_bb_battle_or_flying_machine(self, strategy):
        '''One bar lookup: Battle Machine, else Flying Machine; deploy on a random diamond edge.'''
        frame = self.window.screenshot()
        if frame is None:
            return None
        self._update_config_size(frame)
        roi = VisionService.bottom_half_region(frame)
        (tx, ty) = self.vision.find_template(frame, _BB_BATTLE_MACHINE_TEMPLATE, region = roi)
        if not tx:
            (tx, ty) = self.vision.find_template(frame, _BB_FLYING_MACHINE_TEMPLATE, region = roi)
        if not tx:
            logger.debug('Builder Base: %s / %s not found on troop bar', _BB_BATTLE_MACHINE_TEMPLATE, _BB_FLYING_MACHINE_TEMPLATE)
            return None
        (px, py) = strategy._random_diamond_perimeter_point(frame)
        self.input.click(tx, ty, pause = 0.3, rand = False)
        if self.stop_event.wait(0.2):
            return None
        self.input.click(px, py, pause = _EDRAG_DELAY, rand = False)
    def _bb_end_battle_and_return_home(self):
        '''Wait for natural battle end, dismiss Okay, then tap BB Return Home.'''
        (ex, ey) = self._wait_for_image('endbattle.png', timeout = 90, error = False)
        if ex:
            self.input.click(ex, ey, pause = 0.15)
        else:
            logger.warning('Builder Base: endbattle.png not found')
            return None
        self._bb_dismiss_okay_and_return_home()
    def _bb_gold_prioritise_end_battle_and_return_home(self):
        """Gold prioritise: wait for 2+ battle stars, then End Battle → Okay → Return Home."""
        start = time.time()
        timeout = 90
        end_battle_clicked = False
        while time.time() - start < timeout:
            self._check_stop()
            frame = self.window.screenshot()
            if frame is None:
                if self.stop_event.wait(0.5):
                    return
                continue
            self._update_config_size(frame)
            bot_roi = VisionService.bottom_half_region(frame)
            stars = VisionService.find_all_template_centers(frame, _BB_BATTLE_STAR_TEMPLATE, threshold=0.8, region=bot_roi, max_matches=3, suppress_pad_frac=0.35)
            if len(stars) >= _BB_GOLD_PRIORITISE_MIN_STARS:
                bot_roi = VisionService.bottom_half_region(frame)
                ex, ey = self._match_ui_control(frame, 'endbattle.png', region=bot_roi)
                if ex:
                    logger.info('Builder Base (gold): %d bbstar.png — clicking endbattle', len(stars))
                    self.input.click(ex, ey, pause=0.15)
                    end_battle_clicked = True
                    break
            if self.stop_event.wait(0.5):
                return
        if not end_battle_clicked:
            logger.warning('Builder Base (gold): timed out waiting for %d+ %s', _BB_GOLD_PRIORITISE_MIN_STARS, _BB_BATTLE_STAR_TEMPLATE)
            return
        self._bb_dismiss_okay_and_return_home()

    def _bb_return_home_template_for_screen(self, width, height):
        '''Return (template name, scale_template) for Builder Base Return Home.'''
        (tw, th) = _BB_1080P_CAPTURE_SIZE
        tol = _BB_1080P_SIZE_TOLERANCE
        if abs(width - tw) <= tol and abs(height - th) <= tol:
            return (_BB_RETURN_HOME_TEMPLATE_1080P, False)
        return (_BB_RETURN_HOME_TEMPLATE, True)
    def _wait_for_bb_return_home(self, timeout = 15):
        '''Wait for BB Return Home; picks 1080p-native template when capture is ~1920x1080.'''
        start = time.time()
        template_name = _BB_RETURN_HOME_TEMPLATE
        while time.time() - start < timeout:
            self._check_stop()
            frame = self.window.screenshot()
            if frame is None:
                continue
            self._update_config_size(frame)
            (h, w) = frame.shape[:2]
            (template_name, scale_template) = self._bb_return_home_template_for_screen(w, h)
            search_region = self._search_region_for_template(frame, template_name, None, None, 200)
            (x, y) = self.vision.find_template(frame, template_name, threshold = 0.8, region = search_region, scale_template = scale_template)
            if x:
                return (x, y, template_name)
            if self.stop_event.wait(0.5):
                return (None, None, template_name)
        return (None, None, template_name)
    def _bb_dismiss_okay_and_return_home(self, *, retry_battle_template = 'endbattle.png', max_retries = _BB_RETURN_HOME_END_BATTLE_RETRIES):
        '''Dismiss Okay, tap Return Home; retry battle-end + Okay if Return Home stays hidden.'''
        return_home_template = _BB_RETURN_HOME_TEMPLATE
        for attempt in range(max_retries + 1):
            self._check_stop()
            if attempt > 0:
                (bx, by) = self._wait_for_image(retry_battle_template, timeout = 3, error = False)
                if bx:
                    self.input.click(bx, by, pause = 0.15)
                else:
                    logger.debug('Builder Base: %s retry %d/%d — control not visible', retry_battle_template, attempt, max_retries)
            okay_timeout = 10 if attempt == 0 else 5
            (ox, oy) = self._wait_for_image('okay.png', timeout = okay_timeout, error = False)
            if ox:
                self.input.click(ox, oy, pause = 0.15)
            (rx, ry, return_home_template) = self._wait_for_bb_return_home(timeout = 15)
            if rx:
                self.input.click(rx, ry, pause = 0.2)
                if attempt > 0:
                    logger.info('Builder Base: return home after %d %s retry(ies)', attempt, retry_battle_template)
                return None
            if not attempt < max_retries:
                continue
            logger.info('Builder Base: %s not found — retrying %s + okay (%d/%d)', return_home_template, retry_battle_template, attempt + 1, max_retries)
        logger.warning('Builder Base: %s not found after %d %s retries', return_home_template, max_retries, retry_battle_template)
    def _bb_surrender_and_return_home(self):
        '''Elixir prioritise: surrender right after deploy, then Okay + Return Home.'''
        (sx, sy) = self._wait_for_image('surrender.png', timeout = 10, error = False)
        if sx:
            self.input.click(sx, sy, pause = 0.15)
        else:
            logger.warning('Builder Base (elixir): surrender.png not found')
            return None
        self._bb_dismiss_okay_and_return_home(retry_battle_template = 'surrender.png')


    def _select_battle_mode_button(self, frame, battle_template, ranked_fill=False):
        """Select the 'Find a Match' button for regular Battle vs Ranked Battle.

        Because templates/16_9/farmbattle.png and templates/16_10/farmbattle.png
        only crop the orange 'Find a Match' button text, both the regular Battle
        card (left) and Ranked Battle card (right/middle when tournament attacks
        are available) match farmbattle.png with nearly identical scores. When
        multiple 'Find a Match' buttons are visible on the Multiplayer screen:
        - regular farming (ranked_fill=False) is the leftmost 'Find a Match' button.
        - Ranked Battle (ranked_fill=True) is the rightmost 'Find a Match' button.
        """
        if frame is None or getattr(frame, 'size', 0) == 0:
            return (None, None)
        self._update_config_size(frame)
        bot_roi = VisionService.bottom_half_region(frame)
        centers = VisionService.find_all_template_centers(
            frame,
            'farmbattle.png',
            threshold=0.78,
            region=bot_roi,
            max_matches=4,
            suppress_pad_frac=0.35,
        )
        if len(centers) >= 2:
            centers_sorted = sorted(centers, key=lambda pt: pt[0])
            chosen = centers_sorted[-1] if ranked_fill else centers_sorted[0]
            logger.info(
                'Battle selection: %d Find-a-Match buttons %s -> picking %s (ranked_fill=%s)',
                len(centers_sorted),
                centers_sorted,
                chosen,
                ranked_fill,
            )
            return chosen
        search_region = self._search_region_for_template(frame, battle_template, None, None, 200)
        return self._match_ui_control(frame, battle_template, region=search_region)

    def _wait_for_battle_mode_button(self, battle_template, ranked_fill=False, timeout=12):
        """Poll for the Battle Selection 'Find a Match' button and disambiguate Battle vs Ranked Battle."""
        fx, fy = self._wait_for_image(battle_template, timeout=timeout, error=False)
        if not fx:
            return (None, None)
        frame = self.window.screenshot()
        if frame is not None and getattr(frame, 'size', 0) > 0:
            sx, sy = self._select_battle_mode_button(frame, battle_template, ranked_fill=ranked_fill)
            if sx:
                return (sx, sy)
        return (fx, fy)

    def _find_match_and_attack(self, method_id, ranked_fill=False):
        """One attack cycle. Returns ``'ranked_limit'`` (stop now), ``'troop'`` (troops missing — retry later), or None."""
        battle_template = 'rankedbattle.png' if ranked_fill else 'farmbattle.png'
        frame0 = self.window.screenshot()
        if frame0 is not None and getattr(frame0, 'size', 0) > 0:
            self._update_config_size(frame0)
            if self._dismiss_okay_or_exit_on_frame(frame0):
                self.stop_event.wait(0.3)
                frame0 = self.window.screenshot()
                if frame0 is not None and getattr(frame0, 'size', 0) > 0:
                    self._update_config_size(frame0)
            ax0, _ = (None, None)
            if frame0 is not None and getattr(frame0, 'size', 0) > 0:
                ax0, _ = self.vision.find_template(frame0, 'attack.png', region=VisionService.bottom_half_region(frame0))
            fx, fy = (None, None) if ax0 else self._select_battle_mode_button(frame0, battle_template, ranked_fill=ranked_fill)
        else:
            fx, fy = (None, None)
        if fx:
            logger.info('Attack: battle-selection screen already open — continuing from Find a Match')
        else:
            ax, ay = self._wait_for_attack_with_nudge()
            if not ax:
                return 'no_attack'
            self._refresh_home_resources()
            self.input.click(ax, ay, pause=0.6)
            fx, fy = self._wait_for_battle_mode_button(battle_template, ranked_fill=ranked_fill, timeout=12)
            for _ in range(3):
                if fx:
                    break
                frame_retry = self.window.screenshot()
                if frame_retry is not None and getattr(frame_retry, 'size', 0) > 0:
                    self._update_config_size(frame_retry)
                    self._dismiss_okay_or_exit_on_frame(frame_retry)
                    rkind, rlx, rly = self._find_reload_button(frame_retry)
                    if rlx:
                        logger.warning('Attack open: reload dialog (%s) detected — clicking RELOAD', rkind)
                        self.input.click(rlx, rly, pause=0.5)
                        self._wait_for_village_after_reload()
                        return 'no_attack'
                    bot_roi = VisionService.bottom_half_region(frame_retry)
                    rax, ray = self.vision.find_template(frame_retry, 'attack.png', region=bot_roi)
                    if not rax:
                        rax, ray = find_battle_button(frame_retry, self.vision)
                    if rax:
                        logger.info('attack.png still on screen — click was eaten (popup/selection?); tapping empty and re-clicking')
                        self.input.click(*self.config.get_point('empty'), pause=0.15)
                        self.input.click(rax, ray, pause=0.6)
                fx, fy = self._wait_for_battle_mode_button(battle_template, ranked_fill=ranked_fill, timeout=10)
        if not fx:
            logger.warning('Battle mode button (%s) not recognized after clicking Attack — recovering to Home screen instead of crashing', battle_template)
            self._dismiss_open_popup()
            return 'no_attack'
        if ranked_fill:
            frame = self.window.screenshot()
            if find_ui_label(frame, self.vision, 'Join Tournament')[0] is not None:
                msg = 'Ranked Battle requires joining a tournament in the game before farming.'
                logger.info(msg)
                cb = getattr(self, '_status_callback', None)
                if cb:
                    cb(msg)
                return 'ranked_limit'
        self.input.click(fx, fy, pause=0.45)
        a2x, a2y = self._wait_for_image('attack2.png', timeout=5, error=False)
        for _ in range(3):
            if a2x:
                break
            frame_rf = self.window.screenshot()
            rfx, rfy = self._select_battle_mode_button(frame_rf, battle_template, ranked_fill=ranked_fill)
            if not rfx:
                break
            logger.info('%s still on screen — click was eaten (popup?); re-clicking', battle_template)
            self.input.click(rfx, rfy, pause=0.45)
            a2x, a2y = self._wait_for_image('attack2.png', timeout=5, error=False)
        if not a2x:
            a2x, a2y = self._wait_for_image('attack2.png', error=False)
        if not a2x:
            logger.warning('Army review Attack button (attack2.png) not recognized — recovering to Home screen instead of crashing')
            self._dismiss_open_popup()
            return 'no_attack'
        if method_id == 3 and (not self._ensure_valkyrie_army_from_recipes()):
            msg = 'Valkyrie army not confirmed (slow screen or missing saved recipe) — attacking with current army'
            logger.warning(msg)
            cb = getattr(self, '_status_callback', None)
            if cb:
                cb(msg)
        if a2x:
            if self.stop_event.wait(0.2):
                self._check_stop()
                return 'no_attack'
            self.input.click(a2x, a2y, pause=0.5)
            if ranked_fill:
                rx, ry = self._wait_for_image('rankedattackconfirm.png', timeout=10)
                if not rx:
                    logger.warning('rankedattackconfirm.png not found after attack2.png')
                else:
                    self.input.click(rx, ry, pause=0.3)
            else:
                rx, ry = self._wait_for_image('rankedattackconfirm.png', timeout=1.5, error=False)
                if rx:
                    logger.info('Confirm Attack modal appeared after attack2.png — clicking Attack! (%d, %d)', rx, ry)
                    self.input.click(rx, ry, pause=0.3)
        live_x, _ = self._wait_for_any_image(('endbattle.png', 'surrender.png'), timeout=6, error=False)
        for _ in range(3):
            if live_x is not None:
                break
            frame_bf = self.window.screenshot()
            if frame_bf is not None and getattr(frame_bf, 'size', 0) > 0:
                self._update_config_size(frame_bf)
                rkind, rlx, rly = self._find_reload_button(frame_bf)
                if rlx:
                    logger.warning('Attack launch: reload dialog (%s) detected — clicking RELOAD', rkind)
                    self.input.click(rlx, rly, pause=0.5)
                    self._wait_for_village_after_reload()
                    return 'no_attack'
                rx_c, ry_c = self.vision.find_template(frame_bf, 'rankedattackconfirm.png')
                if rx_c:
                    logger.info('Confirm Attack modal (rankedattackconfirm.png) visible during battlefield wait — clicking Attack!')
                    self.input.click(rx_c, ry_c, pause=0.3)
                ox, oy = self.vision.find_template(frame_bf, 'okay.png')
                if ox:
                    logger.info('Confirmation popup (okay.png) visible after Attack! — clicking Okay')
                    self.input.click(ox, oy, pause=0.3)
                a2_region = self._search_region_for_template(frame_bf, 'attack2.png', None, None, 200)
                ra2x, ra2y = self._match_ui_control(frame_bf, 'attack2.png', region=a2_region)
                if ra2x:
                    logger.info('attack2.png still on screen — click was eaten (animation/popup?); re-clicking')
                    self.input.click(ra2x, ra2y, pause=0.5)
                    rx, ry = self._wait_for_image('rankedattackconfirm.png', timeout=3 if ranked_fill else 1.5, error=False)
                    if rx:
                        self.input.click(rx, ry, pause=0.3)
                else:
                    rfx, rfy = self._select_battle_mode_button(frame_bf, battle_template, ranked_fill=ranked_fill)
                    if rfx:
                        logger.info('%s still on screen during battlefield wait — re-clicking', battle_template)
                        self.input.click(rfx, rfy, pause=0.45)
                        ra2x, ra2y = self._wait_for_image('attack2.png', timeout=5, error=False)
                        if ra2x:
                            self.input.click(ra2x, ra2y, pause=0.5)
                            rx, ry = self._wait_for_image('rankedattackconfirm.png', timeout=3 if ranked_fill else 1.5, error=False)
                            if rx:
                                self.input.click(rx, ry, pause=0.3)
            live_x, _ = self._wait_for_any_image(('endbattle.png', 'surrender.png'), timeout=10, error=False)
        if live_x is None:
            live_x, _ = self._wait_for_any_image(('endbattle.png', 'surrender.png'), timeout=15)
        if live_x is None:
            frame_timeout = self.window.screenshot()
            _dump_debug_frame(frame_timeout, 'battlefield_timeout')
            logger.warning(
                'Battlefield could not be confirmed after Attack (clicks: battle=%s, attack2=%s) — recovering to Home screen instead of crashing',
                (fx, fy),
                (a2x, a2y),
            )
            self._dismiss_open_popup()
            return 'no_attack'
        if not ranked_fill:
            self._skip_low_loot_bases()
        frame = self.window.screenshot()
        if frame is None:
            return
        self._update_config_size(frame)
        h, w = frame.shape[:2]
        cy = h // 2
        cx = w // 2
        self.input.move(cx, cy)
        if self.stop_event.wait(0.05):
            self._check_stop()
            return
        self.input.scroll(cx, cy, 3)
        if self.stop_event.wait(0.2):
            self._check_stop()
            return
        frame = self.window.screenshot()
        if frame is None:
            return
        self._update_config_size(frame)
        strategy = self._get_strategy(method_id)
        strategy.event_handler = self._battle_rewards
        self._begin_raid(ranked=ranked_fill)
        result = strategy.execute(frame, self.stop_event)
        self._check_stop()
        if result is False:
            bx, by = self._wait_for_any_image(('endbattle.png', 'surrender.png'), timeout=5, error=False)
            if bx:
                self.input.click(bx, by, pause=0.2)
            return 'troop'
        self._wait_for_battle_end(is_sneaky=method_id == 1)
        return None

    def _skip_low_loot_bases(self):
        """On the battle-prep screen: press Next while the base holds less than the\nconfigured minimum loot.\n\nEach Next costs another search fee, so the number of skips per cycle is capped\nand the cap is the user\'s (Settings → Minimum loot to attack). Two things\ndeliberately fall through to attacking rather than stalling the farm loop: a\nloot panel that cannot be read (OCR missing — the filter is an optimisation,\nnot a safety rail) and a missing Next button.\n"""
        lf = getattr(self, '_loot_filter', None)
        if lf is None or not lf.enabled or getattr(self, '_loot_filter_off', False):
            return None
        else:
            if not get_template_path(TEMPLATE_NEXT_BASE).exists():
                self._loot_filter_off = True
                msg = f'Loot filter: {TEMPLATE_NEXT_BASE} not captured — attacking every base. Capture it in Settings → Clan assist → Capture templates.'
                logger.warning(msg)
                cb = getattr(self, '_status_callback', None)
                if cb:
                    cb(msg)
                return None
            else:
                for _ in range(max(1, int(lf.max_skips))):
                    self._check_stop()
                    loot = self._read_base_loot()
                    if loot is None:
                        logger.info('Loot filter: could not read this base loot (see the LootFilter lines) — attacking it')
                        return
                    else:
                        gold, elixir, dark = loot
                        if meets(loot, lf):
                            logger.info('Loot filter: base holds %s gold / %s elixir / %s dark — attacking', gold, elixir, dark)
                            return
                        else:
                            confirm = self._read_base_loot(polls=2)
                            if confirm is None or meets(confirm, lf):
                                logger.info('Loot filter: second read of this base disagreed (%s vs %s) — attacking it', loot, confirm)
                                return
                            else:
                                frame = self.window.screenshot()
                                if frame is None:
                                    return
                                else:
                                    self._update_config_size(frame)
                                    nx, ny = self.vision.find_template(frame, TEMPLATE_NEXT_BASE)
                                    if not nx:
                                        logger.warning('Loot filter: %s gold / %s elixir is below %s but Next was not found — attacking this base', gold, elixir, lf.summary())
                                        return
                                    else:
                                        self._bases_skipped = getattr(self, '_bases_skipped', 0) + 1
                                        logger.info('Loot filter: skipping a base with %s gold / %s elixir (want %s) — %d skipped this session', gold, elixir, lf.summary(), self._bases_skipped)
                                        cb = getattr(self, '_status_callback', None)
                                        if cb:
                                            cb(f'Skipping base: {self._fmt_resource(gold)} gold / {self._fmt_resource(elixir)} elixir')
                                        self._emit_state(skipped=self._bases_skipped)
                                        self.input.click(nx, ny, pause=0.4)
                                        if not self._wait_for_next_base():
                                            raise RuntimeError('Loot filter: the next base did not load; deployment stopped until the battlefield is verified.')
                logger.info('Loot filter: %d skips used on this cycle — attacking this base', lf.max_skips)
    def _battle_prep_visible(self, frame):
        """True while the battle-prep controls (End Battle / Surrender) are on screen."""
        bot_roi = VisionService.bottom_half_region(frame)
        for name in ('endbattle.png', 'surrender.png'):
            x, _y = self.vision.find_template(frame, name, region=bot_roi)
            if x:
                return True
        for name in ('endbattle.png', 'surrender.png'):
            x, _y = self._match_ui_control(frame, name, region=bot_roi)
            if x:
                return True
        return False
    def _wait_for_next_base(self):
        """After pressing Next: let the old base clear, then wait for the new one.\n\nLive repro: the loot read fired ~1s after Next, while the *old* battle-prep\nscreen was still up, so it came back unreadable and the bot attacked a base that\nhad not finished loading — \"Troop sneaky not found!\", because the deploy bar had\nnot rendered either. Waiting for the controls to disappear first is what makes\nthe wait that follows mean \"the next base\", not \"the last one\".\n"""
        cleared = False
        for _ in range(_NEXT_CLEAR_POLLS):
            self._check_stop()
            frame = self.window.screenshot()
            if frame is not None and getattr(frame, 'size', 0):
                    self._update_config_size(frame)
                    if not self._battle_prep_visible(frame):
                        cleared = True
                        break
            if self.stop_event.wait(0.5):
                return False
        if not cleared:
            return False
        else:
            sx, _sy = self._wait_for_any_image(('endbattle.png', 'surrender.png'), timeout=30, error=False)
            if not sx:
                return False
            else:
                if self.stop_event.wait(_NEXT_SETTLE_SECONDS):
                    return False
                else:
                    return True
    def _read_base_loot(self, polls=_LOOT_READ_POLLS, samples=_LOOT_READ_SAMPLES):
        """OCR the enemy loot panel and fold several reads into one.\n\nRetries while the panel has not rendered yet, then keeps reading until it has\n``samples`` successful reads to combine — a single read of this panel is wrong\nabout half the time on a live client (dropped digit: 705,559 read as 70,559),\nand one low read is the difference between skipping a good base and raiding it.\nReturns the combined triplet, or None once the polls are spent.\n"""
        reads = []
        for _ in range(max(1, int(polls))):
            frame = self.window.screenshot()
            if frame is not None:
                self._update_config_size(frame)
                loot = read_enemy_loot(frame)
                if loot is not None:
                    reads.append(loot)
                    if len(reads) >= max(1, int(samples)):
                        break
            if self.stop_event.wait(0.35):
                break
        return combine_reads(reads)
    def _get_strategy(self, method_id):
        cb = getattr(self, '_status_callback', None)
        eq = getattr(self, '_earthquake_method', EARTHQUAKE_METHOD_CURVE)
        if method_id == 1:
            return TroopSpamStrategy(self.input, self.vision, self.config, self.stop_event, 'sneaky', 15, status_callback = cb, earthquake_method = eq)
        if method_id == 2:
            return TroopSpamStrategy(self.input, self.vision, self.config, self.stop_event, 'superminion', 3.1, status_callback = cb, earthquake_method = eq)
        if method_id == 3:
            return TroopSpamStrategy(self.input, self.vision, self.config, self.stop_event, 'valkyrie', 5.5, status_callback = cb, earthquake_method = eq)
        if method_id == 4:
            return EdragStrategy(self.input, self.vision, self.config, self.stop_event, status_callback = cb, earthquake_method = eq)
        return TroopSpamStrategy(self.input, self.vision, self.config, self.stop_event, 'sneaky', 15, status_callback = cb, earthquake_method = eq)
    def _wait_for_battle_end(self, is_sneaky):
        if is_sneaky:
            if self.stop_event.wait(3):
                return None
            (sx, sy) = self._wait_for_image('surrender.png', timeout = 2, error = False)
            if sx:
                self.input.click(sx, sy, pause = 0.1)
                return None
            (bx, by) = self._wait_for_image('endbattle.png', timeout = 2, error = False)
            if bx:
                self.input.click(bx, by, pause = 0.1)
                return None
            return None
        # Non-sneaky (e.g. E-Drags): let the attack run up to 75s or until the
        # Victory/Defeat screen (returnhome.png / chestclaim.png) appears naturally.
        # Do NOT click endbattle.png immediately after deployment, because once all
        # troops/spells/abilities are spent, the bottom-left button changes from
        # Surrender to End Battle while E-Drags are still attacking.
        start = time.time()
        while time.time() - start < 75:
            self._check_stop()
            frame = self.window.screenshot()
            if frame is not None and not isinstance(frame, np.ndarray):
                self._update_config_size(frame)
                if self._battle_rewards.handle(frame):
                    continue
                bx, by = self.vision.find_template(frame, 'endbattle.png', region = self._search_region_for_template('endbattle.png', frame))
                if bx:
                    self.input.click(bx, by, pause = 0.1)
                return None
            if frame is not None and getattr(frame, 'size', 0) > 0:
                self._update_config_size(frame)
                if self._battle_rewards.handle(frame):
                    continue
                rkind, rlx, rly = self._find_reload_button(frame)
                if rlx:
                    logger.warning('Battle wait: reload dialog (%s) detected — clicking RELOAD', rkind)
                    self.input.click(rlx, rly, pause = 1.0)
                    self._wait_for_village_after_reload()
                    return None
                bot_roi = VisionService.bottom_half_region(frame)
                returnhome_tpls = ('returnhome.png', 'returnhome2.png') if self.config.aspect_key == ASPECT_16_10 else ('returnhome.png',)
                for tpl in returnhome_tpls:
                    rx, _ = self.vision.find_template(frame, tpl, region = bot_roi)
                    if rx:
                        return None
                cx, _ = self.vision.find_template(frame, 'chestclaim.png', region = bot_roi)
                if cx:
                    return None
            if self.stop_event.wait(1.0):
                self._check_stop()
                return None
        (bx, by) = self._wait_for_any_image(('endbattle.png', 'surrender.png'), timeout = 5, error = False)
        if bx:
            self.input.click(bx, by, pause = 0.1)
        return None
    def _return_home(self):
        '''Dismiss Okay if present, then wait for ``returnhome.png`` (+ ``returnhome2.png`` on 16:10) or ``chestclaim.png`` (mutually exclusive).'''
        (kind, hx, hy) = self._wait_for_return_home_or_chest_claim(timeout = 15)
        if kind == 'return' and hx:
            self.input.click(hx, hy, pause = 0.1)
            return True
        if kind == 'chest' and hx:
            logger.info('Post-battle UI: chestclaim.png (replacing return home); running chest flow')
            self.input.click(hx, hy, pause = 0.2)
            if self.stop_event.wait(0.35):
                self._check_stop()
                return True
            self._tap_empty_until_chest_continue()
            return True
        return False
    def _wait_for_return_home_or_chest_claim(self, timeout=15):
        """\nPoll one frame for ``okay.png`` (if surrendering/ending battle), ``returnhome.png`` (+ ``returnhome2.png`` on 16:10 only) then ``chestclaim.png``.\nReturns (``\"return\"`` | ``\"chest\"``, x, y) or (None, None, None) on timeout.\n"""
        start = time.time()
        while time.time() - start < timeout:
            self._check_stop()
            frame = self.window.screenshot()
            if frame is None:
                if self.stop_event.wait(0.5):
                    self._check_stop()
                    return (None, None, None)
                continue
            self._update_config_size(frame)
            if self._battle_rewards.handle(frame):
                continue
            rkind, rlx, rly = self._find_reload_button(frame)
            if rlx:
                logger.warning('Post-battle wait: reload dialog (%s) detected — clicking RELOAD', rkind)
                self.input.click(rlx, rly, pause=1.0)
                self._wait_for_village_after_reload()
                return (None, None, None)
            ox, oy = self.vision.find_template(frame, 'okay.png')
            if ox:
                self.input.click(ox, oy, pause=0.2)
                start = time.time()
                if self.stop_event.wait(0.4):
                    self._check_stop()
                    return (None, None, None)
                continue
            bot_roi = VisionService.bottom_half_region(frame)
            rx, ry = (None, None)
            returnhome_tpls = ('returnhome.png', 'returnhome2.png') if self.config.aspect_key == ASPECT_16_10 else ('returnhome.png',)
            for tpl in returnhome_tpls:
                rx, ry = self.vision.find_template(frame, tpl, region=bot_roi)
                if rx:
                    break
            if rx:
                self._record_raid_result(frame)
                return ('return', rx, ry)
            cx, cy = self.vision.find_template(frame, 'chestclaim.png')
            if cx:
                self._record_raid_result(frame)
                return ('chest', cx, cy)
            ax, _ = self.vision.find_template(frame, 'attack.png', region=bot_roi)
            if ax:
                self._finish_unread_raid('home reached directly after ending battle')
                return (None, None, None)
            for tpl in returnhome_tpls:
                rx, ry = self._match_ui_control(frame, tpl, region=bot_roi)
                if rx:
                    break
            if rx:
                self._record_raid_result(frame)
                return ('return', rx, ry)
            if self.stop_event.wait(0.4):
                self._check_stop()
                return (None, None, None)
        logger.warning('Timeout waiting for returnhome.png / returnhome2.png (16:10) or chestclaim.png')
        return (None, None, None)

    def _random_point_chest_tap_through(self):
        '''Random point near center-right of the capture for chest tap-through (±85 px from anchor).'''
        (w, h) = (self.config.width, self.config.height)  # [recovered: pycdc reversed STORE_FAST_STORE_FAST pairs]
        if w <= 1 or h <= 1:
            (w, h) = (self.config.ref_width, self.config.ref_height)
        cx = int(w * 0.75)
        cy = h // 2
        j = 85
        return (max(0, min(w - 1, cx + random.randint(-j, j))), max(0, min(h - 1, cy + random.randint(-j, j))))
    def _tap_empty_until_chest_continue(self):
        '''After ``chestclaim`` was clicked: tap center-right (±85px) until ``chestcontinue.png``, then click it (home).'''
        CHEST_TAP_TIMEOUT = 120
        deadline = time.time() + CHEST_TAP_TIMEOUT
        while time.time() < deadline:
            self._check_stop()
            (nx, ny) = self._random_point_chest_tap_through()
            self.input.click_at(nx, ny, rand = False)
            t0 = time.time()
            if self.stop_event.wait(0.15):
                return None
            frame2 = self.window.screenshot()
            if not frame2 is None:
                self._update_config_size(frame2)
                (tx, ty) = self.vision.find_template(frame2, 'chestcontinue.png')
                if tx:
                    logger.info('Chest reward: chestcontinue.png found; clicking (expect home village)')
                    self.input.click(tx, ty, pause = 0.25)
                    return None
            elapsed = time.time() - t0
            to_wait = max(0.05, 0.5 - elapsed)
            if self.stop_event.wait(to_wait):
                return None
        logger.warning(f'''chestcontinue.png not seen within {int(CHEST_TAP_TIMEOUT)}s after chest claim; continuing bot loop''')  # [recovered: decompiler misnested this inside the loop, ending the tap-through after one tap]
        return None
    def _is_star_bonus_claimed(self):
        '''True if neither emptystar nor glowstar matches strongly (bonus claimed / not shown).'''
        frame = self.window.screenshot()
        if frame is None:
            return False
        self._update_config_size(frame)
        for template in ('emptystar.png', 'glowstar.png'):
            (_, _, confidence) = self.vision.find_template_with_confidence(frame, template, threshold = 0)  # [recovered + verified against original bytecode]
            if confidence >= _STAR_BONUS_THRESHOLD:
                return False
        return True
    def _is_bb_star_bonus_finished(self):
        '''True when ``bstar.png`` is not visible in the bottom half (BB star bonus attacks done).'''
        frame = self.window.screenshot()
        if frame is None:
            return False
        self._update_config_size(frame)
        bot_roi = VisionService.bottom_half_region(frame)
        (_, _, confidence) = self.vision.find_template_with_confidence(frame, _BB_STAR_BONUS_TEMPLATE, threshold = 0.0, region = bot_roi)  # [recovered + verified against original bytecode]
        return confidence < _STAR_BONUS_THRESHOLD
    def _home_screen_recovery(self):
        """Ensures we are back at home screen. Escapes popups, stray screens, live battles, and end screens."""
        for step in range(8):
            self._check_stop()
            frame = self.window.screenshot()
            if frame is None:
                if self.stop_event.wait(1):
                    return
                else:
                    continue
            self._update_config_size(frame)
            if self._battle_rewards.handle(frame):
                continue
            rkind, lx, ly = self._find_reload_button(frame)
            if lx:
                if rkind in ('inactivity', 'try_again'):
                    logger.warning('Recovery: %s dialog detected — clicking immediately', rkind)
                    self.input.click(lx, ly, pause=1.0)
                    self._reload_first_seen = None
                    if not self._wait_for_village_after_reload() and self.stop_event.is_set():
                        return
                    continue
                now = time.monotonic()
                first = getattr(self, '_reload_first_seen', None)
                if first is None:
                    self._reload_first_seen = now
                    logger.warning('Recovery: connection-lost dialog detected — waiting %ds before reloading (another device may be using the account)', _RELOAD_GRACE_SECONDS)
                elif now - first >= _RELOAD_GRACE_SECONDS:
                    logger.warning('Recovery: connection-lost dialog persisted — clicking RELOAD')
                    self.input.click(lx, ly, pause=1.0)
                    self._reload_first_seen = None
                    if not self._wait_for_village_after_reload() and self.stop_event.is_set():
                        return
                if self.stop_event.wait(2):
                    return
                continue
            if self._dismiss_okay_or_exit_on_frame(frame):
                if self.stop_event.wait(0.3):
                    return
                else:
                    continue
            bot_roi = VisionService.bottom_half_region(frame)
            returnhome_tpls = ('returnhome.png', 'returnhome2.png') if self.config.aspect_key == ASPECT_16_10 else ('returnhome.png',)
            rx, ry = (None, None)
            for tpl in returnhome_tpls:
                rx, ry = self.vision.find_template(frame, tpl, region=bot_roi)
                if rx:
                    break
            if rx:
                self._record_raid_result(frame)
                logger.info('Recovery: clicking Return Home')
                self.input.click(rx, ry, pause=0.3)
                if self.stop_event.wait(0.5):
                    return
                continue
            sx, sy = self.vision.find_template(frame, 'surrender.png', region=bot_roi)
            if not sx:
                sx, sy = self.vision.find_template(frame, 'endbattle.png', region=bot_roi)
            if sx:
                logger.info('Recovery: live battle — surrendering to get home')
                self.input.click(sx, sy, pause=0.3)
                ox, oy = self._wait_for_image('okay.png', timeout=2, error=False)
                if ox:
                    self.input.click(ox, oy, pause=0.3)
                if self.stop_event.wait(0.5):
                    self._check_stop()
                    return
                continue
            ax, _ = self.vision.find_template(frame, 'attack.png', region=bot_roi)
            if ax or self._detect_village_type(frame) is not None or is_village_hud(frame, self.vision):
                self._reload_first_seen = None
                self._finish_unread_raid('home reached without a readable result screen')
                return
            if step % 3 == 0:
                sx, sy = self._match_ui_control(frame, 'surrender.png', region=bot_roi)
                if sx:
                    logger.info('Recovery: live battle — surrendering to get home')
                    self.input.click(sx, sy, pause=0.3)
                    ox, oy = self._wait_for_image('okay.png', timeout=2, error=False)
                    if ox:
                        self.input.click(ox, oy, pause=0.3)
                    if self.stop_event.wait(0.5):
                        return
                    else:
                        continue
                rx, ry = self._match_ui_control(frame, 'returnhome.png', region=bot_roi)
                if not rx and self.config.aspect_key == ASPECT_16_10:
                    rx, ry = self._match_ui_control(frame, 'returnhome2.png', region=bot_roi)
                if rx:
                    self._record_raid_result(frame)
                    logger.info('Recovery: clicking Return Home')
                    self.input.click(rx, ry, pause=0.3)
                    if self.stop_event.wait(0.5):
                        return
                    else:
                        continue
            self.input.click(*self.config.get_point('empty'), pause=0.2)
            if not self.stop_event.wait(0.6):
                pass
        return

    def _open_account_settings(self, timeout=10):
        """Open legacy Settings or the October Profile → Settings route.\n\nEvery click requires the visible old icon, recognized profile card, or\nSettings tab. A missing/unreadable route fails without selecting a guess.\n"""
        deadline = time.monotonic() + timeout
        profile_clicked = False
        while time.monotonic() < deadline:
            self._check_stop()
            frame = self.window.screenshot()
            if frame is None:
                if self.stop_event.wait(0.3):
                    self._check_stop()
                continue
            self._update_config_size(frame)
            stx, sty = self.vision.find_template(frame, 'settings.png')
            if not stx:
                stx, sty = find_settings_button(frame, self.vision)
            if stx is not None:
                self.input.click(stx, sty, pause=0.25)
                return True
            if not profile_clicked:
                px, py = find_profile_button(frame, self.vision)
                if px is not None:
                    self.input.click(px, py, pause=0.25)
                    profile_clicked = True
            if self.stop_event.wait(0.3):
                self._check_stop()
        logger.info('Multi-run: Profile / Settings route unreadable; account was not changed')
        return False

    def _switch_account_and_load_home(self, username):
        """Open Settings → Change user, OCR-click username, wait for home. Raises on failure."""
        cb = getattr(self, '_status_callback', None)
        msg = f'Multi-run: switching to {username!r}'
        logger.info(msg)
        if cb:
            cb(msg)
        if not self._open_account_settings():
            raise RuntimeError('Multi-run: Settings not found (open Profile → Settings and retry)')
        else:
            cux, cuy = self._wait_for_image('changeuser.png')
            if not cux:
                raise RuntimeError('Multi-run: change user button not found')
            else:
                self.input.click(cux, cuy, pause=1)
                ucx, ucy = self._wait_for_player_name(username, timeout=25, min_confidence=70, tesseract_config='--psm 11', match_alnum_only=True, fuzzy_min_ratio=0.8, white_text=True)
                if not ucx:
                    err = f'Multi-run: could not find username \"{username}\" on screen (OCR)'
                    logger.error(err)
                    if cb:
                        cb(err)
                    raise RuntimeError(err)
                else:
                    self.input.click(ucx, ucy, pause=0.2)
                    if self.stop_event.wait(1):
                        self._check_stop()
                    ax, ay = self._wake_home_and_wait_for_attack(timeout=30)
                    if not ax:
                        err = f'Multi-run: Home Village not ready after loading {username!r} (builder.png|gbuilder.png / attack.png timeout after login / leaving Builder Base)'
                        logger.error(err)
                        if cb:
                            cb(err)
                        raise RuntimeError(err)
                    else:
                        self._finish_unread_raid('account changed before a result could be recorded')
                        self._loot_account = username
                        self._advisor = None
                        self._research_advisor = None
                        self._upgrade_retry_pending = False
                        self._auto_upgrade_last_scan = 0
                        self._inventory_prev = self._last_hud_triplet = None
                        self._gold_pinned = self._elixir_pinned = False
    def _wake_home_and_wait_for_attack(self, timeout=30):
        """\nAfter switching accounts: dismiss idle UI, then poll the **top half** for village type\n(``mbuilder.png`` = Builder Base; ``builder.png`` or ``gbuilder.png`` = Home Village). If Builder Base,\nleave via :meth:`_leave_builder_base_with_nboat`. When Home Village is detected, return\n``attack.png`` coordinates from the **bottom half** (battle bar) once visible — same\ntemplate as :meth:`_find_match_and_attack` uses to start battles.\nEach iteration dismisses ``okay.png`` / ``exit.png`` if present, then village / attack logic; else nudge.\n"""
        frame = self.window.screenshot()
        self._update_config_size(frame)
        empty_pt = self.config.get_point('empty')
        self.input.click(*empty_pt, pause=0.2)
        self.input.scroll(*self._scroll_point(), 20)
        delay = random.uniform(0.1, 0.3)
        if self.stop_event.wait(delay):
            return (None, None)
        start = time.time()
        while time.time() - start < timeout:
            self._check_stop()
            frame = self.window.screenshot()
            if frame is not None:
                self._update_config_size(frame)
                if self._dismiss_okay_or_exit_on_frame(frame):
                    if self.stop_event.wait(0.25):
                        return (None, None)
                    continue
                top_roi = VisionService.top_half_region(frame)
                mx, my = self._match_ui_control(frame, 'mbuilder.png', region=top_roi)
                if mx:
                    logger.info('Account loaded in Builder Base (mbuilder.png); leaving to Home Village')
                    cb = getattr(self, '_status_callback', None)
                    if cb:
                        cb('Multi-run: Builder Base on login — leaving (nboat)')
                    self._leave_builder_base_with_nboat(settle_before_drag=False)
                    if self.stop_event.wait(1):
                        return (None, None)
                    continue
                if self._detect_village_type(frame) == 'home':
                    bot_roi = VisionService.bottom_half_region(frame)
                    ax, ay = self._match_ui_control(frame, 'attack.png', region=bot_roi)
                    if ax:
                        return (ax, ay)
            self._nudge_view_to_reveal_attack()
            if self.stop_event.wait(0.35):
                return (None, None)
        return (None, None)

    def _detect_village_type(self, frame=None):
        """Identify the village from builder portraits in either HUD layout."""
        if frame is None:
            frame = self.window.screenshot()
        if frame is None or frame.size == 0:
            return None
        else:
            self._update_config_size(frame)
            top_roi = VisionService.top_half_region(frame)
            mx, my = self._match_ui_control(frame, 'mbuilder.png', region=top_roi)
            if mx:
                return 'builder'
            else:
                hx, hy = self._find_home_village_builder(frame, top_roi)
                if hx:
                    return 'home'
                else:
                    for template in _HOME_VILLAGE_BUILDER_TEMPLATES:
                        if self._match_ui_control(frame, template, region=top_roi)[0] is not None:
                            return 'home'
                    bot_roi = VisionService.bottom_half_region(frame)
                    if self.vision.find_template(frame, 'attack.png', region=bot_roi)[0] is not None:
                        return 'home'
                    if find_battle_button(frame, self.vision)[0] is not None or is_village_hud(frame, self.vision):
                        return 'home'
                    return None
    def _go_to_builder_base_with_boat(self):
        '''Home Village → Builder Base via ``boat.png``.'''
        self._check_stop()
        (bx, by) = self._wait_for_image('boat.png', timeout = 15, error = False)
        if not bx:
            logger.warning('boat.png not found — cannot switch to Builder Base')
            return False
        self.input.click(bx, by, pause = 0.25)
        if self.stop_event.wait(1):
            return False
        (mx, my) = self._wait_for_image('mbuilder.png', timeout = 15, error = False)
        if not mx:
            logger.warning('mbuilder.png not found after boat — may still be in Home Village')
            return False
        return True
    def _ensure_correct_village(self, builder_base):
        '''Before farming: switch villages if the wrong builder portrait is showing.'''
        self._check_stop()
        self.input.click(pause = 0.15, *self.config.get_point('empty'))
        if self.stop_event.wait(0.2):
            return None
        village = self._detect_village_type()
        if village is None:
            logger.info('Startup: village HUD not visible — running home screen recovery first')
            self._home_screen_recovery()
            village = self._detect_village_type()
        if builder_base:
            if village == 'builder':
                return None
            if village == 'home':
                logger.info('Startup: in Home Village but Builder Base was selected — taking boat')
                self._go_to_builder_base_with_boat()
                return None
            logger.warning('Startup: could not detect village (expected Builder Base)')
            return None
        if village == 'home':
            return None
        if village == 'builder':
            logger.info('Startup: in Builder Base but Home Village was selected — leaving (nboat)')
            self._leave_builder_base_with_nboat()
            return None
        logger.warning('Startup: could not detect village (expected Home Village)')
    def _leave_builder_base_with_nboat(self, settle_before_drag = True):
        '''
Pan down-left from screen center (~500px), then click ``nboat.png`` to return to Home Village.
Does not check ``mbuilder.png`` first — call only when a BB→HV trip is intended.

``settle_before_drag``: small delay after prior UI (e.g. collect clicks) before capturing
dimensions and dragging.
'''
        self._check_stop()
        if settle_before_drag and self.stop_event.wait(0.75):
            self._check_stop()
        logger.info('Leaving Builder Base (drag + nboat)')
        cb = getattr(self, '_status_callback', None)
        if cb:
            cb('Multi-run: leaving Builder Base (nboat)')
        self._bb_pan_down_left_from_center()
        (nx, ny) = self._wait_for_image('nboat.png', timeout = 12, error = False)
        if nx:
            self.input.click(nx, ny, pause = 0.25)
            return None
        logger.warning('nboat.png not found after Builder Base drag — may still be in Builder Base')
    def _multi_run_collect_home_village_resources(self):
        '''Multi-run: tap Home Village collect bubbles if visible, before taking the boat to Builder Base.'''
        cb = getattr(self, '_status_callback', None)
        logger.info('Multi-run: Home Village collect (hgold, helixir, hdelixir)')
        if cb:
            cb('Multi-run: Home Village collect')
        for tpl in ('hgold.png', 'helixir.png', 'hdelixir.png'):
            self._check_stop()
            (rx, ry) = self._find_template_once(tpl, threshold = 0.7)
            if not rx:
                continue
            self.input.click(rx, ry, pause = 0.15)
    def _multi_run_builder_base_after_session(self):
        """
Multi-run only: after an account's farming session, collect Home Village resources if icons
appear, open the secondary base via boat, collect builder resources if icons appear,
optionally run the clock boost chain, then leave Builder Base.
"""
        self._multi_run_collect_home_village_resources()
        cb = getattr(self, '_status_callback', None)
        msg = 'Multi-run: Builder Base (boat → collect)'
        logger.info(msg)
        if cb:
            cb(msg)
        (bx, by) = self._wait_for_image('boat.png', timeout = 15, error = False)
        if not bx:
            logger.warning('Multi-run: boat.png not found — skipping Builder Base step')
            return None
        self.input.click(bx, by, pause = 0.2)
        if self.stop_event.wait(1):
            self._check_stop()
        for tpl in ('bgold.png', 'belixir.png', 'bgem.png'):
            self._check_stop()
            (rx, ry) = self._find_template_once(tpl, threshold = 0.7)
            if not rx:
                continue
            self.input.click(rx, ry, pause = 0.15)
        self._check_stop()
        (cx, cy) = self._wait_for_image('bclock.png', timeout = 2, error = False)
        if cx:
            self.input.click(cx, cy, pause = 0.2)
            if self.stop_event.wait(1):
                self._check_stop()
            (cbx, cby) = self._wait_for_image('clockboost.png', timeout = 10, error = False)
            if cbx:
                self.input.click(cbx, cby, pause = 0.2)
            if self.stop_event.wait(1):
                self._check_stop()
            (bux, buy) = self._wait_for_image('boost.png', timeout = 10, error = False)
            if bux:
                self.input.click(bux, buy, pause = 0.15)
        self._leave_builder_base_with_nboat(settle_before_drag = True)
    def _frame_shows_supercell_login_prompt(self, frame):
        """True if OCR sees the Supercell ID login line (list scroll won't help)."""
        if frame is None or frame.size == 0:
            return False
        words = self.vision.find_words_ocr(frame, query = None, min_confidence = 20, preprocess = True, tesseract_config = '--psm 11', white_text = True)
        blob = ' '.join([ w.text for w in words ]).lower()
        if 'supercell id' in blob:
            return True
        if 'log in' in blob and 'supercell' in blob:
            return True
        return False
    def _scroll_player_list_with_drag(self, frame):
        '''Drag ~200px upward from lower-right (client coords) to scroll the change-user list.'''
        if frame is None or frame.size == 0:
            return None
        (h, w) = frame.shape[:2]
        x1 = int(w * 0.86) + random.randint(-15, 15)
        x1 = max(int(w * 0.72), min(w - 8, x1))
        y1 = int(h * 0.86) + random.randint(-12, 12)
        y1 = max(int(h * 0.55), min(h - 12, y1))
        y2 = max(int(h * 0.12), y1 - 200)
        x2 = x1
        self.input.mouse_down(x1, y1)
        self.input.human_move(x1, y1, x2, y2, duration = random.uniform(0.28, 0.42))
        self.input.mouse_up(x2, y2)
    def _wait_for_player_name(self, text, timeout = 25, error = True, region = None, **ocr_kwargs):
        '''
Like :meth:`_wait_for_text`, but after each failed OCR pass drags upward in the lower-right
to scroll the account list before trying again.

OCR is limited to the right half of the window unless ``region`` is passed explicitly.
'''
        start = time.time()
        while time.time() - start < timeout:
            self._check_stop()
            frame = self.window.screenshot()
            if frame is None:
                if self.stop_event.wait(0.5):
                    return (None, None)
                continue
            self._update_config_size(frame)
            roi = region if not region is None else VisionService.right_half_region(frame)
            (x, y) = self.vision.find_word_on_screen(frame, text, region = roi, **ocr_kwargs)
            if x:
                return (x, y)
            if self._frame_shows_supercell_login_prompt(frame):
                msg = f'''Multi-run: "Log in to Supercell ID" is showing and {text!r} was not found — log in or dismiss that screen, then retry.'''
                logger.warning(msg)
                cb = getattr(self, '_status_callback', None)
                if cb:
                    cb(msg)
                raise RuntimeError(msg)
            self._scroll_player_list_with_drag(frame)
            if self.stop_event.wait(0.45):
                return (None, None)
            if error:
                logger.warning(f'''Timeout waiting for player name OCR match: {text!r}''')
        return (None, None)
    def _wait_for_text(self, text, timeout = 10, error = True, region = None, **ocr_kwargs):
        '''Poll screenshots until OCR finds ``text`` (substring match). Returns click center.'''
        start = time.time()
        while time.time() - start < timeout:
            self._check_stop()
            frame = self.window.screenshot()
            if frame is None:
                if self.stop_event.wait(0.5):
                    return (None, None)
                continue
            self._update_config_size(frame)
            (x, y) = self.vision.find_word_on_screen(frame, text, region = region, **ocr_kwargs)
            if x:
                return (x, y)
            if self.stop_event.wait(0.5):
                return (None, None)
        if error:
            logger.warning(f'''Timeout waiting for OCR text containing {text!r}''')
        return (None, None)
    def _dismiss_open_popup(self):
        '''Best-effort close of whatever popup is on screen (okay/exit) so the flow underneath is usable again.'''
        frame = self.window.screenshot()
        if frame is None:
            return False
        self._update_config_size(frame)
        return self._dismiss_okay_or_exit_on_frame(frame)
    def _ensure_valkyrie_army_from_recipes(self):
        '''If Valkyrie army template is missing, load it from Saved Recipes. Returns False on failure.'''
        (vx, vy) = self._wait_for_image('valkarmy.png', timeout = 3, error = False)
        if vx:
            return True
        (sx, sy) = self._wait_for_image('savedrecipes.png')
        if not sx:
            logger.warning('Saved Recipes button not found while ensuring Valkyrie army')
            return False
        self.input.click(sx, sy, pause = 0.2)
        (rx, ry) = self._wait_for_image('valkrecipe.png')
        if not rx:
            logger.warning('Valkyrie recipe template not found')
            self._dismiss_open_popup()  # close the recipes popup we opened
            return False
        (ux, uy) = self._wait_for_image('use.png', y_anchor = ry, y_slop = 200)
        if not ux:
            logger.warning('Use button not found near Valkyrie recipe row')
            self._dismiss_open_popup()  # close the recipes popup we opened
            return False
        self.input.click(ux, uy, pause = 0.2)
        if self.stop_event.wait(0.35):
            return False
        (vx2, _) = self._wait_for_image('valkarmy.png', timeout = 5, error = False)
        if not vx2:
            logger.warning('valkarmy.png still not visible after applying saved recipe')
            return False
        return True
    def _search_region_for_template(self, frame, template, region = None, y_anchor = None, y_slop = 200):
        if not region is None:
            return region
        if not y_anchor is None:
            (h, w) = frame.shape[:2]
            y0 = max(0, y_anchor - y_slop)
            y1 = min(h, y_anchor + y_slop)
            return (0, y0, w, y1 - y0)
        if template in TOP_HALF_BOT_TEMPLATES:
            return VisionService.top_half_region(frame)
        if template in BOTTOM_HALF_BOT_TEMPLATES:
            return VisionService.bottom_half_region(frame)
    def _find_template_once(self, template, region=None, y_anchor=None, y_slop=200, threshold=0.8):
        """One screenshot; match template or return (None, None). No polling."""
        self._check_stop()
        frame = self.window.screenshot()
        if frame is None:
            return (None, None)
        else:
            self._update_config_size(frame)
            search_region = self._search_region_for_template(frame, template, region, y_anchor, y_slop)
            return self._match_ui_control(frame, template, threshold=threshold, region=search_region)
    def _match_ui_control(self, frame, template, threshold=0.8, region=None):
        """Prefer existing art, then recognize a current visible navigation label."""
        point = self.vision.find_template(frame, template, threshold=threshold, region=region)
        if point[0] is not None:
            return point
        else:
            if template in (*_HOME_VILLAGE_BUILDER_TEMPLATES, 'mbuilder.png'):
                height, width = frame.shape[:2]
                new_hud = (width // 2, height // 2, width - width // 2, height - height // 2)
                return self.vision.find_template(frame, template, threshold=threshold, region=new_hud)
            else:
                return find_navigation_fallback(frame, self.vision, template, region=region)
    def _wait_for_any_image(self, templates, timeout=10, error=True, threshold=0.8, region=None, region_from_frame=None):
        """First ordered template match wins (checked left-to-right each frame)."""
        start = time.time()
        while time.time() - start < timeout:
            self._check_stop()
            frame = self.window.screenshot()
            if frame is None:
                if self.stop_event.wait(0.5):
                    return (None, None)
                continue
            self._update_config_size(frame)
            if self._battle_rewards.handle(frame):
                continue
            rkind, rlx, rly = self._find_reload_button(frame)
            if rlx:
                logger.warning('%s wait: reload dialog (%s) detected — reloading the game', templates, rkind)
                self.input.click(rlx, rly, pause=1.0)
                if not self._wait_for_village_after_reload() and self.stop_event.is_set():
                    return (None, None)
                start = time.time()
                continue
            search_region = region
            if search_region is None and region_from_frame is not None:
                search_region = region_from_frame(frame)
            regions = {}
            for template in templates:
                regions[template] = search_region if search_region is not None else self._search_region_for_template(frame, template, None, None, 200)
            # pass 1: cheap template-art match for every candidate first — a slow
            # OCR fallback on an earlier template must not starve a later one
            # (surrender.png's nav fallback takes ~45s and starved endbattle.png).
            for template in templates:
                x, y = self.vision.find_template(frame, template, threshold=threshold, region=regions[template])
                if x:
                    return (x, y)
            # pass 2: navigation-label OCR fallback only if no art matched
            for template in templates:
                x, y = self._match_ui_control(frame, template, threshold=threshold, region=regions[template])
                if x:
                    return (x, y)
            if self.stop_event.wait(0.5):
                self._check_stop()
                return (None, None)
        if error:
            logger.warning(f'Timeout waiting for any of {templates}')
        return (None, None)

    def _wait_for_image(self, template, timeout=10, error=True, region=None, y_anchor=None, y_slop=200, threshold=0.8):
        """Poll for one template. Bails out early through the connection-lost /
        reload-game dialog (which otherwise eats the whole timeout, then the whole
        pass): every poll also checks ``reload.png``; when it shows, the bot logs,
        taps Reload, and lets the caller's next poll re-anchor on the fresh home."""
        start = time.time()
        reload_warned = 0
        while time.time() - start < timeout:
            self._check_stop()
            frame = self.window.screenshot()
            if frame is None:
                continue
            self._update_config_size(frame)
            if self._battle_rewards.handle(frame):
                continue
            rkind, lx, ly = self._find_reload_button(frame)
            if lx:
                reload_warned += 1
                if reload_warned == 1:
                    logger.warning('%s wait: reload dialog (%s) detected — reloading the game', template, rkind)
                self.input.click(lx, ly, pause=1.0)
                if not self._wait_for_village_after_reload() and self.stop_event.is_set():
                    return (None, None)
                start = time.time()
                continue
            search_region = self._search_region_for_template(frame, template, region, y_anchor, y_slop)
            x, y = self._match_ui_control(frame, template, threshold=threshold, region=search_region)
            if x:
                return (x, y)
            if self.stop_event.wait(0.5):
                self._check_stop()
                return (None, None)
        if error:
            logger.warning(f'Timeout waiting for {template}')
        return (None, None)
