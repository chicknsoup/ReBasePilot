# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\core\\loot_filter.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

"""Loot filter: read the enemy loot on the battle-prep screen and decide whether the\nbase is worth attacking.\n\nMatchmaking hands you a base with its available gold / elixir / dark elixir listed in\nthe top-left corner. Raiding a base that holds 40k gold costs the same time and army as\none holding 800k, so this reads those numbers and lets the farm loop press **Next**\nuntil a base clears the minimums the user set.\n\nReading them needs OCR (Tesseract). When it is not installed, or a frame simply cannot\nbe read, :func:`read_enemy_loot` returns ``None`` and the caller attacks rather than\nskipping — a filter that cannot see must not stop the bot from farming.\n"""
from dataclasses import dataclass
from typing import Optional, Tuple
import cv2
import numpy as np
from app.services.clash_digits import load_digit_bank, normalize_glyph
from app.services.vision import VisionService
from app.utils.logger import setup_logger
logger = setup_logger('LootFilter')
TEMPLATE_NEXT_BASE = 'nextbase.png'
LOOT_TEMPLATE_SPECS = ((TEMPLATE_NEXT_BASE, 'Next button (battle prep, to skip a base)', False),)
_LOOT_PANEL_REGION = (0.0, 0.02, 0.24, 0.26)
_LOOT_ROI_UPSCALE = 3
_IMPLAUSIBLE_MAIN = 50000000
_IMPLAUSIBLE_DARK = 1000000
_NOISE_VALUE_FLOOR = 1000
@dataclass(frozen=True)
class LootFilter:
    """Minimums a base must hold to be worth attacking. Zeroes mean \"attack anything\"."""
    min_gold: int = 0
    min_elixir: int = 0
    max_skips: int = 10
    @property
    def enabled(self):
        return self.max_skips > 0 and (self.min_gold > 0 or self.min_elixir > 0)
    def summary(self):
        return f'gold >= {self.min_gold:,} and elixir >= {self.min_elixir:,}'
def loot_panel_region(frame):
    """ROI (x, y, w, h) over the enemy loot panel."""
    h, w = frame.shape[:2]
    fx, fy, fw, fh = _LOOT_PANEL_REGION
    return (int(w * fx), int(h * fy), max(1, int(w * fw)), max(1, int(h * fh)))
def parse_enemy_loot(groups):
    """``(gold, elixir, dark)`` from OCR\'d number clusters, or None.\n\nThe panel stacks the resources vertically, so vertical order is the assignment —\nthe same reasoning as the home HUD reader, which cannot sort horizontally because\nthe numbers are right-aligned. A base with no dark elixir row reads as two numbers,\nand dark is then 0 rather than a failure.\n\nFragments are dropped before the rows are assigned. Live repro: reads came back as\n``1/885517/1190256`` and ``302001/1/1549124`` — a stray one-digit cluster (an icon\nedge, a trophy delta) taken as a resource row shifts every real number down a slot,\nwhich is far worse than ignoring it. Nothing worth attacking holds under\n``_NOISE_VALUE_FLOOR``, so anything smaller cannot be a gold or elixir row.\n"""
    scored = []
    for g in groups or ():
        value = VisionService.parse_loot_amount_from_grouped_text(g.text)
        if value is not None:
            scored.append((float(g.top) + float(g.height) * 0.5, value))
    scored.sort(key=lambda t: t[0])
    kept = [v for _cy, v in scored if v >= _NOISE_VALUE_FLOOR]
    if len(kept) < 2:
        logger.debug('Loot filter: only %d usable number(s) in the loot panel (raw: %s)', len(kept), [v for _cy, v in scored])
    else:
        gold, elixir = (kept[0], kept[1])
        dark = kept[2] if len(kept) >= 3 else 0
        if gold > _IMPLAUSIBLE_MAIN or elixir > _IMPLAUSIBLE_MAIN:
            logger.warning('Loot filter: implausible gold/elixir read %s/%s discarded as an OCR misread (raw: %s)', gold, elixir, [v for _cy, v in scored])
        else:
            if dark > _IMPLAUSIBLE_DARK:
                logger.debug('Loot filter: ignoring an implausible dark elixir read (%s)', dark)
                dark = 0
            return (gold, elixir, dark)
def _read_loot_rows_fast(
    frame,
    *,
    min_digits=4,
    value_floor=_NOISE_VALUE_FLOOR,
    skip_header_frac=0.0,
    min_score=0.84,
    min_margin=0.012,
):
    """Fast (< 30 ms) glyph-based reader for the top-left Available Loot rows.

    Uses the bundled Clash digit atlas instead of spawning Tesseract subprocesses.
    Returns ``(gold, elixir, dark)`` when at least Gold and Elixir decode cleanly
    with high glyph similarity, or ``None`` when unreadable.
    """
    rx, ry, rw, rh = loot_panel_region(frame)
    roi = frame[ry:ry + rh, rx:rx + rw]
    if roi.size == 0 or roi.shape[0] < 24 or roi.shape[1] < 40:
        return None
    try:
        bank, labels, counts = load_digit_bank()
    except Exception:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    max_digit_h = max(14, int(round(roi.shape[0] * 0.22)))
    min_digit_h = max(6, int(round(roi.shape[0] * 0.045)))
    min_row_y = int(round(roi.shape[0] * skip_header_frac))
    max_row_x = int(round(roi.shape[1] * 0.72))

    for v_floor, s_ceil in ((210, 125), (195, 135)):
        ink = cv2.inRange(hsv, (0, 0, v_floor), (180, s_ceil, 255))
        _, _, stats, _ = cv2.connectedComponentsWithStats(ink)
        cands = [
            tuple(map(int, b[:4]))
            for b in stats[1:]
            if b[4] >= 4
            and b[1] >= min_row_y
            and b[0] <= max_row_x
            and min_digit_h <= b[3] <= max_digit_h
            and 0.2 <= (b[2] / float(b[3])) <= 1.5
        ]
        if not cands:
            continue
        lines = []
        for box in sorted(cands, key=lambda b: b[1] + b[3] * 0.5):
            cy = box[1] + box[3] * 0.5
            line = next(
                (ln for ln in lines if abs(cy - (ln[0][1] + ln[0][3] * 0.5)) <= max(box[3], ln[0][3]) * 0.45),
                None,
            )
            if line is None:
                lines.append([box])
            else:
                line.append(box)

        rows = []
        for ln in lines:
            heights = sorted(b[3] for b in ln)
            med_h = heights[len(heights) // 2]
            filtered = sorted(
                (b for b in ln if med_h * 0.7 <= b[3] <= med_h * 1.35),
                key=lambda b: b[0],
            )
            # Keep the left-aligned loot number intact across thousand-separator spaces
            if len(filtered) > 1:
                max_gap = max(14, int(round(med_h * 2.3)))
                runs = [[filtered[0]]]
                for box in filtered[1:]:
                    prev = runs[-1][-1]
                    if box[0] - (prev[0] + prev[2]) <= max_gap:
                        runs[-1].append(box)
                    else:
                        runs.append([box])
                # Never truncate a split number inside the loot panel; fall back instead
                if len(runs) > 1 and runs[1][0][0] <= int(roi.shape[1] * 0.62):
                    continue
                filtered = runs[0]
            if filtered and filtered[0][0] > int(roi.shape[1] * 0.45):
                continue
            if not (min_digits <= len(filtered) <= 8):
                continue
            digits = []
            ok = True
            for x, y, w, h in filtered:
                if x <= 0 or y <= 0 or x + w >= ink.shape[1] or y + h >= ink.shape[0]:
                    ok = False
                    break
                glyph = normalize_glyph(ink[y:y + h, x:x + w]) > 0
                g_sum = int(glyph.sum())
                if g_sum == 0:
                    ok = False
                    break
                overlap = np.logical_and(bank, glyph).sum(axis=(1, 2))
                sims = 2.0 * overlap / (counts + g_sum)
                per = [float(sims[labels == d].max()) for d in range(10)]
                order = np.argsort(per)
                best, second = int(order[-1]), int(order[-2])
                if per[best] < min_score or (per[best] - per[second]) < min_margin:
                    ok = False
                    break
                digits.append(str(best))
            if ok:
                val = int(''.join(digits))
                if val >= value_floor:
                    rows.append((filtered[0][1] + med_h * 0.5, val))
        if len(rows) >= 2:
            rows.sort(key=lambda t: t[0])
            # Guard against missing Gold (row 0 too low) or missing Elixir (row 1 is actually Dark Elixir, 2 pitches below Gold)
            if len(rows) == 2 and (
                rows[0][0] > roi.shape[0] * 0.54
                or (rows[1][0] - rows[0][0]) > roi.shape[0] * 0.32
            ):
                continue
            gold, elixir = rows[0][1], rows[1][1]
            dark = rows[2][1] if len(rows) >= 3 else 0
            if gold <= _IMPLAUSIBLE_MAIN and elixir <= _IMPLAUSIBLE_MAIN:
                if dark > _IMPLAUSIBLE_DARK:
                    dark = 0
                return (gold, elixir, dark)
    return None


def _read_enemy_loot_fast(frame):
    return _read_loot_rows_fast(
        frame,
        min_digits=4,
        value_floor=_NOISE_VALUE_FLOOR,
        skip_header_frac=0.0,
        min_score=0.83,
        min_margin=0.010,
    )


def read_battle_remaining_loot(frame):
    """Fast in-battle reader for remaining Available Loot (supports 0 / 0 / 0)."""
    if frame is None or not getattr(frame, 'size', 0):
        return None
    return _read_loot_rows_fast(
        frame,
        min_digits=1,
        value_floor=0,
        skip_header_frac=0.34,
        min_score=0.80,
        min_margin=0.001,
    )


def read_enemy_loot_ocr(frame):
    """Tesseract-only reader for the enemy loot panel (used as fallback / confirmation)."""
    if frame is None or not getattr(frame, 'size', 0):
        return None
    try:
        groups = VisionService.extract_grouped_numbers_in_region(
            frame,
            loot_panel_region(frame),
            white_text=True,
            roi_upscale=_LOOT_ROI_UPSCALE,
        )
    except Exception:
        logger.debug('Loot filter: OCR of the loot panel failed', exc_info=True)
        return None
    return parse_enemy_loot(groups)


def read_enemy_loot(frame):
    """Read the enemy loot panel. ``(gold, elixir, dark)`` or None when unreadable."""
    if frame is None or not getattr(frame, 'size', 0):
        return None
    fast = _read_enemy_loot_fast(frame)
    if fast is not None:
        return fast
    return read_enemy_loot_ocr(frame)
def combine_reads(reads):
    """Fold several reads of the same panel into one, keeping the largest value seen\nfor each resource. Returns None when nothing was readable.\n\nOCR drops digits far more often than it invents them. Measured live on a base\nholding 705,559 gold: three of six reads came back 70,559 — the same panel, a\ndropped digit, a tenfold error, and enough to skip a base that easily cleared the\nminimum. Invented digits are the rarer failure and are already caught by the\nimplausibility cap, so the largest reading per resource is the one to trust.\n"""
    # ***<module>.combine_reads: Failure detected at line number 73 and instruction offset 62: Different bytecode
    usable = [r for r in reads if r is not None]
    if not usable:
        return
    return (max(r[0] for r in usable), max(r[1] for r in usable), max(r[2] for r in usable))
def meets(loot, loot_filter):
    """True when the base clears the minimums — and when the loot could not be read,\nwhich is deliberate: an unreadable panel must not stop the bot from attacking."""
    if loot is None or not loot_filter.enabled:
        return True
    else:
        gold, elixir, _dark = loot
        return gold >= loot_filter.min_gold and elixir >= loot_filter.min_elixir