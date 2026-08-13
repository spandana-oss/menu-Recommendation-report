"""
item_sourcing.py
------------------
Deterministic selection of a real, already-selling menu item to recommend
for a target ZCTA/category/price-tier -- replaces the LLM invention step
(creation_engine.build_invention_prompt / run_menu_creation.call_claude)
entirely. No language model call anywhere in this module.

SELECTION LOGIC (all rule-based):
  1. Candidate pool = items in the target category, from restaurants
     OUTSIDE the target ZCTA (so the recommendation is genuinely new to
     that specific market) with a non-empty extracted ingredient list
     (so the report can show real ingredients, not a blank field).
  2. Exclude any candidate whose cleaned name already appears verbatim
     (case-insensitive) on a menu already in the target ZCTA -- don't
     recommend something the local market already has.
  3. Price-tier filter: keep only candidates whose real price falls
     within that tier's price band for this category, computed from the
     REAL price distribution of this category across the whole comp-store
     dataset (33rd/66th percentile cut points -> Family/Premium/Premium
     Edge bands). This is the deterministic replacement for a hand-picked
     "target_price_hint".
  4. Trend signal: how many distinct restaurants in the dataset sell an
     item with a similar (normalized) name -- a real, countable proxy for
     "is this a proven concept," not a vibe.
  5. Rank remaining candidates by trend signal descending, then by price
     closeness to the tier's median, and take the top one. Ties broken by
     restaurant_object_key for reproducibility (same inputs -> same output).
"""

import re
from dataclasses import dataclass
from typing import Optional

import pandas as pd


def _normalize_for_trend(name: str) -> str:
    """Loose normalization so 'Cheeseburger' and 'Classic Cheeseburger'
    can both contribute to the same trend count without being literally
    identical -- keeps only alphanumeric tokens, sorted, so word order
    doesn't matter either."""
    tokens = re.findall(r'[a-z0-9]+', name.lower())
    stop = {'the', 'a', 'an', 'and', 'with', 'of', 'classic', 'original', 'style'}
    tokens = sorted(t for t in tokens if t not in stop and len(t) > 2)
    return ' '.join(tokens)


def compute_price_bands(item_bank_df: pd.DataFrame, category_col: str = 'category_resolved',
                         price_col: str = 'price') -> pd.DataFrame:
    """Real 33rd/66th percentile price cut points per category, computed
    once from the whole comp-store dataset. Returns a DataFrame indexed by
    category with columns: p33, p66 (band edges for Family|Premium|Premium Edge)."""
    def bands(g):
        return pd.Series({'p33': g[price_col].quantile(0.33), 'p66': g[price_col].quantile(0.66)})
    return item_bank_df.groupby(category_col).apply(bands)


def _tier_price_range(bands_row, tier: str) -> tuple:
    p33, p66 = bands_row['p33'], bands_row['p66']
    if tier == 'Family':
        return (0.0, p33)
    if tier == 'Premium':
        return (p33, p66)
    return (p66, float('inf'))  # Premium Edge


@dataclass
class SourcedItem:
    item_name: str
    ingredients: str
    description: str
    price: float
    category: str
    source_restaurant: str
    trend_count: int
    price_band_low: float
    price_band_high: float
    indulgent_lean: float


def source_item(item_bank_df: pd.DataFrame, price_bands: pd.DataFrame,
                 category: str, tier: str, target_zcta: str,
                 already_recommended_names: Optional[set] = None,
                 category_col: str = 'category_resolved') -> Optional[SourcedItem]:
    """Returns the single best real candidate item for this category/tier/
    ZCTA combination, or None if nothing qualifies (caller should fall back
    to a broader category or flag the gap explicitly -- never invent)."""
    already_recommended_names = already_recommended_names or set()

    pool = item_bank_df[item_bank_df[category_col] == category].copy()
    pool = pool[pool['zip_code'].astype(str) != str(target_zcta)]
    pool = pool[pool['ingredients_extracted'].fillna('').str.len() > 0]
    if pool.empty:
        return None

    local_names = set(
        item_bank_df.loc[item_bank_df['zip_code'].astype(str) == str(target_zcta),
                          'menu_item_name_clean'].str.lower()
    )
    pool = pool[~pool['menu_item_name_clean'].str.lower().isin(local_names)]
    pool = pool[~pool['menu_item_name_clean'].str.lower().isin(
        {n.lower() for n in already_recommended_names})]
    if pool.empty:
        return None

    if category not in price_bands.index:
        return None
    lo, hi = _tier_price_range(price_bands.loc[category], tier)
    pool = pool[(pool['price'] >= lo) & (pool['price'] <= (hi if hi != float('inf') else pool['price'].max()))]
    if pool.empty:
        return None

    # Trend signal: count distinct restaurants selling a normalized-similar
    # name, computed across the FULL bank (not just the filtered pool) so
    # a locally-rare-but-broadly-proven concept still scores correctly.
    all_norm = item_bank_df.copy()
    all_norm['_norm'] = all_norm['menu_item_name_clean'].apply(_normalize_for_trend)
    trend_counts = all_norm.groupby('_norm')['restaurant_object_key'].nunique()

    pool['_norm'] = pool['menu_item_name_clean'].apply(_normalize_for_trend)
    pool['_trend'] = pool['_norm'].map(trend_counts).fillna(1).astype(int)

    tier_median = pool['price'].median()
    pool['_price_dist'] = (pool['price'] - tier_median).abs()

    pool = pool.sort_values(['_trend', '_price_dist', 'restaurant_object_key'],
                             ascending=[False, True, True])
    best = pool.iloc[0]

    return SourcedItem(
        item_name=best['menu_item_name_clean'],
        ingredients=best['ingredients_extracted'],
        description=best.get('description', '') or '',
        price=float(best['price']),
        category=category,
        source_restaurant=best['restaurant_name'],
        trend_count=int(best['_trend']),
        price_band_low=round(lo, 2),
        price_band_high=round(hi, 2) if hi != float('inf') else None,
        indulgent_lean=float(best.get('indulgent_lean', 50.0)),
    )


if __name__ == '__main__':
    import input_normalizer as inorm
    from menu_item_bank import build_item_bank

    raw_rest = pd.read_csv('MEM_compstore_restaurants.csv')
    norm = inorm.normalize_restaurant_dataframe(raw_rest)
    menus = pd.read_csv('MEM_compstore_menus.csv')
    bank = build_item_bank(menus, norm.df)
    bands = compute_price_bands(bank.df)
    print(bands.head(15))
    print()

    for tier in ('Family', 'Premium', 'Premium Edge'):
        item = source_item(bank.df, bands, category='Entrées', tier=tier, target_zcta='38114')
        print(tier, '->', item)