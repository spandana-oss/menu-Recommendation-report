"""
replacement_recommender.py — for every REMOVE item, generates a
"replacement brief": what market gap that item's removal leaves, and what
a new item should target to fill it. This is the final step of the same
Menu Refresh decision (KEEP / REFRESH / REMOVE-and-replace), not a
separate "Menu Creation" feature — REMOVE without a replacement direction
is an incomplete decision.

What this DOES do (mechanical, built from signals already computed by
analysis_engine.py — category shares, occasion roles, commuter/income fit,
the item's own removal reason):
  - Identifies which category loses share when the item is removed, and
    whether that category is still adequately represented afterward.
  - Restates the occasion(s) (weekday/weekend role) the removed item
    served, so a replacement can be aimed at the same real demand
    instead of guessing.
  - Suggests a price band from the survivors in that category (or an
    adjacent one, if removal was duplication-driven and the category is
    already well covered).
  - Flags the SPECIFIC reason this item failed (duplication / bottom-
    quintile margin / bottom-quintile popularity / ghost revenue) so the
    replacement brief tells you what NOT to repeat.

What this does NOT do: name a dish, write a description, or choose
ingredients. That's the same creative judgment call REFRESH's new-item
naming already isn't automated for — see run_analysis.py's docstring.
This produces the brief a person (or an assistant, handed this output)
would use to actually design the replacement item.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReplacementBrief:
    removed_item: str
    category: str
    reason_summary: str
    gap_type: str  # 'duplication' | 'margin' | 'popularity' | 'ghost_revenue' | 'commuter_fit'
    category_share_before_pct: float
    category_share_after_pct: float
    category_still_covered: bool
    target_weekday_role: str
    target_weekend_role: str
    suggested_price_low: float
    suggested_price_high: float
    suggested_category: str
    differentiate_from: Optional[str]
    brief_text: str


def _category_qty_shares(items):
    total = sum(i.annual_qty for i in items) or 1.0
    shares = {}
    for i in items:
        shares[i.category] = shares.get(i.category, 0.0) + i.annual_qty
    return {c: q / total * 100 for c, q in shares.items()}


def _classify_gap(item):
    reason = (item.reason or '').lower()
    if 'ghost-item' in reason or 'revenue floor' in reason:
        return 'ghost_revenue'
    if item.duplicate_of and ('duplicate' in reason or 'outsells' in reason):
        return 'duplication'
    if 'profitability rank 1' in reason and 'commuter' not in reason.split('profitability rank 1')[0][-40:]:
        return 'margin'
    if 'commuter score percentile' in reason:
        return 'commuter_fit'
    return 'popularity'


GAP_TYPE_LABELS = {
    'ghost_revenue': "Vanishingly small revenue — this slot wasn't earning its place at any price.",
    'duplication': "Duplicated another item on the menu — the gap isn't the category, it's the redundant format.",
    'margin': "Bottom-quintile margin for this menu with no growth trend to offset it.",
    'commuter_fit': "Poor fit for the dominant commuter segment (price/occasion mismatch), not a demand problem per se.",
    'popularity': "Weak, declining demand even after accounting for the other signals.",
}


def _price_band(items, category, exclude_name):
    same_cat = [i.price for i in items if i.category == category and i.name != exclude_name]
    if not same_cat:
        return None, None
    return min(same_cat), max(same_cat)


def build_replacement_briefs(items):
    """items: the full list of ItemInput after ae.run_engine() has run
    (recommendation/reason/category/roles all populated). Returns a list
    of ReplacementBrief, one per REMOVE item."""
    removed = [i for i in items if i.recommendation == 'REMOVE']
    if not removed:
        return []

    shares_before = _category_qty_shares(items)
    survivors = [i for i in items if i.recommendation != 'REMOVE']
    shares_after = _category_qty_shares(survivors) if survivors else {}

    briefs = []
    for item in removed:
        gap_type = _classify_gap(item)
        cat = item.category
        share_before = shares_before.get(cat, 0.0)
        share_after = shares_after.get(cat, 0.0)
        still_covered = share_after >= 5.0  # heuristic floor: category still has a meaningful presence

        lo, hi = _price_band(survivors, cat, item.name)
        if lo is None:
            # no survivors in this category at all -- use the removed item's own price as an anchor
            lo, hi = item.price * 0.85, item.price * 1.15
        differentiate_from = item.duplicate_of if gap_type == 'duplication' else None

        if gap_type == 'duplication':
            suggested_category = cat
            direction = (
                f"a genuinely different {cat.title()} concept — not another variation on "
                f"'{item.duplicate_of}', which is already covering this slot"
            )
        elif not still_covered:
            suggested_category = cat
            direction = (
                f"a new {cat.title()} item — removing this one drops the category from "
                f"{share_before:.1f}% to {share_after:.1f}% of total volume, which may be "
                f"thin coverage if {cat.title()} is meant to be a real part of the menu"
            )
        else:
            suggested_category = cat
            direction = (
                f"a {cat.title()} item if that slot is still wanted (the category still holds "
                f"{share_after:.1f}% of volume without it, so this is optional, not urgent)"
            )

        brief_text = (
            f"'{item.name}' is being removed: {GAP_TYPE_LABELS[gap_type]} "
            f"If replacing it, consider {direction}. It served the "
            f"{item.weekday_role} occasion on weekdays and {item.weekend_role} on weekends — "
            f"aim a replacement at that same real demand rather than guessing at a new occasion. "
            f"Price it in the ${lo:.2f}–${hi:.2f} range to match what similar items in this menu "
            f"actually command."
        )

        briefs.append(ReplacementBrief(
            removed_item=item.name, category=cat, reason_summary=item.reason,
            gap_type=gap_type, category_share_before_pct=share_before,
            category_share_after_pct=share_after, category_still_covered=still_covered,
            target_weekday_role=item.weekday_role, target_weekend_role=item.weekend_role,
            suggested_price_low=lo, suggested_price_high=hi,
            suggested_category=suggested_category, differentiate_from=differentiate_from,
            brief_text=brief_text,
        ))

    return briefs
