# Decompiled with PyLingual (https://pylingual.io)
# Internal filename: 'app\\core\\loot_filter.py'
# Bytecode version: 3.13.0rc3 (3571)
# Source timestamp: 1970-01-01 00:00:00 UTC (0)

"""Loot filter: read the enemy loot on the battle-prep screen and decide whether the\nbase is worth attacking.\n\nMatchmaking hands you a base with its available gold / elixir / dark elixir listed in\nthe top-left corner. Raiding a base that holds 40k gold costs the same time and army as\none holding 800k, so this reads those numbers and lets the farm loop press **Next**\nuntil a base clears the minimums the user set.\n\nReading them needs OCR (Tesseract). When it is not installed, or a frame simply cannot\nbe read, :func:`read_enemy_loot` returns ``None`` and the caller attacks rather than\nskipping — a filter that cannot see must not stop the bot from farming.\n"""
from dataclasses import dataclass
from typing import Optional, Tuple
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
def read_enemy_loot(frame):
    """OCR the enemy loot panel. ``(gold, elixir, dark)`` or None when unreadable."""
    if frame is None or not getattr(frame, 'size', 0):
        return None
    else:
        try:
            groups = VisionService.extract_grouped_numbers_in_region(frame, loot_panel_region(frame), white_text=True, roi_upscale=_LOOT_ROI_UPSCALE)
        except Exception:
            logger.debug('Loot filter: OCR of the loot panel failed', exc_info=True)
            return None
        return parse_enemy_loot(groups)
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