# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\core\\research.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

"""First suggested Home Village research, independent of builder availability.\n\nOnly the first complete card under a visible Suggested upgrades heading qualifies.\nTwo fresh reads, a corroborated idle lab, sufficient current resources, and an\nexact research title/price/currency on the resource Confirm button gate spending.\nNever scroll through alternatives, buy resources, or use gems or magic items.\n"""
from dataclasses import dataclass
import math
import re
import time
from app.core.ui_navigation import find_build_button, find_buildings_tab, find_research_tab, is_village_hud
from app.core.upgrade_menu import BuildMenuLayout, SECTION_SUGGESTED, UpgradeRow, detect_research_menu, _parse_build_cards, parse_lab_chip
from app.core.upgrader import UpgradeAdvisor, LIVE_UPGRADE_MODES, MODE_OFF
from app.core.village_state import read_hud_resources_stable
from app.services.vision import VisionService
from app.utils.logger import setup_logger
logger = setup_logger('Research')
@dataclass(frozen=True)
class ResearchResult:
    note: str
    pick: UpgradeRow | None = None
    executed: bool = False
    lab: tuple[int, int] | None = None
def first_suggested_research(frame, layout):
    """Read the first card only. A missing name cannot promote the second card."""
    if layout is None or not layout.cards:
        return None
    first = layout.cards[0]
    if first[1] - layout.title_bottom > first[3] * 1.5:
        return None
    one = BuildMenuLayout((first,), layout.title_bottom)
    rows = _parse_build_cards(frame, one, '', batch_labels=True)
    if len(rows) != 1:
        return None
    row = rows[0]
    if row.cost is None and row.affordable is not False:
        text_rows = _parse_build_cards(frame, one, '', batch_labels=False)
        if len(text_rows) == 1 and _name(text_rows[0].label) == _name(row.label):
            row = text_rows[0]
    x, y, w, h = first
    if row.section != SECTION_SUGGESTED or not y <= row.line_y < y + h:
        return None
    return row

def read_research_slot(frame, layout):
    """Strict literal footer counter, with two preprocessing passes agreeing.\n\nThe HUD\'s weaker display-only lab reader never authorizes a purchase.\nCallers also require agreement across independently captured frames.\n"""
    if layout is None or not layout.cards:
        return None
    height = frame.shape[0]
    left = min((c[0] for c in layout.cards))
    right = max((c[0] + c[2] for c in layout.cards))
    tab = (right - left) / 4
    region = (round(left + tab * 1.1), round(height * 0.94) + 1, round(tab * 0.8), max(1, int(height * 0.04) - 1))
    votes = []
    for floor, upscale in [(210, 4), (235, 4), (180, 8), (200, 8)]:
        words = VisionService.find_words_ocr(frame, region=region, white_text=True, brightness_floor=floor, roi_upscale=upscale, tesseract_config='--psm 7 -c tessedit_char_whitelist=01/')
        if not words or any((not math.isfinite(float(w.confidence)) or w.confidence < 40 for w in words)):
            continue
        text = ''.join((w.text.strip() for w in sorted(words, key=lambda w: w.left)))
        if text in ['0/1', '1/1']:
            votes.append((int(text[0]), 1))
            if votes.count(votes[(-1)]) >= 2:
                return votes[(-1)]
    return next((value for value in votes if votes.count(value) >= 2), None)

def _name(value):
    return ' '.join(re.findall('[a-z0-9]+', value.lower()))
def same_research(first, second, frame_height):
    return first is not None and second is not None and (_name(first.label) == _name(second.label)) and (first.cost == second.cost) and (first.resource == second.resource) and (first.affordable is True) and (second.affordable is True) and (abs(first.line_y - second.line_y) <= frame_height * 0.015)

def research_dialog_matches(frame, pick):
    """Exact title, including a level number; building and refill dialogs fail."""
    if frame is None or pick is None:
        return False
    height, width = frame.shape[:2]
    for floor in [190, 210]:
        words = VisionService.find_words_ocr(frame, region=(round(width * 0.1), round(height * 0.025), round(width * 0.78), round(height * 0.08)), white_text=True, brightness_floor=floor, roi_upscale=3, tesseract_config='--psm 7')
        if not words or any((not math.isfinite(float(w.confidence)) or w.confidence < 60 for w in words)):
            continue
        title = _name(' '.join((w.text for w in sorted(words, key=lambda w: w.left))))
        if re.fullmatch('upgrade ' + re.escape(_name(pick.label)) + ' to level [1-9][0-9]*', title):
            return True
    return False

def research_budget_allows(pick, balances):
    if pick is None or pick.cost is None or pick.cost < 1000 or (pick.resource not in ['elixir', 'dark_elixir']) or (pick.affordable is not True) or (balances is None):
        return False
    else:
        index = 1 if pick.resource == 'elixir' else 2
        return balances[index] is not None and balances[index] >= pick.cost
class ResearchAdvisor(UpgradeAdvisor):
    """Reuse capture/escape and exact resource-button checks, never building policy."""
    def __init__(self, *args):
        super().__init__(*args)
        self._next_check = 0
        self._purchase_hold_until = 0
    def _open_research(self):
        if self.stop_event.is_set():
            return
        else:
            frame = self._frame()
            layout = detect_research_menu(frame)
            if layout is None:
                tab = find_research_tab(frame, self.vision)
                if tab[0] is None:
                    button = find_build_button(frame, self.vision)
                    if button[0] is None:
                        return
                    else:
                        self.input.click(*button, pause=0.4, rand=False)
                        if self.stop_event.wait(0.4):
                            return
                        else:
                            frame = self._frame()
                            tab = find_research_tab(frame, self.vision)
                if tab[0] is None or self.stop_event.is_set():
                    return None
                else:
                    self.input.click(*tab, pause=0.4, rand=False)
                    if self.stop_event.wait(0.4):
                        return
                    else:
                        frame = self._frame()
                        layout = detect_research_menu(frame)
            if layout is not None:
                self.input.scroll(*layout.scroll_point, 28, upward=True)
                if self.stop_event.wait(0.5):
                    return
                else:
                    frame = self._frame()
                    layout = detect_research_menu(frame)
            return (frame, layout) if layout is not None else None
    def _verify_started(self):
        """The recognized Army panel must show 0/1 twice after the sole click."""
        previous = None
        for _ in range(5):
            if self.stop_event.wait(0.5):
                return False
            else:
                frame = self._frame()
                layout = detect_research_menu(frame)
                if layout is None and is_village_hud(frame, self.vision):
                        opened = self._open_research()
                        if opened is not None:
                            frame, layout = opened
                slot = read_research_slot(frame, layout)
                if slot == previous == (0, 1):
                    return True
                else:
                    previous = slot
        return False
    def run_pass(self, mode, force=False):
        if mode == MODE_OFF or self.stop_event.is_set():
            return None
        if time.monotonic() < self._purchase_hold_until:
            return
        if not force and time.monotonic() < self._next_check:
            return
        self._next_check = time.monotonic() + 60
        opened = False
        pick = None
        try:
            frame = self._frame()
            if frame is None:
                return ResearchResult('screen unreadable; research deferred')
            if find_buildings_tab(frame, self.vision)[0] is not None:
                self._close_popup()
                frame = self._frame()
            if frame is not None and is_village_hud(frame, self.vision):
                if self.vision.find_template(frame, 'mbuilder.png', region=VisionService.bottom_half_region(frame))[0] is not None:
                    return ResearchResult('Home Village required; research deferred')
            if parse_lab_chip(frame) == (0, 1):
                return ResearchResult('laboratory is busy', lab=(0, 1))
            balances = read_hud_resources_stable(self._frame, self.stop_event.wait)
            if self.stop_event.is_set():
                return None
            opened = True
            result = self._open_research()
            if result is None:
                return ResearchResult('Army Upgrades panel unreadable; research deferred')
            first_frame, layout = result
            slot = read_research_slot(first_frame, layout)
            if slot == (0, 1):
                return ResearchResult('laboratory is busy', lab=slot)
            if slot != (1, 1):
                return ResearchResult('laboratory counter unreadable; research deferred')
            pick = first_suggested_research(first_frame, layout)
            if pick is None:
                return ResearchResult('first suggested research unreadable or absent; keeping suggested order', lab=slot)
            if not research_budget_allows(pick, balances):
                return ResearchResult('first suggestion ' + pick.label + ' is unaffordable or its price/currency is unreadable', pick, lab=slot)
            if self.stop_event.wait(0.35):
                return
            frame = self._frame()
            layout = detect_research_menu(frame)
            fresh = first_suggested_research(frame, layout)
            if read_research_slot(frame, layout) != slot or not same_research(pick, fresh, frame.shape[0]):
                return ResearchResult('suggestion or lab counter changed; research deferred', pick)
            if mode not in LIVE_UPGRADE_MODES:
                return ResearchResult('preview: first suggestion ' + pick.label + '; no research started', pick, lab=slot)
            if self.stop_event.is_set():
                return
            self.input.click(*fresh.center, pause=0.5, rand=False)
            frame = self._frame()
            point = self._find_resource_confirm(frame, fresh) if research_dialog_matches(frame, fresh) else None
            if point is None or self.stop_event.is_set():
                return ResearchResult('research confirmation did not match name, price and currency; no purchase', pick)
            self._purchase_hold_until = self._next_check = time.monotonic() + 900
            self.input.click(*point, pause=0.8, rand=False)
            if self._verify_started():
                self._purchase_hold_until = 0
                self._next_check = time.monotonic() + 60
                logger.info('Research STARTED: %s (cost %d %s); lab 1/1 -> 0/1', pick.label, pick.cost, pick.resource)
                return ResearchResult('started ' + pick.label, pick, True, (0, 1))
            logger.warning('Research confirmation sent for %s; lab transition unreadable, no immediate purchase retry', pick.label)
            return ResearchResult('confirmation sent; research start unverified, purchase retry delayed', pick)
        finally:
            if opened and not self.stop_event.is_set():
                self._escape_ui('research pass finished')
