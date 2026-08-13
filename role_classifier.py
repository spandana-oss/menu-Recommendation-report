"""
role_classifier.py — heuristic assignment of Weekday Role / Weekend Role
and their "fit points" (0-50), which feed Section C's Worker Inflow Score
and Population Score.

IMPORTANT — this is NOT a validated formula like analysis_engine.py.
Occasion-role assignment is a judgment call (what occasion does this dish
realistically serve?), so this module encodes reasonable rules-of-thumb
inferred from price point and category, not a spec-defined calculation.

Confidence: on the real Grizz Grill menu, this heuristic's role labels
matched the hand-assigned ones (from the original expert-reviewed
workbook) for the majority of items, and produced commuter scores within
roughly 5-10 points of the real ones for most items — close enough to
rank-order items similarly, but NOT guaranteed to reproduce exact Refresh/
Keep boundary cases (Profitability Rank 3 + Commuter Percentile 40-59 is
a real trigger in Section I, so borderline items can flip).

Recommended usage: run this to get a first pass, then let a human
(or ask an assistant) spot-check the roles on items near recommendation
boundaries before trusting the output completely. The app's review step
lets you edit any item's role/fit points before running the engine.
"""

from dataclasses import dataclass


@dataclass
class RoleAssignment:
    weekday_role: str
    weekday_desc: str
    weekday_fit_points: float
    weekend_role: str
    weekend_desc: str
    weekend_fit_points: float


BREAKFAST_CATS = {'BREAKFAST', 'BREAKFAST SIDE'}


def classify_weekday(category, price, name):
    name_l = name.lower()
    if category in BREAKFAST_CATS:
        return 'Breakfast', f"Morning-format item fits the weekday breakfast occasion.", 40.0
    if price >= 17:
        return ('Business Meal',
                f"${price:.2f} price point fits expense-account / business-meal dining.", 42.0)
    if category == 'SIDE / OTHER' and price < 6:
        return 'Afternoon Snack', "Low-price, shareable format fits an afternoon snack occasion.", 30.0
    if category == 'APPETIZER':
        return 'Afternoon Snack', "Shareable appetizer format fits an afternoon snack occasion.", 32.0
    if 'wrap' in name_l or 'sandwich' in name_l or 'burger' in name_l:
        return 'Office Lunch', "Handheld, moderate-price format fits a weekday office-lunch order.", 38.0
    return 'Office Lunch', "Moderate price point fits a typical weekday office-lunch order.", 35.0


def classify_weekend(category, price, name):
    name_l = name.lower()
    if category in BREAKFAST_CATS:
        return 'Weekend Brunch', "Morning-format item fits weekend brunch.", 40.0
    if price >= 17:
        return 'Premium Dinner', f"${price:.2f} price point fits a weekend premium-dinner occasion.", 42.0
    if category == 'SIDE / OTHER':
        return 'Sports Event Special', "Shareable, casual side fits weekend sports-viewing occasions.", 30.0
    if category == 'APPETIZER':
        return 'Kids Favorite', "Approachable, shareable appetizer fits a kids'-favorite role.", 28.0
    if 'combo' in name_l or 'platter' in name_l:
        return 'Family Meal', "Multi-component/shareable format suits weekend family dining.", 38.0
    return 'Casual Dining', "General-purpose item fits weekend casual dining.", 33.0


def assign_roles(category, price, name):
    wd_role, wd_desc, wd_pts = classify_weekday(category, price, name)
    we_role, we_desc, we_pts = classify_weekend(category, price, name)
    return RoleAssignment(wd_role, wd_desc, wd_pts, we_role, we_desc, we_pts)
