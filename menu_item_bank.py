"""
menu_item_bank.py
-------------------
Builds a cleaned, deduplicated bank of REAL menu items from the comp-store
menu export, joined to the restaurant list for ZIP/cuisine context. This
replaces LLM-based item invention entirely: new menu item recommendations
are SOURCED from real, already-selling dishes at comparable restaurants,
not generated from scratch.

Why sourcing beats invention for this use case: a dish that's already
selling at K real restaurants in the metro is stronger evidence of local
demand than anything a language model could invent. It also sidesteps
every one of the master prompt's Section 5 guardrails about not inventing
ingredients/restaurants/statistics -- there's nothing to invent.

Expects two files with these canonical columns (after input_normalizer.py's
normalize_restaurant_dataframe() has run on the restaurant list):
  - restaurant list: restaurant_object_key, restaurant_name, zip_code, cuisines
  - menu items:       restaurant_object_key, menu_item_name, description,
                       price, standardized_category
"""

import re
from dataclasses import dataclass
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# 1. Cleaning helpers
# ---------------------------------------------------------------------------

# Strip leading order-codes like "(l41) " or "(m12) " that some POS/menu
# exports prepend to item names -- these are internal SKU codes, not part
# of the dish name a customer or a client report should see.
_LEADING_CODE_RE = re.compile(r'^\(\s*[a-zA-Z0-9]+\s*\)\s*')

def clean_item_name(name: str) -> str:
    name = str(name).strip()
    name = _LEADING_CODE_RE.sub('', name)
    return name.strip()


# Deterministic ingredient extraction from a real menu description.
# No LLM: split on common separators, drop filler/service phrases, title-case
# each fragment. This won't be as clean as a hand-written ingredient list,
# but every fragment traces back to real menu copy -- nothing invented.
_FILLER_PHRASES = [
    r'\bserved with\b', r'\bcomes with\b', r'\bincludes?\b', r'\btopped with\b',
    r'\bmade with\b', r'\bmade to order\b', r'\bmade to enjoy on the go\b',
    r'\byour choice of\b', r'\bchoice of\b', r'\bwith a side of\b',
    r'\bavailable\b', r'\boptional\b', r'\(cal\.[^)]*\)', r'\(\d+[^)]*\)',
]
_FILLER_RE = re.compile('|'.join(_FILLER_PHRASES), flags=re.IGNORECASE)
_SPLIT_RE = re.compile(r',| and | with |;|\.')


def extract_ingredients(description: Optional[str], max_items: int = 6) -> str:
    if not description or not isinstance(description, str) or not description.strip():
        return ''
    text = _FILLER_RE.sub('', description)
    fragments = [f.strip() for f in _SPLIT_RE.split(text) if f.strip()]
    # drop very short/noisy fragments and de-duplicate while preserving order
    seen = set()
    cleaned = []
    for f in fragments:
        f_clean = re.sub(r'^[a-z]\b', '', f).strip()  # drop stray leading single letters from splits
        if len(f_clean) < 3:
            continue
        key = f_clean.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(f_clean[0].upper() + f_clean[1:] if f_clean else f_clean)
        if len(cleaned) >= max_items:
            break
    return ', '.join(cleaned)


# Simple keyword-based Indulgent/Healthy lean, computed from real item text
# (name + description) -- a judgment-call proxy, explicitly labeled as such
# downstream, same as the master prompt requires when no review/rating
# sentiment data exists.
_INDULGENT_KWS = ('fried', 'cheese', 'bacon', 'crispy', 'loaded', 'buttermilk',
                   'mayo', 'ranch', 'gravy', 'battered', 'melt', 'smash', 'candy')
_HEALTHY_KWS = ('grilled', 'salad', 'fresh', 'steamed', 'lean', 'veggie',
                 'vegetable', 'baked', 'light', 'greens')


def indulgent_lean_score(text: str) -> float:
    """0-100: higher = more indulgent-leaning, based on keyword hits in the
    item's own real name+description. A judgment-call proxy, not sourced
    sentiment data."""
    t = str(text).lower()
    ind = sum(1 for kw in _INDULGENT_KWS if kw in t)
    heal = sum(1 for kw in _HEALTHY_KWS if kw in t)
    total = ind + heal
    if total == 0:
        return 50.0
    return round(100.0 * ind / total, 1)


# ---------------------------------------------------------------------------
# 2. Item bank construction
# ---------------------------------------------------------------------------

@dataclass
class ItemBank:
    df: pd.DataFrame          # one row per unique (restaurant, item, price)
    dedup_report: dict


def build_item_bank(menus_df: pd.DataFrame, restaurants_df: pd.DataFrame) -> ItemBank:
    """
    menus_df: raw menu items export (must have restaurant_object_key,
              menu_item_name, description, price, standardized_category).
    restaurants_df: output of input_normalizer.normalize_restaurant_dataframe().df
              (must have restaurant_object_key, restaurant_name, zip_code, cuisines).
    """
    required_menu_cols = {'restaurant_object_key', 'menu_item_name', 'price', 'standardized_category'}
    missing = required_menu_cols - set(menus_df.columns)
    if missing:
        raise ValueError(f"Menu items file is missing required columns: {sorted(missing)}")
    if 'restaurant_object_key' not in restaurants_df.columns:
        raise ValueError(
            "Restaurant list has no 'restaurant_object_key' column -- can't join to menu "
            "items. This field must survive normalize_restaurant_dataframe() (it's not "
            "renamed, just passed through as an unmapped column)."
        )

    m = menus_df.copy()
    m['menu_item_name_clean'] = m['menu_item_name'].apply(clean_item_name)
    m['price'] = pd.to_numeric(m['price'], errors='coerce')
    m = m.dropna(subset=['price', 'menu_item_name_clean'])
    m = m[m['menu_item_name_clean'].str.len() > 0]

    before = len(m)
    # Resolve category-inconsistent duplicates by taking the mode (most
    # common) standardized_category per (restaurant, item, price), then
    # drop to one row per that key.
    key_cols = ['restaurant_object_key', 'menu_item_name_clean', 'price']
    mode_cat = (m.groupby(key_cols)['standardized_category']
                  .agg(lambda s: s.mode().iat[0] if not s.mode().empty else s.iat[0])
                  .reset_index().rename(columns={'standardized_category': 'category_resolved'}))
    m = m.merge(mode_cat, on=key_cols, how='left')
    m = m.drop_duplicates(subset=key_cols).reset_index(drop=True)
    after = len(m)

    # Join restaurant context (zip, cuisines, name) -- inner join drops any
    # menu rows whose restaurant_object_key isn't in the restaurant list at all.
    rest_cols = ['restaurant_object_key', 'restaurant_name', 'zip_code']
    if 'cuisines' in restaurants_df.columns:
        rest_cols.append('cuisines')
    joined = m.merge(restaurants_df[rest_cols].drop_duplicates('restaurant_object_key'),
                      on='restaurant_object_key', how='inner')
    unmatched = after - len(joined)

    joined['ingredients_extracted'] = joined.get('description', pd.Series(dtype=str)).apply(extract_ingredients)
    joined['indulgent_lean'] = (joined['menu_item_name_clean'].fillna('') + ' ' +
                                 joined.get('description', pd.Series(dtype=str)).fillna('')).apply(indulgent_lean_score)

    report = {
        'raw_rows': int(len(menus_df)),
        'deduped_rows': int(after),
        'exact_or_category_duplicates_dropped': int(before - after),
        'joined_rows': int(len(joined)),
        'rows_dropped_no_restaurant_match': int(unmatched),
    }
    return ItemBank(df=joined, dedup_report=report)


if __name__ == '__main__':
    import input_normalizer as inorm
    raw_rest = pd.read_csv('MEM_compstore_restaurants.csv')
    norm = inorm.normalize_restaurant_dataframe(raw_rest)
    menus = pd.read_csv('MEM_compstore_menus.csv')
    bank = build_item_bank(menus, norm.df)
    print(bank.dedup_report)
    print(bank.df[['restaurant_name', 'zip_code', 'menu_item_name_clean',
                    'ingredients_extracted', 'price', 'category_resolved',
                    'indulgent_lean']].head(10).to_string())