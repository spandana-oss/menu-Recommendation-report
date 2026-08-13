"""
duplicate_detector.py — Section I support: structural-bundle flags,
near-duplicate pairs, and unique-use-ingredient flags.

Confidence varies a lot by piece:

  - detect_structural_bundles(): MECHANICAL, high confidence. Rule =
    category in {BREAKFAST, BREAKFAST SIDE, SIDE / OTHER} AND (name starts
    with "Side" or is exactly "Coleslaw") AND name contains one of
    egg/toast/bacon/sausage/pulled/coleslaw. Reproduced the real menu's
    10 structural-bundle items exactly (10/10, 0 false positives).

  - detect_unique_ingredients(): MECHANICAL, high confidence. Directly
    implements the spec: parse every item's ingredient list, flag
    ingredients appearing in exactly one dish menu-wide.

  - detect_near_duplicates(): HEURISTIC, moderate confidence. Combines
    category match + item-name token overlap + ingredient token overlap
    into a similarity score. This reliably catches duplicates that share
    vocabulary (e.g. "Side Caesar" vs "Caesar Salad", "...with Chips" vs
    "...with Fries", "Buffalo Chicken Wrap" vs "Grilled Buffalo Chicken
    Wrap") but WILL MISS duplicates that are conceptually similar but
    lexically unrelated (e.g. "Chips" vs "French Fries" — both are a
    single-serve starchy side, but share almost no ingredient/name text).
    Review the flagged pairs in the app before trusting them, and add any
    missed pairs manually — the review step exists for exactly this.
"""

import re
from dataclasses import dataclass
from itertools import combinations
from typing import List, Optional


STRUCTURAL_CATEGORIES = {'BREAKFAST', 'BREAKFAST SIDE', 'SIDE / OTHER'}
STRUCTURAL_KEYWORDS = ('egg', 'toast', 'bacon', 'sausage', 'pulled', 'coleslaw')

STOPWORDS = {'with', 'and', 'the', 'a', 'of', 'in', 'on', 'side'}


def detect_structural_bundles(items):
    """items: list of objects/dicts with .name / .category (or ['name']/['category'])."""
    flagged = set()
    for it in items:
        name = _get(it, 'name')
        category = _get(it, 'category')
        name_l = name.lower()
        if category not in STRUCTURAL_CATEGORIES:
            continue
        if not (name_l.startswith('side') or name_l == 'coleslaw'):
            continue
        if any(kw in name_l for kw in STRUCTURAL_KEYWORDS):
            flagged.add(name)
    return flagged


def _tokenize(text):
    words = re.findall(r"[a-z]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


@dataclass
class DuplicatePair:
    item_a: str
    item_b: str
    score: float


def detect_near_duplicates(items, name_weight=0.6, ingredient_weight=0.4, threshold=0.35):
    """Returns a list of DuplicatePair for any two items in the same raw
    category whose combined name+ingredient token similarity clears the
    threshold. Tune `threshold` down to catch more pairs (more false
    positives) or up to catch fewer (more false negatives)."""
    pairs = []
    by_cat = {}
    for it in items:
        by_cat.setdefault(_get(it, 'category'), []).append(it)

    for cat, group in by_cat.items():
        for a, b in combinations(group, 2):
            name_sim = _jaccard(_tokenize(_get(a, 'name')), _tokenize(_get(b, 'name')))
            ing_sim = _jaccard(_tokenize(_get(a, 'ingredients')), _tokenize(_get(b, 'ingredients')))
            score = name_weight * name_sim + ingredient_weight * ing_sim
            if score >= threshold:
                pairs.append(DuplicatePair(_get(a, 'name'), _get(b, 'name'), round(score, 3)))
    return sorted(pairs, key=lambda p: -p.score)


def assign_duplicate_flags(items, pairs, qty_by_name):
    """Given confirmed DuplicatePair list, decide directionality:
    - If one item's name starts with "Side" and the other doesn't (a
      portion-size variant of a full-size item), only the "Side" item
      gets duplicate_of set (matches the real menu's asymmetric pattern
      for Side House Salad/Side Caesar/Chips-style pairs).
    - Otherwise (same-tier variants, e.g. two sandwich builds) both
      directions are set, matching the Brewpub/Buffalo-wrap pattern.
    Sets .duplicate_of / .duplicate_outsells on each item in `items`
    (matched by .name). Does not overwrite an already-set duplicate_of."""
    by_name = {_get(it, 'name'): it for it in items}
    for p in pairs:
        a_is_side = _get(by_name[p.item_a], 'name').lower().startswith('side')
        b_is_side = _get(by_name[p.item_b], 'name').lower().startswith('side')
        a_qty = qty_by_name.get(p.item_a, 0)
        b_qty = qty_by_name.get(p.item_b, 0)

        if a_is_side and not b_is_side:
            _set_dup(by_name[p.item_a], p.item_b, b_qty > a_qty)
        elif b_is_side and not a_is_side:
            _set_dup(by_name[p.item_b], p.item_a, a_qty > b_qty)
        else:
            _set_dup(by_name[p.item_a], p.item_b, b_qty > a_qty)
            _set_dup(by_name[p.item_b], p.item_a, a_qty > b_qty)
    return items


def _set_dup(it, other_name, outsells):
    if getattr(it, 'duplicate_of', None):
        return  # don't overwrite an existing/manual assignment
    it.duplicate_of = other_name
    it.duplicate_outsells = outsells


def detect_unique_ingredients(items):
    """Mechanical: parse every item's ingredient list, flag ingredients
    that appear in exactly one dish menu-wide. Returns {item_name: bool}."""
    ingredient_to_items = {}
    parsed = {}
    for it in items:
        name = _get(it, 'name')
        ings = [i.strip().lower() for i in re.split(r',(?![^()]*\))', _get(it, 'ingredients')) if i.strip()]
        parsed[name] = ings
        for ing in ings:
            ingredient_to_items.setdefault(ing, set()).add(name)

    result = {}
    for name, ings in parsed.items():
        result[name] = any(len(ingredient_to_items[ing]) == 1 for ing in ings)
    return result


def _get(obj, key):
    if isinstance(obj, dict):
        return obj[key]
    return getattr(obj, key)
