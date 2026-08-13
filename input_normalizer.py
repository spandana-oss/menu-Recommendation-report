"""
input_normalizer.py — the preprocessing/normalization layer that makes
the Menu Refresh workflow accept *any* compatible raw restaurant/item
dataset, not just files that happen to use one exact set of column names.
Also covers restaurant-LIST normalization (one row per restaurant) for
the Menu Creation module, via a parallel alias table further down.

Pipeline this module implements for the item-list / Menu Refresh path:

    Upload Raw Restaurant Data
      -> Detect Available Columns        (detect_columns)
      -> Normalize Column Names          (CANONICAL_ALIASES + _normalize_header)
      -> Map to Canonical Schema         (normalize_dataframe)
      -> Generate Missing Fields         (infer_category, resolve fields below)
      -> [caller continues: market data, engine, workbook, report]

Design principle: every one of the 6 canonical fields the engine actually
needs (name, category, ingredients, annual_qty, price, theoretical_cost)
can be produced from as little as **name + price + quantity sold** alone.
Category and Theoretical Cost get a best-effort estimate when absent;
Ingredients degrades to an empty string (documented everywhere else in
this project as a duplicate-detection fidelity tradeoff, not a blocker).

What's genuinely required and CANNOT be inferred: item name, price, and
quantity sold. Without those three there's no popularity, profitability,
or forecast signal to compute from — see REQUIRED_MINIMUM below.

For the restaurant-LIST path (Menu Creation), see RESTAURANT_LIST_ALIASES
and normalize_restaurant_dataframe() near the bottom of this file. Same
_normalize_header / alias-lookup approach, second schema, not a
competing module.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


# ----------------------------------------------------------------------
# Canonical field <-> real-world header aliases (Menu Refresh item-list path).
# Add more variants here as new files surface them -- this is the single
# place alias support lives for the raw-item-list / minimal-schema path.
# ----------------------------------------------------------------------
CANONICAL_ALIASES: Dict[str, List[str]] = {
    'name': [
        'item name', 'menu item', 'current menu item', 'menu_item_name',
        'item', 'product name', 'dish name', 'dish', 'menu_item', 'name',
    ],
    'category': [
        'category', 'current category', 'department', 'family_group_name',
        'menu group', 'menu_group', 'food category', 'item category',
        'course', 'type', 'dept', 'product_group_id',
    ],
    'ingredients': [
        'ingredients', 'current ingredients', 'recipe', 'recipe_name',
        'ingredient list',
    ],
    'price': [
        'price', 'selling price', 'current price ($)', 'avg menu price ($)',
        'menu_item_price', 'unit price', 'menu price', 'sale price',
        'item price', 'price ($)',
    ],
    'annual_qty': [
        'quantity sold', 'total qty sold', 'total qty sold (annual)',
        'qty sold', 'units sold', 'store_qty_sold', 'qty', 'quantity',
        'annual quantity', 'annual qty', 'units', 'volume',
    ],
    'theoretical_cost': [
        'theoretical cost', 'theoretical cost ($)', 'cost', 'unit cost',
        'cogs', 'food cost', 'item cost', 'theoretical_cost',
        'cost of goods sold',
    ],
    'ingredient_cost': ['ingredient cost', 'ingredient cost ($)', 'ingredient_cost'],
    'prep_cost': ['prep cost', 'prep cost ($)', 'prep_cost', 'labor cost'],
    'zcta': ['zcta', 'zip', 'zip code', 'postal code', 'zipcode'],
}

REQUIRED_MINIMUM = ('name', 'price', 'annual_qty')

# Reuse the same category benchmark table as sales_data_builder.py so a
# generated Theoretical Cost is consistent regardless of which input path
# produced it.
CATEGORY_FC_BENCHMARK = {
    'APPETIZER': 0.28, 'BREAKFAST': 0.28, 'BREAKFAST SIDE': 0.22, 'SOUP': 0.28,
    'SALAD': 0.28, 'SAND / WRAP': 0.30, 'SIDE / OTHER': 0.22,
    'ENTREE': 0.2715, 'BURGER': 0.1614,
}
DEFAULT_FC_BENCHMARK = 0.25

# Lightweight keyword -> category inference for when no category column
# exists at all. Deliberately simple and conservative — same confidence
# tier as role_classifier.py's heuristics: a reasonable first pass, not a
# claim of certainty. Checked in order; first match wins.
CATEGORY_KEYWORDS = [
    ('BURGER', ('burger',)),
    ('SALAD', ('salad',)),
    ('SOUP', ('soup', 'chowder', 'bisque', 'chili')),
    ('BREAKFAST', ('pancake', 'waffle', 'omelet', 'omelette', 'breakfast',
                    'biscuit', 'french toast')),
    ('SAND / WRAP', ('sandwich', 'wrap', 'sub', 'panini', 'burrito')),
    ('APPETIZER', ('wings', 'pretzel', 'nachos', 'dip', 'sticks', 'poppers')),
    ('SIDE / OTHER', ('fries', 'chips', 'side', 'slaw', 'guacamole')),
]
FALLBACK_CATEGORY = 'UNKNOWN'


def _normalize_header(h) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(h).strip().lower()).strip()


def _build_alias_lookup(aliases_dict=None) -> Dict[str, str]:
    """normalized alias string -> canonical field name.

    Takes an optional aliases dict so this same function serves both the
    item-list schema (CANONICAL_ALIASES, the default) and the
    restaurant-list schema (RESTAURANT_LIST_ALIASES, passed explicitly
    further down in this file) without duplicating this logic."""
    aliases_dict = aliases_dict if aliases_dict is not None else CANONICAL_ALIASES
    lookup = {}
    for canonical, aliases in aliases_dict.items():
        lookup[_normalize_header(canonical)] = canonical  # canonical name always matches itself
        for a in aliases:
            lookup[_normalize_header(a)] = canonical
    return lookup


_ALIAS_LOOKUP = _build_alias_lookup()


def detect_columns(headers) -> Dict[str, str]:
    """headers: iterable of raw header strings from the uploaded file.
    Returns {canonical_field: original_header_string} for whichever
    canonical fields were matched. Unmatched headers are silently ignored
    (they may be extra columns the rest of the pipeline doesn't use)."""
    found = {}
    for h in headers:
        if h is None:
            continue
        norm = _normalize_header(h)
        canonical = _ALIAS_LOOKUP.get(norm)
        if canonical and canonical not in found:
            found[canonical] = h
    return found


def missing_required_fields(column_map: Dict[str, str]) -> List[str]:
    return [f for f in REQUIRED_MINIMUM if f not in column_map]


def infer_category(item_name: str) -> str:
    name_l = str(item_name).lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(kw in name_l for kw in keywords):
            return category
    return FALLBACK_CATEGORY


@dataclass
class NormalizationResult:
    rows: List[dict]                  # ready for run_analysis.build_items_from_raw
    warnings: List[str] = field(default_factory=list)
    column_map: Dict[str, str] = field(default_factory=dict)
    fields_generated: List[str] = field(default_factory=list)


def normalize_dataframe(df: pd.DataFrame) -> NormalizationResult:
    """The full 'detect -> normalize -> map -> generate missing' sequence.
    Returns rows shaped exactly like run_analysis.RAW_COLUMNS expects
    (Menu Item, Category, Ingredients, Total Qty Sold (annual), Price,
    Theoretical Cost), with every gap filled in and every inference
    recorded in `warnings`."""
    column_map = detect_columns(df.columns)
    missing = missing_required_fields(column_map)
    if missing:
        raise ValueError(
            f"Missing required data: {', '.join(missing)}. At minimum, the "
            f"file needs a recognizable item-name column, a price column, "
            f"and a quantity-sold column. Recognized headers in this file: "
            f"{list(df.columns)}."
        )

    warnings = []
    fields_generated = []

    work = pd.DataFrame()
    work['name'] = df[column_map['name']].astype(str).str.strip()
    work['price'] = pd.to_numeric(df[column_map['price']], errors='coerce')
    work['annual_qty'] = pd.to_numeric(df[column_map['annual_qty']], errors='coerce')

    bad_price = work['price'].isna().sum()
    bad_qty = work['annual_qty'].isna().sum()
    if bad_price:
        warnings.append(f"{bad_price} row(s) had a non-numeric price and were dropped.")
    if bad_qty:
        warnings.append(f"{bad_qty} row(s) had a non-numeric quantity and were dropped.")
    work = work.dropna(subset=['price', 'annual_qty', 'name'])
    work = work[work['name'].str.len() > 0]

    if 'category' in column_map:
        work['category'] = df.loc[work.index, column_map['category']].astype(str).str.strip()
        work['category'] = work['category'].replace('', pd.NA)
        n_missing_cat = work['category'].isna().sum()
        if n_missing_cat:
            work.loc[work['category'].isna(), 'category'] = work.loc[work['category'].isna(), 'name'].apply(infer_category)
            warnings.append(
                f"{n_missing_cat} row(s) had a blank category — inferred from the item "
                f"name using simple keyword matching (see input_normalizer.CATEGORY_KEYWORDS)."
            )
    else:
        work['category'] = work['name'].apply(infer_category)
        fields_generated.append('category')
        n_unknown = (work['category'] == FALLBACK_CATEGORY).sum()
        warnings.append(
            f"No category column found — inferred every item's category from its name "
            f"using simple keyword matching. {n_unknown} of {len(work)} items didn't "
            f"match any keyword and were left as '{FALLBACK_CATEGORY}' (uses the default "
            f"{DEFAULT_FC_BENCHMARK*100:.0f}% food-cost benchmark, not a category-specific one)."
        )

    if 'ingredients' in column_map:
        work['ingredients'] = df.loc[work.index, column_map['ingredients']].fillna('').astype(str).str.strip()
    else:
        work['ingredients'] = ''
        fields_generated.append('ingredients')
        warnings.append(
            "No ingredients column found — duplicate and unique-ingredient detection "
            "will run on item name + category only (lower fidelity, same as every other "
            "ingredients-free input path in this project)."
        )

    if 'theoretical_cost' in column_map:
        tc_col = pd.to_numeric(df.loc[work.index, column_map['theoretical_cost']], errors='coerce')
        n_missing_tc = tc_col.isna().sum()
        work['theoretical_cost'] = tc_col
        if n_missing_tc:
            fallback = work.loc[tc_col.isna()].apply(
                lambda r: r['price'] * CATEGORY_FC_BENCHMARK.get(r['category'], DEFAULT_FC_BENCHMARK), axis=1)
            work.loc[tc_col.isna(), 'theoretical_cost'] = fallback
            warnings.append(
                f"{n_missing_tc} row(s) had a blank/invalid Theoretical Cost — estimated "
                f"from each item's category food-cost benchmark instead."
            )
    else:
        work['theoretical_cost'] = work.apply(
            lambda r: r['price'] * CATEGORY_FC_BENCHMARK.get(r['category'], DEFAULT_FC_BENCHMARK), axis=1)
        fields_generated.append('theoretical_cost')
        warnings.append(
            "No Theoretical Cost column found — estimated for every item from its "
            "category's food-cost benchmark (Entrée ~27%, Salad/Breakfast/Soup/Appetizer "
            "~28%, Sandwich/Wrap ~30%, Side categories ~22%, Burger ~16%, unknown "
            f"category ~{DEFAULT_FC_BENCHMARK*100:.0f}%). These are real benchmarks from "
            "a validated reference menu, not guesses, but they're still estimates — "
            "replace with actual recipe costs when you have them for a more accurate result."
        )

    rows = [{
        'Menu Item': r['name'], 'Category': r['category'], 'Ingredients': r['ingredients'],
        'Total Qty Sold (annual)': r['annual_qty'], 'Price': r['price'],
        'Theoretical Cost': r['theoretical_cost'],
    } for _, r in work.iterrows()]

    return NormalizationResult(rows=rows, warnings=warnings, column_map=column_map,
                                fields_generated=fields_generated)


def looks_like_minimal_schema(headers) -> bool:
    """True if this file has at least name+price+qty recognizable via the
    alias table — used by the orchestrator to route anything that isn't
    one of the three fully-specified schemas through normalize_dataframe
    instead of rejecting it outright."""
    column_map = detect_columns(headers)
    return not missing_required_fields(column_map)


# ============================================================================
# RESTAURANT-LIST NORMALIZATION (Menu Creation path)
# One row per restaurant, not per menu item -- a different canonical
# schema from everything above, using the same _normalize_header /
# _build_alias_lookup machinery already defined earlier in this file.
# ============================================================================

RESTAURANT_LIST_ALIASES: Dict[str, List[str]] = {
    'restaurant_name': [
        'name', 'restaurant', 'business_name', 'biz_name', 'restaurant name',
        'restaurant_title', 'location_name',
    ],
    'zip_code': [
        'zip', 'zipcode', 'zip_or_postal_code', 'postal_code', 'zcta',
        'zip code', 'postcode', 'zip_code_5', 'postal code',
    ],
    'cuisines': [
        'cuisine', 'cuisine_type', 'food_type', 'categories', 'category',
        'tags', 'cuisine_tags', 'style_tags',
    ],
    'style': ['concept', 'restaurant_style', 'format'],
    'restaurant_type': ['service_type', 'segment', 'type', 'restaurant_class'],
    'price_range': ['price_tier', 'price_level', '$_rating', 'cost_rating'],
    'rating_value': ['rating', 'star_rating', 'avg_rating', 'review_score'],
    'review_count': ['num_reviews', 'reviews', 'review_total'],
    'city': ['town', 'municipality'],
    'state': ['state_or_province', 'st', 'province', 'state code'],
    'latitude': ['lat'],
    'longitude': ['lng', 'lon', 'long'],
}

REQUIRED_MINIMUM_RESTAURANT = ('restaurant_name', 'zip_code', 'cuisines')

_RESTAURANT_ALIAS_LOOKUP = _build_alias_lookup(RESTAURANT_LIST_ALIASES)


def detect_restaurant_columns(headers) -> Dict[str, str]:
    """Same matching logic as detect_columns(), against the restaurant
    alias table instead of the item-list one."""
    found = {}
    for h in headers:
        if h is None:
            continue
        norm = _normalize_header(h)
        canonical = _RESTAURANT_ALIAS_LOOKUP.get(norm)
        if canonical and canonical not in found:
            found[canonical] = h
    return found


@dataclass
class RestaurantNormalizationResult:
    df: pd.DataFrame
    warnings: List[str] = field(default_factory=list)
    column_map: Dict[str, str] = field(default_factory=dict)
    unmapped_columns: List[str] = field(default_factory=list)


def normalize_restaurant_dataframe(df: pd.DataFrame) -> RestaurantNormalizationResult:
    """Renames matched columns onto canonical names (restaurant_name,
    zip_code, cuisines, ...), leaves everything else untouched, and
    raises ValueError if restaurant_name/zip_code/cuisines can't be
    found -- those three are the minimum Menu Creation's category
    landscape + comparable-restaurant selection need to run at all."""
    column_map = detect_restaurant_columns(df.columns)
    missing = [f for f in REQUIRED_MINIMUM_RESTAURANT if f not in column_map]
    if missing:
        raise ValueError(
            f"Missing required restaurant-list fields: {', '.join(missing)}. "
            f"At minimum, the file needs a recognizable restaurant-name column, "
            f"a ZIP/ZCTA column, and a cuisine/category column. Headers found: "
            f"{list(df.columns)}."
        )

    rename_map = {orig: canon for canon, orig in column_map.items()}
    out = df.rename(columns=rename_map)
    unmapped = [c for c in out.columns if c not in RESTAURANT_LIST_ALIASES and c not in rename_map.values()]

    warnings = []
    out['zip_code'] = out['zip_code'].astype(str).str.extract(r'(\d{5})')[0]
    bad_zip = out['zip_code'].isna().sum()
    if bad_zip:
        warnings.append(f"{bad_zip} row(s) had an unparseable ZIP/ZCTA and were dropped.")
        out = out.dropna(subset=['zip_code'])

    return RestaurantNormalizationResult(
        df=out, warnings=warnings, column_map=column_map, unmapped_columns=unmapped,
    )


# ============================================================================
# COMPARABLE-RESTAURANT METRICS NORMALIZATION
# Optional Menu Creation input: one row per comparable restaurant, with at
# least a restaurant-name column and, when available, price and quantity-sold
# columns to enrich the generated output.
# ============================================================================

COMPARABLE_RESTAURANT_ALIASES: Dict[str, List[str]] = {
    'restaurant_name': [
        'restaurant name', 'restaurant', 'name', 'location name', 'chain name',
        'store name', 'business name',
    ],
    'price': [
        'price', 'avg price', 'average price', 'avg menu price', 'menu price',
        'selling price', 'current price ($)', 'price ($)', 'unit price',
    ],
    'annual_qty': [
        'quantity sold', 'qty sold', 'total qty sold', 'total quantity sold',
        'annual qty', 'annual quantity sold', 'units sold', 'units',
    ],
}

_COMPARABLE_ALIAS_LOOKUP = _build_alias_lookup(COMPARABLE_RESTAURANT_ALIASES)


def detect_comparable_restaurant_columns(headers) -> Dict[str, str]:
    """Same alias lookup pattern as detect_restaurant_columns(), but for the
    optional comparable-restaurant metrics file."""
    found = {}
    for h in headers:
        if h is None:
            continue
        norm = _normalize_header(h)
        canonical = _COMPARABLE_ALIAS_LOOKUP.get(norm)
        if canonical and canonical not in found:
            found[canonical] = h
    return found


@dataclass
class ComparableRestaurantNormalizationResult:
    df: pd.DataFrame
    warnings: List[str] = field(default_factory=list)
    column_map: Dict[str, str] = field(default_factory=dict)


def normalize_comparable_restaurant_dataframe(df: pd.DataFrame) -> ComparableRestaurantNormalizationResult:
    """Normalize an optional comparable-restaurant file into a compact
    dataframe with restaurant_name, price, and annual_qty columns when
    available.

    The file must at least expose a recognizable restaurant-name column.
    Price and quantity-sold are optional individually, but the file is only
    useful when at least one of them is present."""
    column_map = detect_comparable_restaurant_columns(df.columns)
    if 'restaurant_name' not in column_map:
        raise ValueError(
            f"Missing required comparable-data field: restaurant_name. "
            f"Recognized headers in this file: {list(df.columns)}."
        )

    warnings = []
    out = pd.DataFrame()
    out['restaurant_name'] = df[column_map['restaurant_name']].fillna('').astype(str).str.strip()

    if 'price' in column_map:
        out['price'] = pd.to_numeric(df[column_map['price']], errors='coerce')
        bad_price = out['price'].isna().sum()
        if bad_price:
            warnings.append(f"{bad_price} comparable row(s) had a non-numeric price and were ignored for price lookup.")
    else:
        out['price'] = pd.NA
        warnings.append("No comparable price column found — comparable price values will be blank.")

    if 'annual_qty' in column_map:
        out['annual_qty'] = pd.to_numeric(df[column_map['annual_qty']], errors='coerce')
        bad_qty = out['annual_qty'].isna().sum()
        if bad_qty:
            warnings.append(f"{bad_qty} comparable row(s) had a non-numeric quantity sold and were ignored for quantity lookup.")
    else:
        out['annual_qty'] = pd.NA
        warnings.append("No comparable quantity-sold column found — comparable quantity values will be blank.")

    out = out[out['restaurant_name'].str.len() > 0].reset_index(drop=True)
    if out.empty:
        raise ValueError("Comparable data file does not contain any usable restaurant names.")

    if out[['price', 'annual_qty']].isna().all(axis=None):
        warnings.append(
            "Comparable data file has restaurant names, but no usable price or quantity-sold "
            "values were found."
        )

    return ComparableRestaurantNormalizationResult(
        df=out, warnings=warnings, column_map=column_map,
    )
