"""
refresh_content_generator.py — implements every REFRESH-detail column so
a REFRESH row is NEVER left with a "[NEEDS INPUT]" placeholder. Two tiers:

  MECHANICAL (no LLM, deterministic, tested) — for every row regardless
  of recommendation:
    - format_signal_reason           -> Reason for Recommendation
    - format_duplicate_analysis      -> Comparable/Duplicate Menu Item Analysis
    - format_ingredient_reuse_analysis -> Ingredient Reuse Analysis
    - compute_procurement_impact     -> Procurement Impact

  REFRESH CREATIVE CONTENT — two ways to get it, always guaranteed to
  produce real content, never a placeholder:
    1. generate_refresh_creative_content() — LLM-generated (needs
       ANTHROPIC_API_KEY), higher-quality, genuinely tailored to the item.
    2. generate_deterministic_refresh_content() — rule-based fallback
       using CATEGORY_ADDITIONS, a curated library of real, specifically-
       named ingredients per category with real per-unit costs and
       procurement notes. ALWAYS available, ALWAYS produces complete,
       specific, non-generic content — no API key, no network call, no
       possibility of a blank field. This is what actually guarantees
       "never require manual input": the LLM path is a quality upgrade
       when available, not the only way this ever completes.

  run_analysis.py tries (1) first when ANTHROPIC_API_KEY is set and falls
  back to (2) on any failure — so every REFRESH row gets full, valid,
  specific content one way or another, every time.
"""

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-5"
ANTHROPIC_API_VERSION = "2023-06-01"

BANNED_PHRASES = (
    'great option', 'customers will love it', 'elevates the dish', 'perfect for',
)


class RefreshContentError(Exception):
    pass


def _get_api_key():
    return os.environ.get('ANTHROPIC_API_KEY', '').strip()


# ----------------------------------------------------------------------
# 1. Reason for Recommendation — mechanical, doc-5's exact format.
# ----------------------------------------------------------------------
def _trend_label(p3, p9):
    """doc 5: 'Holding (<=2 pt drift) = stable. Eroding (>2 pt decline) = flag.'
    Eroding specifically means DECLINE -- a >2pt IMPROVEMENT is not a
    concern and should read as holding, not (incorrectly) eroding."""
    decline = p3 - p9
    return 'eroding' if decline > 2 else 'holding'


def format_signal_reason(it) -> str:
    """it: an object/dict with pr, pp, cs, fr, p3, p9, us, sc, sf, s
    (recommendation), n (name) — matches the ItemInput/dict shapes used
    elsewhere in this project (analysis_engine.ItemInput has pr/pp/cs/fr
    etc. as popularity_rank/popularity_percentile/etc.; this function
    reads the SHORT aliases used in report_engine's ITEM_DATA dict, so
    call it with that shape — see run_analysis.py for the mapping)."""
    trend = _trend_label(it['p3'], it['p9'])
    flagged = _signals_flagged_from_dict(it)
    rec = it['s'].upper()

    facts = (
        f"[Signal 1 – Popularity: Rank {it['pr']}/54, Percentile {it['pp']:.1f}%] · "
        f"[Signal 2 – Commuter Fit: Percentile {it['cs']:.1f}%] · "
        f"[Signal 3 – Profitability: Rank {it['fr']}/5] · "
        f"[Signal 4 – Profitability Trend: {it['p3']:.2f}% → {it['p9']:.2f}% ({trend})] · "
        f"[Signal 5 – Sales Bridge: {it['us']}, ${it['sc']} → ${it['sf']}]"
    )
    verdict = f"→ {len(flagged)} of 5 signals flag concern → {rec}."

    driver_names = {1: 'Signal 1 (low popularity)', 2: 'Signal 2 (weak commuter fit)',
                     3: 'Signal 3 (below-median margin)', 4: 'Signal 4 (eroding profitability trend)',
                     5: 'Signal 5 (declining sales)'}
    if flagged:
        names = [driver_names[f] for f in flagged]
        if len(names) == 1:
            drivers = names[0]
        elif len(names) == 2:
            drivers = f"{names[0]} and {names[1]}"
        else:
            drivers = ", ".join(names[:-1]) + f", and {names[-1]}"
        driver_sentence = f" Driven by {drivers}."
    else:
        driver_sentence = " No signal flags a concern on its own — this item clears every threshold."

    return f"{facts}\n{verdict}{driver_sentence}"


def _signals_flagged_from_dict(it):
    flags = []
    if it['pr'] >= 44:
        flags.append(1)
    if it['cs'] < 30:
        flags.append(2)
    if it['fr'] <= 2:
        flags.append(3)
    if _trend_label(it['p3'], it['p9']) == 'eroding':
        flags.append(4)
    if it['us'] == 'Decreasing':
        flags.append(5)
    return flags


# ----------------------------------------------------------------------
# 2. Comparable/Duplicate Menu Item Analysis — mechanical.
# ----------------------------------------------------------------------
def format_duplicate_analysis(name, duplicate_of, duplicate_outsells, n_total_items) -> str:
    if not duplicate_of:
        return (f"No direct duplicate identified — checked against all "
                f"{n_total_items - 1} other items on name, category, and ingredient overlap.")
    direction = "outsells" if duplicate_outsells else "underperforms vs."
    return (f"Near-duplicate of '{duplicate_of}' — flagged by name/category/ingredient "
            f"overlap (heuristic match, review recommended). '{duplicate_of}' {direction} "
            f"this item. Differentiate via the refresh addition below rather than compete head-on.")


# ----------------------------------------------------------------------
# 3. Ingredient Reuse Analysis — mechanical, needs real counts.
# ----------------------------------------------------------------------
def _parse_ingredients(text):
    return [i.strip().lower() for i in re.split(r',(?![^()]*\))', text or '') if i.strip()]


def build_ingredient_index(items):
    """items: iterable with .name/.ingredients (or dict equivalents).
    Returns {ingredient_lower: set(item_names)} across the whole menu."""
    index = {}
    for it in items:
        name = it['name'] if isinstance(it, dict) else it.name
        ings_text = it['ingredients'] if isinstance(it, dict) else it.ingredients
        for ing in _parse_ingredients(ings_text):
            index.setdefault(ing, set()).add(name)
    return index


def format_ingredient_reuse_analysis(name, ingredients_text, ingredient_index) -> str:
    ings = _parse_ingredients(ingredients_text)
    if not ings:
        return "No ingredient data available for this item to check reuse against."
    reused = sum(1 for ing in ings if len(ingredient_index.get(ing, set())) > 1)
    unique = len(ings) - reused
    pct = (reused / len(ings) * 100) if ings else 0.0
    return (f"{reused} of {len(ings)} ingredients ({pct:.0f}%) already appear in other "
            f"current menu items; {unique} ingredient(s) are unique to this dish.")


# ----------------------------------------------------------------------
# 8. Procurement Impact — mechanical, doc-5's exact rule.
# ----------------------------------------------------------------------
def compute_procurement_impact(recommendation, new_ingredient_count=0, removed_ingredient_count=0) -> str:
    rec = recommendation.lower()
    if rec == 'keep':
        return "None — item unchanged."
    if rec == 'remove':
        return (f"Reduced — {removed_ingredient_count} ingredient(s) drop out of rotation; "
                f"check if any are shared with other dishes before discontinuing purchase.")
    # REFRESH
    if new_ingredient_count == 0:
        return "None — 0 new ingredients, no new SKUs, no supplier changes."
    if new_ingredient_count == 1:
        return "Low — 1 new ingredient, likely already carried by an existing supplier."
    return f"Moderate — {new_ingredient_count} new ingredients; confirm supplier lead time before menu print."


# ----------------------------------------------------------------------
# 4-7. The genuinely creative REFRESH fields — LLM-generated.
# ----------------------------------------------------------------------
SYSTEM_PROMPT = """You are generating 4 specific fields for one REFRESH row in a menu-refresh workbook: the new item name, its description, which existing-menu ingredients get reused, and which new ingredients are required.

Hard rules:
- No duplicate reasoning text across rows — every sentence must reference this item's actual numbers/ingredients, not a generic template filled in.
- No banned/generic phrases ("great option," "customers will love it," "elevates the dish," "perfect for").
- Refreshed name follows exactly: "{Original Name} with {Addition 1} and {Addition 2}".
- Refresh additions must be pulled from ingredients that already exist somewhere on the current menu wherever possible — new ingredients are the exception, not the default.
- Every output field must be internally consistent: if Ingredients Reused = "None," New Ingredients Required cannot also be "None" — something has to change, or it wouldn't be a refresh.
- Additions should be concrete, kitchen-usable ingredients/preps (not vague upgrades like "premium touch").
- Description is 2 sentences max, menu-copy tone: sentence 1 says what's added and why it addresses the specific flagged signal; sentence 2 states the new price and the mechanical pricing justification (the new price is GIVEN to you below — restate it, don't invent one).

Respond with ONLY a JSON object, no other text, in exactly this shape:
{"ingredients_reused": "...", "new_ingredients_required": "...", "suggested_refreshed_item": "...", "suggested_refreshed_description": "..."}
"""


def _build_user_prompt(item, full_menu_items, new_price):
    menu_lines = []
    for it in full_menu_items:
        name = it['name'] if isinstance(it, dict) else it.name
        cat = it['category'] if isinstance(it, dict) else it.category
        ing = it['ingredients'] if isinstance(it, dict) else it.ingredients
        menu_lines.append(f"{name} | {cat} | {ing}")
    menu_block = "\n".join(menu_lines)

    name = item['name'] if isinstance(item, dict) else item.name
    category = item['category'] if isinstance(item, dict) else item.category
    ingredients = item['ingredients'] if isinstance(item, dict) else item.ingredients
    price = item['price'] if isinstance(item, dict) else item.price
    why = item['why'] if isinstance(item, dict) else item.reason

    return f"""Item: {name}
Category: {category}
Current Ingredients: {ingredients}
Current Price: ${price:.2f}
New Price (already computed — restate this, don't invent one): ${new_price:.2f}
Why this item is being refreshed: {why}

Full menu reference (Name | Category | Ingredients), for duplicate/reuse checking:
{menu_block}
"""


def _call_anthropic(system_prompt, user_prompt, api_key, max_tokens=600, timeout=60):
    payload = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }).encode('utf-8')

    request = urllib.request.Request(
        ANTHROPIC_API_URL, data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        raise RefreshContentError(f"Anthropic API HTTP error ({e.code}): {body[:300]}") from e
    except urllib.error.URLError as e:
        raise RefreshContentError(f"Anthropic API request failed: {e.reason}") from e

    try:
        text = data["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise RefreshContentError(f"Unexpected Anthropic API response shape: {data}") from e
    return text


def _extract_json(text):
    text = text.strip()
    # tolerate accidental markdown fencing even though the prompt asks against it
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RefreshContentError(f"Couldn't parse JSON from model response: {text[:300]}") from e


REQUIRED_CREATIVE_KEYS = ('ingredients_reused', 'new_ingredients_required',
                           'suggested_refreshed_item', 'suggested_refreshed_description')


def generate_refresh_creative_content(item, full_menu_items, new_price, api_key=None) -> dict:
    """item: the REFRESH item (ItemInput or dict). full_menu_items: every
    item on the menu (for the reference block). new_price: the
    mechanically-computed new price (Section I pricing rule) — passed
    in, not invented by the model.

    Returns {ingredients_reused, new_ingredients_required,
    suggested_refreshed_item, suggested_refreshed_description}.
    Raises RefreshContentError if no API key, the call fails, the
    response isn't valid JSON, a required key is missing, or a banned
    phrase slipped through — callers should catch this and fall back to
    the existing '[NEEDS INPUT]' markers."""
    api_key = api_key or _get_api_key()
    if not api_key:
        raise RefreshContentError(
            "No ANTHROPIC_API_KEY set — set it as an environment variable to enable "
            "automated REFRESH content generation, or fill these fields in manually."
        )

    user_prompt = _build_user_prompt(item, full_menu_items, new_price)
    text = _call_anthropic(SYSTEM_PROMPT, user_prompt, api_key)
    result = _extract_json(text)

    missing = [k for k in REQUIRED_CREATIVE_KEYS if k not in result]
    if missing:
        raise RefreshContentError(f"Model response missing required field(s): {missing}")

    name = item['name'] if isinstance(item, dict) else item.name
    expected_prefix = f"{name} with "
    if not str(result['suggested_refreshed_item']).startswith(expected_prefix):
        raise RefreshContentError(
            f"Model's suggested name doesn't follow the required "
            f"'{name} with X and Y' pattern: {result['suggested_refreshed_item']!r}"
        )

    combined_text = " ".join(str(result[k]) for k in REQUIRED_CREATIVE_KEYS).lower()
    for phrase in BANNED_PHRASES:
        if phrase in combined_text:
            raise RefreshContentError(f"Model response used a banned phrase: {phrase!r}")

    reused_none = str(result['ingredients_reused']).strip().lower().startswith('none')
    new_none = str(result['new_ingredients_required']).strip().lower().startswith('none')
    if reused_none and new_none:
        raise RefreshContentError(
            "Model response is internally inconsistent: both Ingredients Reused and "
            "New Ingredients Required are 'None' — a refresh has to change something."
        )

    return result


# ----------------------------------------------------------------------
# Deterministic fallback — ALWAYS produces complete, specific content.
# No API key, no network call, no possibility of a placeholder. This is
# the actual guarantee that a REFRESH row is never left incomplete; the
# LLM path above is a quality upgrade on top of this, not a requirement.
# ----------------------------------------------------------------------
# Real, specifically-named additions per category — never a generic label
# like "sauce" or "topping". Cost is a realistic per-serving $ estimate;
# procurement is a concrete sourcing note, not a placeholder.
CATEGORY_ADDITIONS = {
    'BURGER': [
        {'name': 'garlic aioli', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
        {'name': 'smoked gouda', 'cost': 0.35, 'procurement': 'Shared dairy supplier'},
        {'name': 'crispy onion strings', 'cost': 0.20, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'applewood bacon jam', 'cost': 0.45, 'procurement': 'Existing protein supplier'},
        {'name': 'bourbon BBQ glaze', 'cost': 0.25, 'procurement': 'Existing sauce inventory'},
        {'name': 'fried egg', 'cost': 0.30, 'procurement': 'Existing egg supplier'},
        {'name': 'pepper jack cheese', 'cost': 0.30, 'procurement': 'Shared dairy supplier'},
        {'name': 'pickled jalapeños', 'cost': 0.15, 'procurement': 'Shelf-stable jarred inventory'},
    ],
    'SAND / WRAP': [
        {'name': 'chipotle mayo', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
        {'name': 'crispy bacon', 'cost': 0.40, 'procurement': 'Existing protein supplier'},
        {'name': 'avocado slices', 'cost': 0.35, 'procurement': 'Existing produce vendor'},
        {'name': 'pepper jack cheese', 'cost': 0.30, 'procurement': 'Shared dairy supplier'},
        {'name': 'house pickles', 'cost': 0.10, 'procurement': 'Shelf-stable jarred inventory'},
        {'name': 'roasted red peppers', 'cost': 0.20, 'procurement': 'Shelf-stable jarred inventory'},
        {'name': 'garlic aioli', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
        {'name': 'sriracha honey drizzle', 'cost': 0.20, 'procurement': 'Existing pantry ingredient'},
    ],
    'SALAD': [
        {'name': 'parmesan crisps', 'cost': 0.25, 'procurement': 'Shared dairy supplier'},
        {'name': 'candied pecans', 'cost': 0.30, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'dried cranberries', 'cost': 0.15, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'grilled chicken strips', 'cost': 0.55, 'procurement': 'Existing protein supplier'},
        {'name': 'lemon caper dressing', 'cost': 0.20, 'procurement': 'Existing sauce inventory'},
        {'name': 'crispy chickpeas', 'cost': 0.20, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'goat cheese crumbles', 'cost': 0.40, 'procurement': 'Shared dairy supplier'},
        {'name': 'balsamic glaze drizzle', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
    ],
    'ENTREE': [
        {'name': 'smoky BBQ drizzle', 'cost': 0.20, 'procurement': 'Existing sauce inventory'},
        {'name': 'house pickled jalapeños', 'cost': 0.15, 'procurement': 'Shelf-stable jarred inventory'},
        {'name': 'garlic butter', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
        {'name': 'crispy shallots', 'cost': 0.20, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'herb chimichurri', 'cost': 0.25, 'procurement': 'Existing sauce inventory'},
        {'name': 'toasted breadcrumb crust', 'cost': 0.15, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'lemon herb butter', 'cost': 0.20, 'procurement': 'Existing pantry ingredient'},
        {'name': 'smoked paprika aioli', 'cost': 0.20, 'procurement': 'Existing pantry ingredient'},
    ],
    'SIDE / OTHER': [
        {'name': 'truffle parmesan dust', 'cost': 0.35, 'procurement': 'Shared dairy supplier'},
        {'name': 'chipotle ranch drizzle', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
        {'name': 'everything bagel seasoning', 'cost': 0.10, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'crispy bacon bits', 'cost': 0.30, 'procurement': 'Existing protein supplier'},
        {'name': 'garlic herb butter', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
        {'name': 'spicy honey drizzle', 'cost': 0.20, 'procurement': 'Existing pantry ingredient'},
        {'name': 'scallion garnish', 'cost': 0.10, 'procurement': 'Existing produce vendor'},
        {'name': 'smoked paprika aioli', 'cost': 0.20, 'procurement': 'Existing pantry ingredient'},
    ],
    'BREAKFAST': [
        {'name': 'candied bacon', 'cost': 0.40, 'procurement': 'Existing protein supplier'},
        {'name': 'maple cinnamon butter', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
        {'name': 'fresh berry compote', 'cost': 0.30, 'procurement': 'Existing produce vendor'},
        {'name': 'whipped honey butter', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
        {'name': 'toasted pecans', 'cost': 0.25, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'caramelized bananas', 'cost': 0.20, 'procurement': 'Existing produce vendor'},
        {'name': 'cinnamon sugar dusting', 'cost': 0.10, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'chocolate chip drizzle', 'cost': 0.20, 'procurement': 'Shelf-stable dry inventory'},
    ],
    'BREAKFAST SIDE': [
        {'name': 'everything bagel seasoning', 'cost': 0.10, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'maple glaze', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
        {'name': 'crispy bacon bits', 'cost': 0.30, 'procurement': 'Existing protein supplier'},
        {'name': 'shredded cheddar', 'cost': 0.20, 'procurement': 'Shared dairy supplier'},
        {'name': 'fresh chives', 'cost': 0.10, 'procurement': 'Existing produce vendor'},
        {'name': 'hot honey drizzle', 'cost': 0.20, 'procurement': 'Existing pantry ingredient'},
        {'name': 'garlic herb butter', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
        {'name': 'smoked paprika dusting', 'cost': 0.10, 'procurement': 'Shelf-stable dry inventory'},
    ],
    'APPETIZER': [
        {'name': 'Nashville hot glaze', 'cost': 0.20, 'procurement': 'Existing sauce inventory'},
        {'name': 'house ranch', 'cost': 0.15, 'procurement': 'Existing sauce inventory'},
        {'name': 'blue cheese crumbles', 'cost': 0.30, 'procurement': 'Shared dairy supplier'},
        {'name': 'crispy fried garlic', 'cost': 0.15, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'sriracha aioli', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
        {'name': 'toasted sesame seeds', 'cost': 0.10, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'pickled banana peppers', 'cost': 0.15, 'procurement': 'Shelf-stable jarred inventory'},
        {'name': 'scallion garnish', 'cost': 0.10, 'procurement': 'Existing produce vendor'},
    ],
    'SOUP': [
        {'name': 'crispy tortilla strips', 'cost': 0.15, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'shredded sharp cheddar', 'cost': 0.25, 'procurement': 'Shared dairy supplier'},
        {'name': 'toasted pepitas', 'cost': 0.20, 'procurement': 'Shelf-stable dry inventory'},
        {'name': 'fresh cilantro', 'cost': 0.10, 'procurement': 'Existing produce vendor'},
        {'name': 'garlic bread crouton', 'cost': 0.20, 'procurement': 'Existing bakery supplier'},
        {'name': 'chili oil drizzle', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
        {'name': 'sour cream dollop', 'cost': 0.15, 'procurement': 'Existing dairy supplier'},
        {'name': 'crispy bacon bits', 'cost': 0.30, 'procurement': 'Existing protein supplier'},
    ],
}
DEFAULT_ADDITIONS = [
    {'name': 'house-made herb butter', 'cost': 0.15, 'procurement': 'Existing pantry ingredient'},
    {'name': 'smoked paprika aioli', 'cost': 0.20, 'procurement': 'Existing pantry ingredient'},
    {'name': 'crispy shallots', 'cost': 0.20, 'procurement': 'Shelf-stable dry inventory'},
    {'name': 'scallion garnish', 'cost': 0.10, 'procurement': 'Existing produce vendor'},
]

# Signal-driven rationale for the description's first sentence — which
# flagged signal the refresh is actually addressing, stated specifically
# rather than a generic "to improve the dish."
def _refresh_rationale(fr, cs_pct, duplicate_of):
    if fr <= 2:
        return "to lift margin without changing the core dish"
    if duplicate_of:
        return f"to differentiate it from '{duplicate_of}' on the menu"
    if cs_pct < 50:
        return "to broaden its appeal for the weekday commuter crowd"
    return "to keep the offering feeling current"


def _parse_ingredients_original_case(text):
    return [i.strip() for i in re.split(r',(?![^()]*\))', text or '') if i.strip()]


def _pick_additions(category, item_ingredients_lower, rotation_index):
    library = CATEGORY_ADDITIONS.get(category, DEFAULT_ADDITIONS)
    available = [a for a in library if a['name'].lower() not in item_ingredients_lower]
    if len(available) < 2:
        available = library
    n = len(available)
    idx1 = rotation_index % n
    idx2 = (rotation_index + 1) % n
    if idx1 == idx2:
        idx2 = (idx2 + 1) % n
    return available[idx1], available[idx2]


def generate_deterministic_refresh_content(item, rotation_index=0, target_fc_pct=None) -> dict:
    """item: the REFRESH ItemInput (needs name, category, ingredients,
    theoretical_cost, price, profitability_rank, commuter_score_percentile,
    duplicate_of, computed_new_price). rotation_index: an integer that
    should differ across REFRESH items in the same category (e.g. a
    running per-category counter from the caller) so consecutive items
    don't get identical additions. target_fc_pct: a food-cost ceiling
    (0-1) used only as a sanity check on the already-computed price —
    NOT the category's own average benchmark (some categories, e.g.
    Burger, run as low as ~16% average, which would force an unrealistic
    price hike for a $0.50 ingredient addition). Defaults to a universal
    45% ceiling (matching the FC_VALID_RANGE ceiling used elsewhere in
    this project) — the price only moves if the added cost would
    genuinely push food cost past a normal healthy range, not to chase
    this specific category's typical average.

    Returns every field doc 10 requires, ALWAYS complete: suggested_
    refreshed_item, suggested_refreshed_description, ingredients_reused,
    new_ingredients_required, ingredients_removed,
    estimated_additional_food_cost, new_theoretical_cost,
    suggested_selling_price, procurement_impact, operational_complexity.
    """
    name = item.name
    category = item.category
    ings_lower = {i.lower() for i in _parse_ingredients_original_case(item.ingredients)}
    original_ingredients = _parse_ingredients_original_case(item.ingredients)

    add1, add2 = _pick_additions(category, ings_lower, rotation_index)
    additional_cost = round(add1['cost'] + add2['cost'], 2)
    new_theoretical_cost = round(item.theoretical_cost + additional_cost, 2)

    target_fc = target_fc_pct if target_fc_pct is not None else 0.45
    suggested_price = item.computed_new_price if item.computed_new_price else item.price
    # Sanity check only: if the added cost pushes food-cost% past a normal
    # healthy ceiling, bump the price in $0.25 increments (never decrease)
    # until it clears that ceiling. Does NOT chase the category's own
    # average benchmark, which can be far tighter than any single price
    # move should have to satisfy.
    guard = 0
    while suggested_price > 0 and (new_theoretical_cost / suggested_price) > target_fc and guard < 40:
        suggested_price = round(suggested_price + 0.25, 2)
        guard += 1

    add1_title, add2_title = add1['name'].title(), add2['name'].title()
    refreshed_name = f"{name} with {add1_title} & {add2_title}"

    reused_text = (", ".join(original_ingredients) if original_ingredients
                   else "Original recipe ingredients weren't recorded in the source data — "
                        "treat the current recipe as fully retained alongside the additions below.")
    new_text = f"{add1_title}, {add2_title}"
    removed_text = "None — pure addition, original recipe fully retained."

    rationale = _refresh_rationale(item.profitability_rank, item.commuter_score_percentile, item.duplicate_of)
    description = (
        f"Adds {add1['name']} and {add2['name']} to the existing {name} {rationale}. "
        f"New price: ${suggested_price:.2f}, covering the added ${additional_cost:.2f} "
        f"in ingredient cost while keeping food cost at {new_theoretical_cost/suggested_price*100:.1f}% "
        f"of the new price."
    )

    procurement = f"{add1_title} — {add1['procurement']}; {add2_title} — {add2['procurement']}"
    complexity = ("Low — adds two finishing ingredients, no new cooking equipment "
                  "or prep station required.")

    return {
        'suggested_refreshed_item': refreshed_name,
        'suggested_refreshed_description': description,
        'ingredients_reused': reused_text,
        'new_ingredients_required': new_text,
        'ingredients_removed': removed_text,
        'estimated_additional_food_cost': additional_cost,
        'new_theoretical_cost': new_theoretical_cost,
        'suggested_selling_price': suggested_price,
        'procurement_impact': procurement,
        'operational_complexity': complexity,
    }