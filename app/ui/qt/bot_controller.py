"""Bot orchestration for the Qt UI."""
from __future__ import annotations
import threading
import time
from PySide6.QtCore import QObject, Signal
from app.core.bot import Bot
from app.core.clan import ClanOptions
from app.core.loot_filter import LootFilter
from app.utils.logger import setup_logger
from app.utils.profile_settings_store import load_profile_settings
logger = setup_logger('BotController')

# Auto-restart policy: crashes are retried with a short backoff so a single bad
# frame (mis-read popup, inactivity disconnect mid-click) no longer ends the
# session. User stops (InterruptedError) never restart.
_RESTART_MAX_CONSECUTIVE = 5
_RESTART_BACKOFF_SECONDS = 20

def _clan_options(profile, *, donate, request):
    """Run-page toggles + profile preferences -> the bot's ClanOptions."""
    point = profile.clan_chat_point
    return ClanOptions(donate=bool(donate), request=bool(request), dry_run=bool(profile.clan_dry_run), chat_point=(float(point[0]), float(point[1])) if point else None, chat_point_aspect=profile.clan_chat_point_aspect, donate_troop=profile.clan_donate_troop, donate_count=int(profile.clan_donate_count), min_elixir=int(profile.clan_min_elixir_k) * 1000, donate_interval_s=int(profile.clan_donate_interval_m) * 60, request_interval_s=int(profile.clan_request_interval_m) * 60)
def _loot_filter(profile):
    """Profile preferences -> the farm loop's LootFilter (settings are in thousands)."""
    return LootFilter(min_gold=int(profile.attack_min_gold_k) * 1000, min_elixir=int(profile.attack_min_elixir_k) * 1000, max_skips=int(profile.attack_max_skips))
class BotController(QObject):
    statusChanged = Signal(str, bool)
    botStarted = Signal()
    botFinished = Signal(object)
    runningChanged = Signal(bool)
    stateChanged = Signal(dict)
    lootChanged = Signal(object, object, object, float)
    def __init__(self, bot_version):
        super().__init__()
        self._bot_version = bot_version
        self._bot = Bot()
        self._bot_thread = None
        self._active_worker_thread = None
        self._restart_generation = 0
    def is_running(self):
        return (
            self._bot_thread is not None
            and self._bot_thread.is_alive()
            and not self._bot.stop_event.is_set()
        )
    def start(self, *, method, minutes, star_bonus, ranked_fill, upgrade_walls, multi_run_players, builder_base=False, loot_prioritise='both', auto_upgrade='off', auto_donate=False, auto_request=False):
        """``minutes <= 0`` = unlimited ("run until maxed") — single Home Village runs only."""
        if self.is_running():
            return
        prev_thread = self._active_worker_thread
        if prev_thread is not None and prev_thread.is_alive():
            self._bot.stop()
            prev_thread.join(timeout=1.5)
        self._restart_generation += 1
        generation = self._restart_generation
        run_stop_event = threading.Event()
        self._bot.bind_stop_event(run_stop_event)
        def worker():
            error_msg = None
            def on_status(msg):
                if self._restart_generation == generation:
                    self.statusChanged.emit(msg, 'not found' in msg.lower())
            def on_state(payload):
                if self._restart_generation == generation:
                    self.stateChanged.emit(dict(payload))
            def on_loot(gold, elixir, dark, elapsed):
                if self._restart_generation == generation:
                    self.lootChanged.emit(int(gold), int(elixir), int(dark), float(elapsed))
            consecutive_failures = 0
            restarts = 0
            while True:
                if self._restart_generation != generation or run_stop_event.is_set():
                    break
                attempt_msg = f'Session #{restarts + 1}' if restarts else None
                if attempt_msg:
                    self.statusChanged.emit(attempt_msg, False)
                try:
                    profile = load_profile_settings()
                    self._bot.start(method, minutes, star_bonus=star_bonus, status_callback=on_status, loot_callback=on_loot, state_callback=on_state, multi_run_players=multi_run_players, ranked_fill=ranked_fill, upgrade_walls=upgrade_walls, earthquake_method=profile.earthquake_method, builder_base=builder_base, loot_prioritise=loot_prioritise, wall_upgrade_threshold=profile.wall_upgrade_threshold_m * 1000000, auto_upgrade=auto_upgrade, reserve_builders=profile.reserve_builders, upgrade_order=profile.upgrade_order, clan_options=_clan_options(profile, donate=auto_donate, request=auto_request), loot_filter=_loot_filter(profile))
                    error_msg = None
                    break
                except InterruptedError:
                    logger.info('Bot thread stopped by user')
                    error_msg = None
                    break
                except Exception as exc:
                    if self._restart_generation != generation or run_stop_event.is_set():
                        error_msg = None
                        break
                    error_msg = str(exc)
                    consecutive_failures += 1
                    restarts += 1
                    logger.exception('Bot thread failed (auto-restart %s/%s after %ss)', consecutive_failures, _RESTART_MAX_CONSECUTIVE, _RESTART_BACKOFF_SECONDS)
                    if consecutive_failures >= _RESTART_MAX_CONSECUTIVE:
                        logger.error('Bot kept crashing %s times in a row — giving up: %s', consecutive_failures, error_msg)
                        break
                    note = f'Bot crashed ({consecutive_failures}/{_RESTART_MAX_CONSECUTIVE}): {error_msg} — restarting in {_RESTART_BACKOFF_SECONDS}s'
                    self.statusChanged.emit(note, False)
                    self.runningChanged.emit(False)
                    if self._sleep_interruptible(_RESTART_BACKOFF_SECONDS, generation, run_stop_event):
                        error_msg = None
                        break
                    if self._restart_generation != generation:
                        error_msg = None
                        break
                    self.runningChanged.emit(True)
                    continue
            if self._restart_generation == generation:
                self._bot_thread = None
                self._active_worker_thread = None
                self.runningChanged.emit(False)
                self.botFinished.emit(error_msg)
        self._bot_thread = threading.Thread(target=worker, daemon=True, name='BotThread')
        self._active_worker_thread = self._bot_thread
        self.runningChanged.emit(True)
        self.botStarted.emit()
        self._bot_thread.start()
    def _sleep_interruptible(self, seconds, generation=None, stop_event=None):
        """Wait out the restart backoff; True when the user pressed Stop meanwhile."""
        ev = stop_event or self._bot.stop_event
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if ev.is_set() or (generation is not None and self._restart_generation != generation):
                return True
            if ev.wait(min(0.5, max(0.01, deadline - time.monotonic()))):
                return True
        return ev.is_set()
    def stop(self):
        self._restart_generation += 1
        self._bot.stop()
        self._bot_thread = None
        self.runningChanged.emit(False)
        self.botFinished.emit(None)
    def on_bot_finished(self):
        if not self.is_running():
            self._bot_thread = None
            self.runningChanged.emit(False)
