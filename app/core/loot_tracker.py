# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\core\\loot_tracker.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

"""Confirmed raid earnings, independent of storage balances and purchases.\n\nOnly the battle result\'s central \'You got\' panel is an earnings source. The\nledger records each bot-started raid once, preserves unknown resources, and\nwrites an append-only local history so totals can be checked after a run.\n"""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from uuid import uuid4
import cv2
from app.core.ui_navigation import find_ui_label
from app.services.vision import VisionService
from app.utils.common import get_template_path
from app.utils.logger import setup_logger
logger = setup_logger('RaidLoot')
RESOURCE_NAMES = ('gold', 'elixir', 'dark_elixir')
UNKNOWN = (None, None, None)
RESULT_LIMITS = (50000000, 50000000, 1000000)
def _currency_present(crop, index):
    if crop.size == 0:
        return False
    else:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        bounds = (((12, 90, 110), (42, 255, 255)), ((125, 80, 80), (175, 255, 255)), ((115, 20, 15), (179, 255, 190)))
        lower, upper = bounds[index]
        return cv2.countNonZero(cv2.inRange(hsv, lower, upper)) / crop.shape[0] / crop.shape[1] >= 0.12
def _read_amount(crop, limit, vision):
    """OCR one amount field. Fast glyph-template check first via ``read_price_digits``;
    fallback to two brightness-floor OCR passes."""
    if crop.size == 0:
        return
    try:
        from app.services.clash_digits import read_price_digits
        glyphs = read_price_digits(crop)
        if glyphs is not None and glyphs.score >= 0.85 and 0 <= glyphs.value <= limit:
            return glyphs.value
    except Exception:
        pass
    votes = []
    aliases = str.maketrans({'O': '0', 'I': '1', 'l': '1', 'S': '5', 'B': '8'})
    for floor, scale in ((225, 1), (205, 2)):
        big = crop if scale == 1 else cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
        # keep only bright ink (digits); the card background is dark/brown
        ink = cv2.inRange(gray, (floor,), (255,))
        padded = cv2.copyMakeBorder(255 - ink, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
        words = vision.find_words_ocr(cv2.cvtColor(padded, cv2.COLOR_GRAY2BGR), preprocess=False, tesseract_config='--psm 7 -c tessedit_char_whitelist=+0123456789')
        if not words:
            continue
        if any(not math.isfinite(float(w.confidence)) for w in words):
            continue
        tokens = [w.text.strip().replace(',', '') for w in sorted(words, key=lambda w: w.left)]
        text = ''.join(tokens)
        if not re.fullmatch('[+]?[0-9OIlSB]+', text):
            continue
        value = int(text.lstrip('+').translate(aliases))
        if 0 <= value <= limit:
            votes.append(value)
            break
    if not votes:
        return
    return votes[0]

def read_raid_result(frame, vision=VisionService):
    """Three optional amounts from a recognized Home Village result, or None.\n\nAvailable enemy loot, top-right storage balances, troop levels, and seasonal\nreward panels are outside the anchored number regions. Missing rows stay\nunknown; a visible, confidently read zero is a legitimate result.\n"""
    if frame is None or not getattr(frame, 'size', 0):
        return None
    else:
        h, w = frame.shape[:2]
        footer = (0, h // 2, w, h - h // 2)
        if not any((get_template_path(name).exists() and vision.find_template(frame, name, region=footer)[0] is not None for name in ['returnhome.png', 'returnhome2.png', 'chestclaim.png'])):
            return
        else:
            cx, cy = (round(w * 0.501), round(h * 0.413))
            top0 = round(cy + 0.018 * h)
            bot0 = top0 + round(h * 0.058)
            ic_l0, ic_r0 = (round(cx + h * 0.049), round(cx + h * 0.115))
            if not (0 <= top0 < bot0 <= h and 0 <= ic_l0 < ic_r0 <= w and _currency_present(frame[top0:bot0, ic_l0:ic_r0], 0)):
                cx, cy = find_ui_label(frame, vision, ('You got', 'YOU GOT', 'You Got'), region=(round(w * 0.40), round(h * 0.35), round(w * 0.20), round(h * 0.12)))
            if cx is None:
                return
            else:
                amounts = []
                for index, limit in enumerate(RESULT_LIMITS):
                    top = round(cy + (0.018 + index * 0.066) * h)
                    bottom = top + round(h * 0.058)
                    left, right = (round(cx - h * 0.18), round(cx + h * 0.045))
                    icon_left, icon_right = (round(cx + h * 0.049), round(cx + h * 0.115))
                    if min(top, left) < 0 or bottom > h or icon_right > w:
                        return None
                    else:
                        if not _currency_present(frame[top:bottom, icon_left:icon_right], index):
                            amounts.append(None)
                            continue
                        else:
                            amounts.append(_read_amount(frame[top:bottom, left:right], limit, vision))
                return tuple(amounts)
def read_raid_bonus(frame, vision=VisionService):
    """Read the separate percentage Bonus panel on an already verified result.\n\nThe caller must first recognize the battle result. An absent or not-yet-\nvisible panel stays unknown; it never means a guessed zero bonus.\n"""
    if frame is None or not getattr(frame, 'size', 0):
        return None
    h, w = frame.shape[:2]
    anchor = None
    fcx, fcy = (round(w * 0.771), round(h * 0.456))
    top0 = round(fcy + 0.028 * h)
    bot0 = top0 + round(h * 0.04)
    ic_l0, ic_r0 = (round(fcx + h * 0.026), round(fcx + h * 0.069))
    if 0 <= top0 < bot0 <= h and 0 <= ic_l0 < ic_r0 <= w and _currency_present(frame[top0:bot0, ic_l0:ic_r0], 0):
        anchor = (fcx, fcy)
    if anchor is None:
        roi = (round(w * 0.66), round(h * 0.36), round(w * 0.22), round(h * 0.18))
        for floor in (225, 195):
            words = vision.find_words_ocr(frame, region=roi, white_text=True, brightness_floor=floor, roi_upscale=2, tesseract_config='--psm 11') or []
            labels = [word for word in words if word.text.lower().strip(':') == 'bonus' and math.isfinite(float(word.confidence)) and word.confidence >= 70]
            candidates = []
            for label in labels:
                percentages = [word for word in words if re.fullmatch('[0-9]{1,3}%', word.text) and word.confidence >= 70 and 0 <= int(word.text[:-1]) <= 100 and abs(word.top - label.top) <= max(word.height, label.height) * 0.6 and 0 <= label.left - word.left - word.width <= label.height * 1.5]
                if len(percentages) == 1:
                    candidates.append((round(label.left + label.width / 2), round(label.top + label.height / 2)))
            if len(candidates) == 1:
                anchor = candidates[0]
                break
    if anchor is None:
        return None
    cx, cy = anchor
    amounts = []
    for index, limit in enumerate(RESULT_LIMITS):
        top = round(cy + (0.028 + index * 0.052) * h)
        bottom = top + round(h * 0.04)
        left, right = (round(cx - h * 0.135), round(cx + h * 0.025))
        icon_left, icon_right = (round(cx + h * 0.026), round(cx + h * 0.069))
        if min(left, top) < 0 or icon_right > w or bottom > h:
            return None
        if not _currency_present(frame[top:bottom, icon_left:icon_right], index):
            amounts.append(None)
            continue
        amounts.append(_read_amount(frame[top:bottom, left:right], limit, vision))
    return tuple(amounts)

def corroborate_results(reads):
    """Require agreement across consecutive captures for each resource separately.\n\nA later contradictory read revokes earlier agreement, which avoids accepting\nan early, still-animating number. An unreadable resource cannot erase another\nresource\'s valid amount or manufacture a zero.\n"""
    previous = [None] * 3
    confirmed = [None] * 3
    for read in reads:
        for index, value in enumerate(read if read is not None else UNKNOWN):
            if value is not None and confirmed[index] is not None and (value != confirmed[index]):
                        confirmed[index] = None
            if value is not None and value == previous[index]:
                    confirmed[index] = value
            previous[index] = value
    return tuple(confirmed)
class LootLedger:
    """One session; each started raid can be resolved at most once."""
    def __init__(self, history_dir=None):
        self.session_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
        self.history_path = Path(history_dir) / (self.session_id + '.jsonl') if history_dir is not None else None
        self.totals = [0, 0, 0]
        self.missing = [0, 0, 0]
        self.known = [0, 0, 0]
        self.finished = 0
        self.complete = 0
        self.started = 0
        self.pending = None
        self._history_failed = False
        self._last_inventory = None
    def _append(self, event):
        # irreducible cflow, using cdg fallback
        # ***<module>.LootLedger._append: Failure: Compilation Error
        if self.history_path is None or self._history_failed:
            return None
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            record = dict(schema=1, session_id=self.session_id, at=datetime.now(timezone.utc).isoformat(), **event)
            with self.history_path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        except OSError:
            self._history_failed = True
            logger.warning('Raid history could not be written; in-memory tracking continues', exc_info=True)
    def begin(self, *, mode='farming', account=None):
        if self.pending is not None:
            self.finish(reason='next raid started before the previous result was recorded')
        self.started += 1
        self.pending = dict(raid_id=self.started, mode=mode, account=account)
        self._append(dict(event='raid_started', **self.pending))
        return self.started
    def finish(self, amounts=UNKNOWN, *, bonus=UNKNOWN, reason='battle result'):
        if self.pending is None:
            return False
        if len(amounts) != 3 or len(bonus) != 3:
            raise ValueError('A raid must have exactly three resource fields')

        def valid(values):
            return tuple(value if type(value) is int and 0 <= value <= limit else None for value, limit in zip(values, RESULT_LIMITS))

        loot, bonus = valid(amounts), valid(bonus)
        confirmed = tuple(sum(value for value in pair if value is not None) if any(value is not None for value in pair) else None for pair in zip(loot, bonus))
        fields_complete = tuple(a is not None and b is not None for a, b in zip(loot, bonus))
        raid = self.pending
        self.pending = None
        self.finished += 1
        self.complete += all(fields_complete)
        for index, value in enumerate(confirmed):
            if not fields_complete[index]:
                self.missing[index] += 1
            if value is not None:
                self.known[index] += 1
                self.totals[index] += value
        self._append(dict(**{'event': 'raid_result', **raid, 'source': 'battle_result' if reason == 'battle result' else reason, 'raid_loot': dict(zip(RESOURCE_NAMES, loot)), 'league_bonus': dict(zip(RESOURCE_NAMES, bonus)), 'resources': dict(zip(RESOURCE_NAMES, confirmed)), 'complete_fields': dict(zip(RESOURCE_NAMES, fields_complete)), 'complete': all(fields_complete)}))
        logger.info('Raid #%s confirmed loot+bonus: %s (G/E/DE); totals %s; incomplete results %s', raid['raid_id'], '/'.join('?' if v is None else ('' if complete else '>=') + str(v) for v, complete in zip(confirmed, fields_complete)), '/'.join(map(str, self.totals)), '/'.join(map(str, self.missing)))
        return True

    def record_inventory(self, amounts, *, account=None):
        snapshot = (account, tuple(amounts))
        if snapshot != self._last_inventory:
            self._last_inventory = snapshot
            self._append(dict(event='inventory_snapshot', account=account, resources=dict(zip(RESOURCE_NAMES, amounts))))
    def summary(self):
        return dict(finished=self.finished, complete=self.complete, missing=list(self.missing), known=list(self.known), pending=self.pending is not None, history=str(self.history_path) if self.history_path and (not self._history_failed) else None)