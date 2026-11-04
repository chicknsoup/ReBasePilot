# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\core\\village_state.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

"""Home-village state snapshot for the auto-maxer\'s idle gate and the UI status panel.\n\nReads everything the home screen shows about \"is there anything productive to do\":\nfree builders (top-HUD builder chip), laboratory research slots (flask chip left of\nit), and full gold/elixir storages (the hero-bar full-storage icons). All fields are\nOptional — a chip that is absent or unreadable yields ``None``, never a guess, so the\ncaller can be conservative. TH-agnostic by construction: villages without a lab (or\nwith fewer builders) simply read as ``None`` / smaller totals.\n\nThe Pet House has NO home-screen indicator (verified on live TH15 frames: a busy pet\nupgrade appears neither as a HUD chip nor as a builder-popup in-progress row), so pet\nstate is not tracked here — pets remain a manual job and never block idling.\n"""
import time
from dataclasses import dataclass
from typing import Optional, Tuple
from app.core.upgrade_menu import parse_builder_chip, parse_lab_chip
from app.services.vision import VisionService
from app.utils.logger import setup_logger
logger = setup_logger('VillageState')
_IMPLAUSIBLE_HUD_MAIN = 30000000
@dataclass(frozen=True)
class VillageState:
    builders: Optional[Tuple[int, int]]
    lab: Optional[Tuple[int, int]]
    gold_full: bool
    elixir_full: bool
    captured_at: float
    @property
    def free_builders(self):
        return self.builders[0] if self.builders else None
    @property
    def storages_full(self):
        """Both main storages at cap — farming would earn (almost) nothing.\nDark elixir has no full-storage icon; DE overflow alone never blocks idling."""
        return self.gold_full and self.elixir_full
    @property
    def lab_idle(self):
        """True when the lab chip is readable and shows a free research slot."""
        return bool(self.lab) and self.lab[0] > 0
    def summary(self):
        b = f'{self.builders[0]}/{self.builders[1]}' if self.builders else '?'
        l = f'{self.lab[0]}/{self.lab[1]}' if self.lab else '?'
        s = '~full' if self.storages_full else 'gold ~full' if self.gold_full else 'elixir ~full' if self.elixir_full else 'filling'
        return f'builders {b} free, lab {l} free, storages {s}'
def read_village_state(frame):
    """Parse a home-screen frame → :class:`VillageState`. Chip reads are OCR — callers\nthat gate behavior on them should tolerate ``None`` (busy frames misread; the\nidle gate re-reads on its next wake anyway)."""
    if frame is None or frame.size == 0:
        return None
    else:
        gx, _gy = VisionService.find_active_hgoldfull(frame)
        ex, _ey = VisionService.find_active_helixirfull(frame)
        state = VillageState(builders=parse_builder_chip(frame), lab=parse_lab_chip(frame), gold_full=gx is not None, elixir_full=ex is not None, captured_at=time.monotonic())
        logger.debug('village state: %s', state.summary())
        return state
def read_hud_resources_stable(capture, wait, attempts=4):
    """Independently corroborated balances; an unreadable currency stays None."""
    previous = [None] * 3
    stable = [None] * 3
    for attempt in range(max(2, attempts)):
        if attempt and wait(0.35):
            return
        else:
            frame = capture()
            if frame is None or not getattr(frame, 'size', 0):
                previous = [None] * 3
                continue
            h, w = frame.shape[:2]
            if VisionService.find_template(frame, 'build.png', region=VisionService.bottom_half_region(frame))[0] is not None:
                groups = VisionService._new_hud_numbers(frame, allow_partial=True)
                current = [None] * 3
                for group in groups:
                    index = round((group.top / h - 0.0265) / 0.0575)
                    if 0 <= index < 3:
                            current[index] = VisionService.parse_loot_amount_from_grouped_text(group.text)
            else:
                current = VisionService.parse_hud_resources_triplet(VisionService.extract_top_right_hud_numbers(frame)) or (None, None, None)
            for index, value in enumerate(current):
                limit = 2000000 if index == 2 else _IMPLAUSIBLE_HUD_MAIN
                if value is None or value > limit:
                    previous[index] = None
                else:
                    if stable[index] is not None and stable[index] != value:
                            stable[index] = None
                    if value == previous[index]:
                        stable[index] = value
                    previous[index] = value
            if all((value is not None for value in stable)):
                break
    return tuple(stable) if any((value is not None for value in stable)) else None
def read_hud_triplet_stable(capture, wait, attempts=4):
    """Top-right HUD (gold, elixir, dark) with consecutive-frame agreement. Single\nreads get corrupted by animations/popups crossing the HUD (live repro: elixir\nread as \"115M\" — 9 digits against a ~21M cap), but corruption is stochastic:\ntwo identical consecutive parses are trustworthy. Returns the triplet or\n``None`` when no two consecutive reads agree."""
    prev = None
    for i in range(max(2, attempts)):
        if i and wait(0.35):
            return
        else:
            frame = capture()
            if frame is None or getattr(frame, 'size', 0) == 0:
                continue
            else:
                triplet = VisionService.parse_hud_resources_triplet(VisionService.extract_top_right_hud_numbers(frame))
                if triplet is None:
                    prev = None
                    continue
                else:
                    if triplet == prev:
                        gold, elixir, _dark = triplet
                        if gold > _IMPLAUSIBLE_HUD_MAIN or elixir > _IMPLAUSIBLE_HUD_MAIN:
                            logger.warning('HUD read gold=%s elixir=%s is impossibly large — treating it as unreadable', gold, elixir)
                            prev = None
                            continue
                        else:
                            return triplet
                    else:
                        prev = triplet
    return
def read_village_state_stable(capture, wait, attempts=3):
    """Multi-frame :func:`read_village_state`: the translucent HUD chips flicker against\nthe animated village, so single-frame chip reads drop out (live repro). Merges up\nto ``attempts`` frames — first readable value per field wins; full-storage icons\nOR across frames. ``capture()`` → frame or None; ``wait(seconds)`` → True to abort\n(stop-event semantics)."""
    merged = None
    for i in range(max(1, attempts)):
        if i and wait(0.4):
            return merged
        else:
            state = read_village_state(capture())
            if state is None:
                continue
            else:
                if merged is None:
                    merged = state
                else:
                    merged = VillageState(builders=merged.builders or state.builders, lab=merged.lab or state.lab, gold_full=merged.gold_full or state.gold_full, elixir_full=merged.elixir_full or state.elixir_full, captured_at=state.captured_at)
                if merged.builders and merged.lab:
                        break
    if merged is not None:
        logger.info('village state: %s', merged.summary())
    return merged