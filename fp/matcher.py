"""
matcher.py — Decide which catalog products correspond to a PFPB list item.

match_product(name) returns (Item, via) or None, where via is
  "ingredient" — active ingredient written in the product name (high confidence)
  "brand"      — matched through a reference/brand alias (medium confidence)
"""

import re
import unicodedata
from typing import List, Optional, Tuple

from fp.elenco import BRAND_ALIASES, COMBO_BRANDS, COMBO_INGREDIENTS, ELENCO, TWO_STRENGTHS, Item

_UNIT_FIX = [
    (re.compile(r"(\d),(\d)"), r"\1.\2"),                   # 0,03 -> 0.03
    (re.compile(r"[µμ]g|\bug\b"), "mcg"),
    (re.compile(r"(\d)\s+(mg|mcg|g|ml|ui|u\.i\.)"), r"\1\2"),  # 50 mg -> 50mg
    (re.compile(r"u\.i\."), "ui"),
    (re.compile(r"(\d)ui\s*/\s*ml"), r"\1ui/ml"),
    (re.compile(r"(\d)u/ml"), r"\1ui/ml"),
]


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    for rx, rep in _UNIT_FIX:
        t = rx.sub(rep, t)
    return re.sub(r"\s+", " ", t).strip()


def _word(rx: str) -> str:
    # Anchor plain ingredient names at a word start ("anlodipino" must not hit "levanlodipino").
    return r"\b" + rx if rx[:1].isalpha() else rx


def _dose_rx(dose: str) -> re.Pattern:
    # "5mg" must not match "25mg" / "0.5mg"; "5mg/ml" is still fine.
    alts = "|".join(re.escape(d) for d in dose.split("|"))
    return re.compile(r"(?<![\d.])(?:" + alts + r")(?![a-z\d])")


_COMPILED = []
for it in ELENCO:
    _COMPILED.append((
        it,
        [re.compile(_word(r)) for r in it.ingredients],
        [_dose_rx(d) for d in it.doses],
        [re.compile(r) for r in it.exclude],
        re.compile(it.form) if it.form else None,
        # Combo partners = known ingredients this item does not itself contain.
        [re.compile(r"\b" + c) for c in COMBO_INGREDIENTS
         if not any(re.search(r, c) for r in it.ingredients)],
        len(it.doses) == 1,
    ))
_COMBO_BRANDS = re.compile(COMBO_BRANDS)
_TWO_STRENGTHS = re.compile(TWO_STRENGTHS)
_BRANDS = [(re.compile(b), ing, re.compile(bad) if bad else None) for b, ing, bad in BRAND_ALIASES]

# Cheap prefilter: product must mention something relevant at all.
_PREFILTER = re.compile("|".join(
    [r for it in ELENCO for r in it.ingredients] + [b for b, _, _ in BRAND_ALIASES]
))


def _try(norm: str) -> Optional[Item]:
    for it, ings, doses, excl, form, combos, single in _COMPILED:
        if not all(r.search(norm) for r in ings):
            continue
        if not all(r.search(norm) for r in doses):
            continue
        if form and not form.search(norm):
            continue
        if any(r.search(norm) for r in excl):
            continue
        if any(r.search(norm) for r in combos):
            continue
        if single and (_COMBO_BRANDS.search(norm) or _TWO_STRENGTHS.search(norm)):
            continue
        return it
    return None


def match_product(name: str) -> Optional[Tuple[Item, str]]:
    norm = normalize(name)
    if not _PREFILTER.search(norm):
        return None
    it = _try(norm)
    if it:
        return it, "ingredient"
    for rx, ing, bad in _BRANDS:
        if rx.search(norm) and not (bad and bad.search(norm)):
            it = _try(norm + " " + ing)
            if it:
                return it, "brand"
    return None


def match_many(names: List[str]) -> List[Optional[Tuple[Item, str]]]:
    return [match_product(n) for n in names]
