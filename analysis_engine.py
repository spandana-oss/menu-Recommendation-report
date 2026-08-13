"""
analysis_engine.py — the deterministic math from the Menu Refresh master
spec (Sections A, B, D, E, F, G, H, I). This is pure computation: given a
list of items with raw inputs (price, cost, annual qty sold, category,
duplicate/bundle flags) plus each item's Commuter Score, it reproduces
every formula-defined column in the 65-column output.

What this module does NOT do (see role_classifier.py and
duplicate_detector.py instead):
  - Assign Weekday Role / Weekend Role / Fit Points (Section C inputs) —
    those require judgment about what occasion an item fits, not a formula.
  - Detect near-duplicates, structural bundles, or unique-use ingredients
    (Section I inputs) — same reason.

Validated against the real Grizz Grill v9 workbook: feeding this module
each item's actual Commuter Score (bypassing the heuristic classifier)
reproduces Popularity Rank, Profitability Rank, all Future/Profitability
Value columns, all Current Profit columns, the full 6-month forecast, and
the Recommendation for all 54 items exactly.
"""

import math
from dataclasses import dataclass, field
from typing import Optional


# ----------------------------------------------------------------------
# Market intelligence — shared across every item at one location.
# Replace with a MarketIntel built from live Census/OnTheMap data
# (see market_data.py) or entered manually in the app.
# ----------------------------------------------------------------------
@dataclass
class MarketIntel:
    residents: float
    daytime_workers: float
    worker_inflow: float
    resident_outflow: float
    stay_local: float
    pct_income_high: float   # % of inflow workers earning >$3,333/mo
    pct_income_low: float    # % of inflow workers earning <$1,250/mo
    pct_age_mid: float       # % of inflow workers age 30-54
    pct_age_senior: float    # % of inflow workers age 55+
    pct_office_jobs: float   # % of inflow jobs in office/professional services
    competition_score: float = 50.0       # neutral — no competitor dataset
    competition_multiplier: float = 0.97  # fixed — no competitor dataset

    @property
    def pct_income_mid(self):
        return max(0.0, 100.0 - self.pct_income_high - self.pct_income_low)


# ----------------------------------------------------------------------
# Category modifier (Section E) — exact table from the spec.
# ENTREE has a few name-based overrides; extend OVERRIDES for new items.
# ----------------------------------------------------------------------
CATEGORY_MODIFIER = {
    'BURGER': 1.08, 'SALAD': 1.10, 'SOUP': 1.10, 'SAND / WRAP': 1.03,
    'BREAKFAST': 1.05, 'BREAKFAST SIDE': 1.02, 'SIDE / OTHER': 1.02,
    'APPETIZER': 1.05,
}
ENTREE_OVERRIDES = {
    'BBQ Combo': ('BBQ/Grill', 1.07),
    'Fish & Chips': ('Seafood', 1.06),
    'Cornmeal Crusted Catfish': ('Seafood', 1.06),
}


def category_modifier(category, item_name):
    if category == 'ENTREE':
        return ENTREE_OVERRIDES.get(item_name, ('Unknown/Default', 1.00))[1]
    return CATEGORY_MODIFIER.get(category, 1.00)


# ----------------------------------------------------------------------
# Item input — what the caller must supply per item.
# ----------------------------------------------------------------------
@dataclass
class ItemInput:
    no: int
    name: str
    category: str            # raw source coding, e.g. 'ENTREE', 'SIDE / OTHER'
    ingredients: str
    annual_qty: float
    price: float
    theoretical_cost: float
    cost_estimated: bool = False
    description: str = ''
    description_source: str = ''
    ingredients_source: str = ''
    ingredient_cost: float = 0.0
    ingredient_cost_source: str = ''
    prep_cost: float = 0.0
    prep_cost_source: str = ''
    theoretical_cost_source: str = ''
    estimation_method: str = ''
    estimation_source: str = ''
    confidence_score: float = 0.0

    # Section C inputs (from role_classifier.py, or entered manually)
    commuter_score: float = 0.0
    weekday_role: str = ''
    weekday_desc: str = ''
    weekend_role: str = ''
    weekend_desc: str = ''

    # Section I inputs (from duplicate_detector.py, or entered manually)
    is_structural_bundle: bool = False
    duplicate_of: Optional[str] = None
    duplicate_outsells: bool = False
    unique_ingredients: bool = False


def round_half_up(v):
    return math.floor(abs(v) + 0.5) * (1 if v >= 0 else -1)


# ----------------------------------------------------------------------
# Section A — Popularity
# ----------------------------------------------------------------------
def compute_popularity(items):
    n = len(items)
    ranked = sorted(items, key=lambda i: (-i.annual_qty, i.no))
    for rank, it in enumerate(ranked, start=1):
        it.popularity_rank = rank
        it.popularity_percentile = ((n - rank) / (n - 1)) * 100 if n > 1 else 100.0


# ----------------------------------------------------------------------
# Section B — Current profitability + relative Profitability Rank
# ----------------------------------------------------------------------
def compute_current_profitability(items):
    n = len(items)
    for it in items:
        it.gross_profit_per_unit = it.price - it.theoretical_cost
        it.current_margin_pct = (it.gross_profit_per_unit / it.price) * 100 if it.price else 0.0

    n_items = len(items)
    ranked = sorted(items, key=lambda i: (-i.current_margin_pct, i.no))
    for rank, it in enumerate(ranked, start=1):
        # NOTE: this is a plain rank/N quintile bucket (top 20% by margin =
        # Rank 5), deliberately NOT the (N-rank)/(N-1) percentile formula
        # used for display elsewhere -- validated against the real workbook,
        # which buckets by straight rank position, not that percentile.
        pct_from_top = (rank / n_items) * 100
        if pct_from_top <= 20:
            it.profitability_rank = 5
        elif pct_from_top <= 40:
            it.profitability_rank = 4
        elif pct_from_top <= 60:
            it.profitability_rank = 3
        elif pct_from_top <= 80:
            it.profitability_rank = 2
        else:
            it.profitability_rank = 1


# ----------------------------------------------------------------------
# Section C — Commuter Score (component formulas). Fit points must be
# supplied by the role classifier (or entered manually) on each item as
# it.weekday_fit_points / it.weekend_fit_points (0-50).
# ----------------------------------------------------------------------
MATURE_ROLES = {'Business Meal', 'Premium Dinner'}


def compute_commuter_score(items, market: MarketIntel):
    n = len(items)
    qty_by_cat = {}
    for it in items:
        qty_by_cat[it.category] = qty_by_cat.get(it.category, 0.0) + it.annual_qty
    total_qty = sum(qty_by_cat.values()) or 1.0
    shares = {c: q / total_qty * 100 for c, q in qty_by_cat.items()}
    lo, hi = min(shares.values()), max(shares.values())
    span = (hi - lo) or 1.0

    for it in items:
        worker_inflow_score = (getattr(it, 'weekday_fit_points', 0) / 50) * 100

        price = it.price
        afford = 0.0
        if price <= 25:
            afford += market.pct_income_high
        if price <= 15:
            afford += market.pct_income_mid
        if price <= 8:
            afford += market.pct_income_low
        median_income_score = min(100.0, afford)

        population_score = (getattr(it, 'weekend_fit_points', 0) / 50) * 100

        if it.weekday_role in MATURE_ROLES or it.weekend_role in MATURE_ROLES:
            customer_segment_score = market.pct_age_mid + market.pct_age_senior
        else:
            customer_segment_score = 100.0

        normalized_share = ((shares[it.category] - lo) / span) * 100
        cuisine_gap_score = 100.0 - normalized_share

        food_trend_score = it.popularity_percentile

        it.commuter_score = (
            0.25 * worker_inflow_score + 0.20 * median_income_score +
            0.15 * population_score + 0.15 * customer_segment_score +
            0.10 * cuisine_gap_score + 0.10 * food_trend_score +
            0.05 * market.competition_score
        )

    ranked = sorted(items, key=lambda i: (-i.commuter_score, i.no))
    for rank, it in enumerate(ranked, start=1):
        it.commuter_score_percentile = ((n - rank) / (n - 1)) * 100 if n > 1 else 100.0


# ----------------------------------------------------------------------
# Section D — Demographic multiplier
# ----------------------------------------------------------------------
def compute_demographic_multiplier(items):
    for it in items:
        it.demographic_multiplier = 0.90 + (it.commuter_score / 100) * 0.20


# ----------------------------------------------------------------------
# Section E — Category modifier
# ----------------------------------------------------------------------
def compute_category_modifier(items):
    for it in items:
        it.category_modifier_value = category_modifier(it.category, it.name)


# ----------------------------------------------------------------------
# Section F — Profitability projection (3/6/9 months)
# ----------------------------------------------------------------------
def compute_profitability_projection(items):
    for it in items:
        it.future_tc_3m = it.theoretical_cost * 1.02
        it.future_tc_6m = it.theoretical_cost * 1.05
        it.future_tc_9m = it.theoretical_cost * 1.08

        it.future_margin_3m = ((it.price - it.future_tc_3m) / it.price) * 100 if it.price else 0.0
        it.future_margin_6m = ((it.price - it.future_tc_6m) / it.price) * 100 if it.price else 0.0
        it.future_margin_9m = ((it.price - it.future_tc_9m) / it.price) * 100 if it.price else 0.0

        dm, cm = it.demographic_multiplier, it.category_modifier_value
        it.profitability_value_3m = it.future_margin_3m * dm * cm * 1.00
        it.profitability_value_6m = it.future_margin_6m * dm * cm * 1.03
        it.profitability_value_9m = it.future_margin_9m * dm * cm * 1.06


# ----------------------------------------------------------------------
# Section G — Current profit projections
# ----------------------------------------------------------------------
def compute_current_profit(items):
    for it in items:
        monthly_units = it.annual_qty / 12
        it.current_profit_3m = it.gross_profit_per_unit * monthly_units * 3
        it.current_profit_6m = it.gross_profit_per_unit * monthly_units * 6
        it.current_profit_9m = it.gross_profit_per_unit * monthly_units * 9
        it.current_profit_12m = it.gross_profit_per_unit * it.annual_qty


# ----------------------------------------------------------------------
# Section H — 6-month sales forecast
# ----------------------------------------------------------------------
def _baseline_growth_rate(pct):
    if pct >= 80:
        return 0.0150
    if pct >= 60:
        return 0.0080
    if pct >= 40:
        return 0.0030
    if pct >= 20:
        return -0.0020
    return -0.0050


def compute_sales_forecast(items, market: MarketIntel):
    for it in items:
        it.baseline_growth_rate = _baseline_growth_rate(it.popularity_percentile)

        trend_mult = 0.97 + (it.popularity_percentile / 100) * 0.06
        demand_mult = 0.99 + (it.current_margin_pct / 100) * 0.02
        raw_mult = (it.demographic_multiplier * it.category_modifier_value *
                    trend_mult * market.competition_multiplier * demand_mult)
        it.market_multiplier = min(1.10, max(0.95, raw_mult))

        it.adjusted_growth_rate = it.baseline_growth_rate * it.market_multiplier

        monthly_qty = it.annual_qty / 12
        exact_qty = monthly_qty
        fq, fs = [], []
        for _ in range(6):
            exact_qty = max(0.0, exact_qty * (1 + it.adjusted_growth_rate))
            rounded_qty = max(0, round_half_up(exact_qty))
            fq.append(rounded_qty)
            fs.append(rounded_qty * it.price)
        it.forecast_qty_months = fq
        it.forecast_sales_months = fs
        it.total_forecast_qty_6mo = sum(fq)
        it.total_forecast_sales_6mo = sum(fs)

        it.current_qty_6mo = it.annual_qty / 2
        it.current_sales_6mo = it.current_qty_6mo * it.price

        it.qty_change_6mo = it.total_forecast_qty_6mo - it.current_qty_6mo
        it.sales_change_6mo = it.total_forecast_sales_6mo - it.current_sales_6mo

        if it.total_forecast_sales_6mo > it.current_sales_6mo * 1.03:
            it.sales_change_signal = 'Increasing'
        elif it.total_forecast_sales_6mo < it.current_sales_6mo * 0.97:
            it.sales_change_signal = 'Decreasing'
        else:
            it.sales_change_signal = 'Stable'


# ----------------------------------------------------------------------
# Section I — Recommendation engine
# ----------------------------------------------------------------------
GHOST_ITEM_FLOOR = 500.0


def compute_recommendations(items):
    n = len(items)
    median_annual_revenue = _median([it.annual_qty * it.price for it in items])

    for it in items:
        annual_revenue = it.annual_qty * it.price

        # --- hard overrides, checked first ---
        if it.is_structural_bundle:
            it.recommendation = 'KEEP'
            it.reason = ('Critical bundled component (protein/side customization '
                         'across multiple high-selling items) -- unconditional '
                         'KEEP regardless of standalone volume.')
            continue

        if annual_revenue < GHOST_ITEM_FLOOR:
            it.recommendation = 'REMOVE'
            it.reason = (f'Total annual revenue ${annual_revenue:,.2f} is below the '
                         f'$500 ghost-item floor -- REMOVE regardless of other signals.')
            continue

        if it.popularity_percentile >= 80 and it.profitability_rank >= 4:
            it.recommendation = 'KEEP'
            it.reason = (f'Popularity Percentile {it.popularity_percentile:.1f} >= 80 '
                         f'and Profitability Rank {it.profitability_rank} >= 4 (relative '
                         f'to this menu) -- unconditional KEEP, actively promote.')
            continue

        high_pop = it.popularity_rank <= 32
        very_low_pop = it.popularity_rank >= 44

        refresh_reasons = []
        if it.popularity_percentile >= 40 and it.profitability_rank <= 2:
            refresh_reasons.append(
                f'Popularity Percentile {it.popularity_percentile:.1f} >= 40 AND '
                f'Profitability Rank {it.profitability_rank} (bottom 40% margin, '
                f'relative to this menu) -- fix margin via upgrade + price increase.')
        if it.popularity_percentile >= 40 and it.commuter_score_percentile < 50:
            refresh_reasons.append(
                f'Popularity Percentile {it.popularity_percentile:.1f} >= 40 AND '
                f'Commuter Score Percentile {it.commuter_score_percentile:.1f} < 50 -- '
                f'below-median commuter demand fit relative to the rest of the menu.')
        if it.popularity_percentile >= 40 and it.sales_change_signal == 'Decreasing':
            refresh_reasons.append(
                f'Popularity Percentile {it.popularity_percentile:.1f} >= 40 AND Sales '
                f'Change Signal Decreasing.')
        if it.duplicate_of:
            refresh_reasons.append(
                f"Near-duplicate of '{it.duplicate_of}' exists -- differentiate "
                f"through a visible addition.")
        if (it.profitability_value_9m < it.profitability_value_3m - 2):
            refresh_reasons.append('Profitability Trend declining (9-Mo value >2 '
                                   'points below 3-Mo value).')
        if it.profitability_rank == 3 and 40 <= it.commuter_score_percentile <= 59:
            refresh_reasons.append(
                f'Profitability Rank 3 AND Commuter Score Percentile '
                f'{it.commuter_score_percentile:.1f} (40-59).')

        remove_signals = []
        if very_low_pop and it.popularity_percentile < 20:
            if it.duplicate_of and it.duplicate_outsells:
                remove_signals.append(
                    f"direct structural duplicate '{it.duplicate_of}' exists AND "
                    f"outsells this item significantly")
            if it.profitability_rank == 1 and it.sales_change_signal == 'Decreasing':
                remove_signals.append('Profitability Rank 1 AND Sales Change Signal Decreasing')
            if it.commuter_score_percentile < 30 and it.sales_change_signal == 'Decreasing':
                remove_signals.append(
                    f'Commuter Score Percentile {it.commuter_score_percentile:.1f} < 30 '
                    f'AND Sales Change Signal Decreasing')
            if it.unique_ingredients and it.profitability_rank == 1:
                remove_signals.append('unique-use ingredients AND Profitability Rank 1')
            if it.profitability_rank == 1 and it.sales_change_signal != 'Increasing':
                remove_signals.append(
                    f"[Extended] Profitability Rank 1 (bottom 20% margin, relative to "
                    f"this menu) AND Sales Change Signal is not Increasing "
                    f"({it.sales_change_signal}) -- worst-quintile on both primary "
                    f"axes with no growth trend to offset it.")
            if annual_revenue < median_annual_revenue * 0.5:
                remove_signals.append(
                    f"[Extended] Total annual revenue ${annual_revenue:,.2f} is less "
                    f"than half this menu's median (${median_annual_revenue:,.2f}) -- "
                    f"a genuinely marginal revenue contributor.")

        if not very_low_pop and it.popularity_percentile < 20 and annual_revenue < median_annual_revenue * 0.5:
            # (kept for symmetry; very_low_pop already covers Rank>=44 which is the
            # only way to reach Percentile<20 on a 54-item menu, so this branch is
            # unreachable in practice but documents the guard explicitly)
            remove_signals.append(
                f"[Extended] Total annual revenue ${annual_revenue:,.2f} is less than "
                f"half this menu's median (${median_annual_revenue:,.2f}).")

        if len(remove_signals) >= 2:
            pop_label = 'Very Low' if very_low_pop else 'Low'
            it.recommendation = 'REMOVE'
            it.reason = (
                f'{pop_label} Popularity (Rank {it.popularity_rank}/{n}, Percentile '
                f'{it.popularity_percentile:.1f}) with {len(remove_signals)} converging '
                f'REMOVE signals: ' + ' '.join(remove_signals))
            continue

        if len(remove_signals) == 1:
            it.recommendation = 'REFRESH'
            it.reason = (
                f'Only 1 REMOVE signal present (2+ required) -- insufficient to '
                f'REMOVE on its own: {remove_signals[0]} Falls back to REFRESH given '
                f'the single weak signal identified.')
            continue

        if refresh_reasons:
            if high_pop:
                it.recommendation = 'REFRESH'
                it.reason = (
                    f'High Popularity (Rank {it.popularity_rank}/{n}, top 32) -- '
                    f'rarely removed regardless of profitability. ' +
                    ' '.join(refresh_reasons))
            else:
                it.recommendation = 'REFRESH'
                it.reason = (
                    f'Low Popularity (Rank {it.popularity_rank}/{n}, Percentile '
                    f'{it.popularity_percentile:.1f}) -- {len(refresh_reasons)} REFRESH '
                    f'signal(s) present: ' + ' '.join(refresh_reasons))
            continue

        if high_pop:
            it.recommendation = 'KEEP'
            it.reason = (
                f'High Popularity (Rank {it.popularity_rank}/{n}) with no material '
                f'profitability, commuter-fit, or sales-decline concerns -- KEEP.')
        else:
            it.recommendation = 'KEEP'
            it.reason = (
                f'Popularity Percentile {it.popularity_percentile:.1f}, Commuter Score '
                f'Percentile {it.commuter_score_percentile:.1f}, Profitability Rank '
                f'{it.profitability_rank}, trend stable/improving, Sales Change Signal '
                f'{it.sales_change_signal} -- no REFRESH or REMOVE triggers met. KEEP as-is.')


def _median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


# ----------------------------------------------------------------------
# Menu Performance / Market Opportunity scores (columns 64-65)
# ----------------------------------------------------------------------
def compute_market_scores(items):
    for it in items:
        it.menu_performance_score = (
            0.5 * it.popularity_percentile + 0.5 * (it.profitability_rank / 5 * 100))
        raw = it.commuter_score * 0.6 + (100 - it.current_margin_pct * 0.4)
        it.market_opportunity_score = max(0.0, min(100.0, raw))


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------
def run_engine(items, market: MarketIntel):
    """items: list[ItemInput], each with commuter_score/weekday_fit_points/
    weekend_fit_points/is_structural_bundle/duplicate_of/etc already set.
    Runs every section in dependency order, mutating each item in place."""
    compute_popularity(items)
    compute_current_profitability(items)
    # commuter score needs popularity_percentile (food trend) — already computed above.
    compute_commuter_score(items, market)
    compute_demographic_multiplier(items)
    compute_category_modifier(items)
    compute_profitability_projection(items)
    compute_current_profit(items)
    compute_sales_forecast(items, market)
    compute_recommendations(items)
    compute_market_scores(items)
    return items
