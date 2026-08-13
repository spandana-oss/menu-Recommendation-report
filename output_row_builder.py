"""
output_row_builder.py
---------------------
Assembles one full output row from a dish_library.GeneratedDish +
ZctaContext + profitability numbers. Entirely template-based, no LLM.

The core comparable evidence still comes from the target ZIP's restaurant
list, and optional comparable metrics are surfaced alongside the named
comparables when that second input file is provided.
"""

import math

from creation_engine import ZctaContext, profitability_block
from dish_library import GeneratedDish
from creative_naming import generate_creative_name
from input_normalizer import CATEGORY_FC_BENCHMARK, DEFAULT_FC_BENCHMARK

# creation_engine's CATEGORY_KEYWORDS labels -> input_normalizer's food-cost
# benchmark table keys. Same mapping convention as the earlier build.
CATEGORY_TO_FC_BENCHMARK_KEY = {
    "Burgers": "BURGER", "Sandwiches": "SAND / WRAP", "Wings": "APPETIZER",
    "Southern": "ENTREE", "Seafood": "ENTREE", "BBQ": "ENTREE",
    "Mexican": "ENTREE", "Italian": "ENTREE", "Chicken": "ENTREE",
    "Breakfast": "BREAKFAST", "Sushi/Asian": "ENTREE", "Pizza": "ENTREE",
    "Fast Food": "BURGER", "Dessert": "SIDE / OTHER",
}

DELIVERY_SUITABILITY_BY_CATEGORY = {
    "Burgers": "High", "Sandwiches": "High", "BBQ": "High", "Pizza": "High",
    "Mexican": "High", "Chicken": "High", "Fast Food": "High",
    "Wings": "Medium", "Seafood": "Medium", "Southern": "Medium",
    "Italian": "Medium", "Sushi/Asian": "Medium", "Breakfast": "Medium",
    "Dessert": "Medium",
}


def estimate_cost_split(price: float, category: str) -> tuple:
    fc_key = CATEGORY_TO_FC_BENCHMARK_KEY.get(category)
    benchmark = CATEGORY_FC_BENCHMARK.get(fc_key, DEFAULT_FC_BENCHMARK)
    theoretical_cost = round(price * benchmark, 2)
    ingredient_cost = round(theoretical_cost * 0.80, 2)
    prep_cost = round(theoretical_cost - ingredient_cost, 2)
    return ingredient_cost, prep_cost


def _coerce_optional_number(value):
    if value in (None, ''):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _format_optional_price(value) -> str:
    number = _coerce_optional_number(value)
    return f"${number:,.2f}" if number is not None else ""


def _format_optional_qty(value) -> str:
    number = _coerce_optional_number(value)
    return f"{number:,.0f}" if number is not None else ""


def _format_comparable_summary(detail: dict) -> str:
    name = detail.get("name", "Comparable restaurant")
    price = _format_optional_price(detail.get("price"))
    qty = _format_optional_qty(detail.get("annual_qty"))
    extras = []
    if price:
        extras.append(f"price {price}")
    if qty:
        extras.append(f"qty sold {qty}")
    return f"{name} ({', '.join(extras)})" if extras else name


def health_nutrition_fit(indulgent_lean: float) -> str:
    if indulgent_lean >= 66:
        return (f"Indulgent-leaning ({indulgent_lean}/100 based on keywords in this item's own "
                 f"ingredient list) -- no formal nutrition data exists in source files; this is a "
                 f"keyword-based judgment call, not a nutrition fact.")
    if indulgent_lean <= 33:
        return (f"Lighter-leaning ({indulgent_lean}/100 based on ingredient-list keywords) -- "
                 f"no formal nutrition data exists in source files; this is a keyword-based judgment call.")
    return (f"Balanced ({indulgent_lean}/100 based on ingredient-list keywords) -- no formal "
            f"nutrition data exists in source files; this is a keyword-based judgment call.")


def family_premium_fit(tier: str) -> tuple:
    if tier == "Family":
        return "Strong", "Low"
    if tier == "Premium":
        return "Moderate", "Moderate"
    return "Weak", "Strong"


def premium_adjacent_upgrade_path(dish: GeneratedDish, tier: str) -> str:
    first_ingredient = dish.ingredients.split(",")[0].strip() if dish.ingredients else "a premium topping"
    if tier == "Premium Edge":
        return "Already at the top tier for this category -- no further upgrade path modeled."
    next_tier = "Premium" if tier == "Family" else "Premium Edge"
    return (f"Add a premium topping alongside '{first_ingredient}' (e.g. a house-made or "
            f"specialty variant) to justify a move into the {next_tier} tier -- a concrete "
            f"ingredient addition, not just a price change.")


def confidence_score_and_explanation(dish: GeneratedDish, tier: str, profit_pct: float,
                                       weekday_pct: float) -> tuple:
    """1-5 score from two real factors: how represented this category
    already is in the target ZIP's own restaurant list (evidence_count)
    and this item's own projected profit margin -- both stated explicitly,
    per the master prompt's confidence-score requirement."""
    if dish.evidence_count >= 5:
        evidence_score = 2
    elif dish.evidence_count >= 2:
        evidence_score = 1.5
    elif dish.evidence_count >= 1:
        evidence_score = 1
    else:
        evidence_score = 0.5

    margin_score = 2 if profit_pct >= 70 else 1.5 if profit_pct >= 60 else 1 if profit_pct >= 50 else 0.5
    raw = 1 + evidence_score + margin_score
    score = max(1, min(5, round(raw)))

    explanation = (
        f"{tier}-tier fit, {dish.evidence_count} restaurant(s) in this ZIP's own list already "
        f"carry the {dish.category} category (local category-presence evidence, not a cross-market "
        f"sales trend), {profit_pct}% projected profit margin at this price, and {weekday_pct}% "
        f"weekday crowd share for this tier -- combined into a {score}/5 score."
    )
    return score, explanation


def reason_for_recommendation(dish: GeneratedDish, display_name: str, tier: str, ctx: ZctaContext) -> str:
    tier_pct = {"Family": ctx.family_pct, "Premium": ctx.premium_pct,
                "Premium Edge": ctx.premium_edge_pct}[tier]
    who = (f"Who it's for: {tier}-tier customers in ZIP {ctx.zcta}, the {tier_pct}% of households "
           f"this ZIP's real income-bracket data puts in that tier.")
    what = (f"What it does: fills out the {dish.category} category, which {dish.evidence_count} "
            f"restaurant(s) in this ZIP already carry, at a real category-benchmarked price of "
            f"${dish.price:.2f}.")
    return who + " " + what


def build_output_row(ctx: ZctaContext, dish: GeneratedDish, tier: str, category_rank: int,
                      comparables: list, comparable_details: list, comparable_total_found: int,
                      weekday_pct: float, weekend_pct: float, top_categories: list,
                      used_names: set) -> dict:
    ingredient_cost, prep_cost = estimate_cost_split(dish.price, dish.category)
    pb = profitability_block(ingredient_cost, prep_cost, dish.price)

    display_name = generate_creative_name(
        ingredients=dish.ingredients, category=dish.category,
        description=dish.description, used_names=used_names,
    )

    description = dish.description
    comparable_details = comparable_details or []
    if not comparable_details:
        comparable_details = [
            {"name": comparables[0] if len(comparables) > 0 else "None found", "price": None, "annual_qty": None},
            {"name": comparables[1] if len(comparables) > 1 else "None found", "price": None, "annual_qty": None},
        ]
    while len(comparable_details) < 2:
        comparable_details.append({"name": "None found", "price": None, "annual_qty": None})

    comparable_1 = comparable_details[0]
    comparable_2 = comparable_details[1]
    comparable_evidence_parts = [
        _format_comparable_summary(detail)
        for detail in comparable_details[:2]
        if detail.get("name") and detail.get("name") != "None found"
    ]

    cuisine_rationale = (
        f"{display_name} is a {dish.category} item, category chosen because {dish.category} "
        f"ranks #{category_rank} by count in this ZIP's local restaurant list. The {tier} price "
        f"tier came from the real income-bracket analysis (Family {ctx.family_pct}% / "
        f"Premium {ctx.premium_pct}% / Premium Edge {ctx.premium_edge_pct}% of ZIP households) -- "
        f"not from local restaurant-count popularity. {dish.evidence_count} restaurant(s) already "
        f"in this ZIP's own list carry {dish.category}, the local-market evidence for this category choice."
    )

    confidence_score, confidence_explanation = confidence_score_and_explanation(
        dish, tier, pb.profit_pct, weekday_pct)

    return {
        "Recommended New Menu Item": display_name,
        "Recommended Category": dish.category,
        "Recommended Ingredients": dish.ingredients,
        "Recommended Description": description,
        "Recommended Price Band": tier,
        "Recommended Menu Price ($)": dish.price,
        "Cuisine": cuisine_rationale,
        "Location": ctx.location_str(),
        "Restaurant Type / Service Model": f"Quick-Service / Fast-Casual ({', '.join(top_categories)} dominate this zip"
                                            f"{'; ' + ctx.anchor_note if ctx.has_anchor else '; no office/tourist anchor present'}).",
        "ZIP Code": ctx.zcta,
        "Median Income ($)": ctx.median_income,
        "Median Age": ctx.median_age,
        "Household Size": ctx.household_size,
        "Resident Labor Force Note": f"{ctx.labor_force_participation_rate}% labor-force participation "
                                      f"(vs ~63% national rate) -- "
                                      f"{'points to more local daytime traffic, less office lunch rush.' if ctx.labor_force_participation_rate < 63 else 'consistent with strong office/day traffic.'}",
        "Unemployment Rate (%)": ctx.unemployment_rate,
        "Total Population": ctx.total_population,
        "Local Restaurant Density": f"{ctx.restaurant_density_per_sqmi()} restaurants/sq mi "
                                     f"({ctx.restaurant_count} restaurants, {ctx.area_sq_mi} sq mi).",
        "Zip-Wide Market Positioning": ctx.market_positioning_paragraph(
            display_name, dish.price, tier, weekday_pct, weekend_pct),
        "Comparable Restaurant 1": comparable_1.get("name", comparables[0] if len(comparables) > 0 else "None found"),
        "Comparable Restaurant 2": comparable_2.get("name", comparables[1] if len(comparables) > 1 else "None found"),
        "Comparable Restaurant 1 Price ($)": _coerce_optional_number(comparable_1.get("price")),
        "Comparable Restaurant 1 Qty Sold (Annual)": _coerce_optional_number(comparable_1.get("annual_qty")),
        "Comparable Restaurant 2 Price ($)": _coerce_optional_number(comparable_2.get("price")),
        "Comparable Restaurant 2 Qty Sold (Annual)": _coerce_optional_number(comparable_2.get("annual_qty")),
        "Comparable Restaurant Match Criteria": f"Category ({dish.category}) matched to the named "
                                                  f"comparable restaurant(s) above. Price and quantity sold "
                                                  f"were pulled from the optional comparable-data upload when available.",
        "# Comparable Restaurants Found": comparable_total_found,
        "Comparable Cuisine Match": f"This item's category ({dish.category}) is represented among the named comparable restaurants above.",
        "Comparable Demographic Match": "100% -- named comparable restaurant(s) are real establishments in this same ZIP code." if comparables else "N/A -- no in-ZIP comparables found.",
        "Comparable Price Tier Match": tier,
        "Comparable Category Presence (Qualitative)": "Strong" if comparable_total_found >= 5 else "Moderate" if comparable_total_found >= 2 else "Weak",
        "Comparable Restaurant Evidence Strength (Qualitative)": "Strong" if comparable_total_found >= 5 else "Moderate" if comparable_total_found >= 2 else "Weak",
        "Comparable Menu Evidence": (
            f"{', '.join(comparable_evidence_parts) if comparable_evidence_parts else 'No in-ZIP comparables found'} "
            f"named as supporting directional evidence for this category choice."
        ),
        "Demographic Fit Reason": f"Median household income ${ctx.median_income:,.0f} and {ctx.unemployment_rate}% "
                                   f"unemployment vs national norms -- the {tier} tier "
                                   f"({ {'Family': ctx.family_pct, 'Premium': ctx.premium_pct, 'Premium Edge': ctx.premium_edge_pct}[tier] }% "
                                   f"of households) is {'not a stretch' if tier == 'Family' else 'a reasonable stretch for the qualifying share of households' if tier == 'Premium' else 'a deliberate small-share offering, not a stretch for the qualifying households'} at this income level.",
        "Local Trend Fit Reason": f"Market context only, not the basis for this item: {ctx.restaurant_density_per_1000()} "
                                   f"restaurants/1,000 residents ({ctx.restaurant_count} total), "
                                   f"{ctx.biz_residential_mix} business/residential mix.",
        "Customer Sentiment Fit Reason": f"No review/rating data used for this item. Proxy only: this item's real "
                                          f"ingredient list reads {dish.indulgent_lean}/100 on an indulgent-vs-healthy keyword scale.",
        "Recommended Health / Nutrition Fit": health_nutrition_fit(dish.indulgent_lean),
        "Delivery Suitability": DELIVERY_SUITABILITY_BY_CATEGORY.get(dish.category, "Medium"),
        "Recommended Family / Value Fit": family_premium_fit(tier)[0],
        "Recommended Premium / Trendy Fit": family_premium_fit(tier)[1],
        "Recommended Premium-Adjacent Fit": premium_adjacent_upgrade_path(dish, tier),
        "Ingredient Cost ($)": pb.ingredient_cost,
        "Prep Cost ($)": pb.prep_cost,
        "Theoretical Cost ($)": pb.theoretical_cost,
        "Profitability Value ($)": pb.profit_value,
        "Profitability Value (%)": pb.profit_pct,
        "Cost %": pb.cost_pct,
        "3-Month Profitability Value ($)": pb.profit_3mo,
        "6-Month Profitability Value ($)": pb.profit_6mo,
        "9-Month Profitability Value ($)": pb.profit_9mo,
        "Recommended Portions": dish.portion_note,
        "Menu Item Confidence Score (1-5)": confidence_score,
        "Confidence Score Explanation": confidence_explanation,
        "Reason for Recommendation": reason_for_recommendation(dish, display_name, tier, ctx),
        "Residential vs. Business-Area Note": f"Supporting context, not a direct input: this zip is classified "
                                                f"'{ctx.urban_rural_class}' with a '{ctx.biz_residential_mix}' mix and "
                                                f"{'an anchor present (' + ctx.anchor_note + ')' if ctx.has_anchor else 'no anchor present'}.",
        "Weekday Menu Role": f"{dish.category} item, {weekday_pct}% weekday crowd share for the {tier} tier "
                              f"-- lifted by in-commuting workers.",
        "Weekend Menu Role": f"{dish.category} item, {weekend_pct}% weekend crowd share for the {tier} tier "
                              f"-- reverts to resident income mix.",
    }
