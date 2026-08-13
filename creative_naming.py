"""
creative_naming.py
--------------------
Generates a new, marketable menu-item name from a SourcedItem's real
ingredients/category -- deterministically, no LLM. The output name NEVER
contains or lightly modifies the sourced item's original real name; it's
built fresh from adjective + base-noun rules driven by what's actually in
the ingredients text.

WHY THIS EXISTS: item_sourcing.py picks a real, proven dish for demand
evidence, but real menu-export names are often bare and unmarketable
("1/2 Onion Rings", "5 Pc", "Cheeseburger (1/4 Lb)"). Section 2, Step 3 of
the original master prompt already established the right pattern for
this: derive the display name from real ingredients, not from the source
text. This module is the deterministic (non-LLM) implementation of that
same rule.

DESIGN
  1. Scan the item's ingredients + category text for signal keywords
     (spicy, bbq/smoked, fried/crispy, grilled, cheese-loaded, garlic,
     ranch, southern, premium-protein).
  2. Each signal maps to an ordered list of real adjectives from the
     approved vocabulary (Smokehouse, Nashville Hot, Fire-Grilled, etc.).
  3. Derive a generic base noun phrase from category + ingredient
     keywords (e.g. "Burger", "Chicken Sandwich", "Wings", "Onion
     Rings") -- these are food-type nouns, not the source item's literal
     name, so combining them with a real adjective produces a genuinely
     new name ("Smokehouse Loaded Burger") rather than a cosmetic edit of
     the original ("1/4 Lb Cheeseburger" -> banned).
  4. Assemble name = up to 2 adjectives + base noun. Enforced unique
     within a batch by trying the next-ranked adjective combination for
     any collision, falling back to a neutral vocabulary word if every
     signal-driven option is exhausted.
"""

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Approved adjective vocabulary, grouped by the real-ingredient signal that
# justifies using it. Order within each list is priority (first = best fit).
# ---------------------------------------------------------------------------
ADJECTIVES_BY_SIGNAL = {
    'spicy':        ['Nashville Hot', 'Fire-Kissed', 'Spicy'],
    'bbq_smoked':   ['Smokehouse', 'BBQ', 'Backyard'],
    'fried_crispy': ['Crispy', 'Golden', 'Hand-Breaded'],
    'grilled':      ['Fire-Grilled', 'Char-Grilled'],
    'cheese_loaded':['Loaded', 'Deluxe'],
    'garlic':       ['Garlic Parmesan'],
    'ranch':        ['Ranch'],
    'southern':     ['Southern', 'Homestyle'],
    'premium':      ['Signature', 'Premium', 'Handcrafted'],
}
DEFAULT_ADJECTIVES = ['Signature', 'Homestyle', 'Handcrafted', 'Deluxe', 'Premium']

SIGNAL_KEYWORDS = {
    'spicy':         ('hot', 'spicy', 'cayenne', 'jalapeno', 'jalapeño', 'chili', 'chile',
                       'pepper jack', 'buffalo', 'sriracha', 'habanero'),
    'bbq_smoked':    ('bbq', 'barbecue', 'smoked', 'smokehouse', 'pulled pork', 'brisket',
                       'hickory'),
    'fried_crispy':  ('fried', 'crispy', 'battered', 'breaded', 'crunchy'),
    'grilled':       ('grilled', 'fire-grilled', 'char-grilled', 'charbroiled', 'flame'),
    'cheese_loaded': ('cheese', 'cheddar', 'pimento', 'mozzarella', 'loaded', 'queso'),
    'garlic':        ('garlic',),
    'ranch':         ('ranch',),
    'southern':      ('southern', 'buttermilk', 'biscuit', 'cornbread', 'cajun', 'creole'),
    'premium':       ('angus', 'wagyu', 'prime', 'house-made', 'handcrafted', 'artisan',
                       'certified'),
}

# ---------------------------------------------------------------------------
# Base noun derivation -- generic food-type nouns, never the source item's
# literal name. Keyed on creation_engine.CATEGORY_KEYWORDS' taxonomy (Wings,
# Burgers, Pizza, Sandwiches, BBQ, Seafood, Mexican, Breakfast, Sushi/Asian,
# Southern, Italian, Chicken, Fast Food, Dessert) -- the taxonomy actually
# used throughout this build (creation_engine.py, dish_library.py). Checked
# in priority order per category; first keyword match wins.
# ---------------------------------------------------------------------------
_BASE_NOUN_RULES = {
    "Wings": [((), "Wings")],
    "Burgers": [((), "Burger")],
    "Pizza": [((), "Pizza")],
    "Sandwiches": [
        (("chicken",), "Chicken Sandwich"),
        (("turkey",), "Turkey Sandwich"),
        (("fish", "cod", "catfish"), "Fish Sandwich"),
        ((), "Sandwich"),
    ],
    "BBQ": [
        (("pulled pork",), "Pulled Pork Sandwich"),
        (("brisket",), "Brisket Plate"),
        (("rib", "ribs"), "Rib Plate"),
        ((), "BBQ Plate"),
    ],
    "Seafood": [
        (("catfish", "cod", "fried fish"), "Fish Plate"),
        (("shrimp",), "Shrimp Plate"),
        ((), "Seafood Plate"),
    ],
    "Mexican": [
        (("tortilla", "taco"), "Taco"),
        (("burrito",), "Burrito"),
        (("bowl", "rice", "black beans"), "Burrito Bowl"),
        ((), "Mexican Plate"),
    ],
    "Breakfast": [
        (("pancake", "pancakes"), "Pancakes"),
        (("egg", "eggs"), "Breakfast Plate"),
        ((), "Breakfast Plate"),
    ],
    "Sushi/Asian": [
        (("sushi rice", "nori"), "Roll"),
        (("noodle", "noodles"), "Noodle Bowl"),
        ((), "Asian Plate"),
    ],
    "Southern": [
        (("shrimp", "grits"), "Shrimp & Grits"),
        (("fried chicken",), "Fried Chicken Plate"),
        ((), "Southern Plate"),
    ],
    "Italian": [
        (("spaghetti", "fettuccine", "pasta", "noodle"), "Pasta"),
        ((), "Italian Plate"),
    ],
    "Chicken": [
        (("tender", "tenders"), "Chicken Tenders"),
        ((), "Chicken Plate"),
    ],
    "Fast Food": [
        (("beef patty", "burger"), "Burger"),
        (("chicken fillet", "chicken"), "Chicken Sandwich"),
        ((), "Combo"),
    ],
    "Dessert": [
        (("cake",), "Cake"),
        (("ice cream", "sundae"), "Sundae"),
        (("pie",), "Pie"),
        ((), "Dessert"),
    ],
}
_FALLBACK_BASE_NOUN = {cat: rules[-1][1] for cat, rules in _BASE_NOUN_RULES.items()}


def _detect_signals(text: str) -> list:
    t = text.lower()
    hits = []
    for signal, keywords in SIGNAL_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            hits.append(signal)
    return hits


def _derive_base_noun(category: str, text: str) -> str:
    t = text.lower()
    for keywords, noun in _BASE_NOUN_RULES.get(category, []):
        if not keywords or any(kw in t for kw in keywords):
            return noun
    return _FALLBACK_BASE_NOUN.get(category, 'Dish')


def generate_creative_name(ingredients: str, category: str, description: str = '',
                            used_names: Optional[set] = None, max_adjectives: int = 2) -> str:
    """
    Builds a new marketable name from real ingredient/category signals.
    Never reads or reuses the sourced item's original literal name --
    only ingredients/description/category feed this function.

    used_names: names already assigned earlier in this batch (case-
    insensitive) -- collisions are resolved by rotating to the next
    signal-ranked adjective, then falling back to DEFAULT_ADJECTIVES.
    """
    used_names = used_names or set()
    used_lower = {n.lower() for n in used_names}

    combined_text = f"{ingredients} {description} {category}"
    signals = _detect_signals(combined_text)
    base_noun = _derive_base_noun(category, combined_text)

    # Build an ordered candidate adjective pool: signal-matched first (in
    # signal-detection order), then the neutral default pool, deduplicated.
    candidate_adjs = []
    for sig in signals:
        for adj in ADJECTIVES_BY_SIGNAL[sig]:
            if adj not in candidate_adjs:
                candidate_adjs.append(adj)
    for adj in DEFAULT_ADJECTIVES:
        if adj not in candidate_adjs:
            candidate_adjs.append(adj)

    # Try increasing combinations of leading adjectives (1, then 2) against
    # the candidate pool until we find a name not already used this batch.
    for n_adj in range(1, max_adjectives + 1):
        for start in range(len(candidate_adjs)):
            combo = candidate_adjs[start:start + n_adj]
            if len(combo) < n_adj:
                continue
            name = ' '.join(combo) + ' ' + base_noun
            if name.lower() not in used_lower:
                return name

    # Exhausted every combination (very large batch) -- fall back to
    # appending a plain ordinal-free qualifier rather than ever reusing
    # the original source name.
    fallback = f"House {base_noun}"
    i = 2
    while fallback.lower() in used_lower:
        fallback = f"House {base_noun} No. {i}"
        i += 1
    return fallback


if __name__ == '__main__':
    tests = [
        ("Beef patty, American cheese, Lettuce, Tomato, Onion, Pickles, Sesame bun", "Fast Food", ""),
        ("Chicken wings, Buffalo sauce, Celery sticks, Blue cheese dressing", "Wings", ""),
        ("Smoked pulled pork, Bbq sauce, Coleslaw, Brioche bun", "BBQ", ""),
        ("Fried catfish, Cornmeal breading, Tartar sauce, Hush puppies", "Seafood", ""),
    ]
    used = set()
    for ing, cat, desc in tests:
        n = generate_creative_name(ing, cat, desc, used_names=used)
        used.add(n)
        print(f"{cat:25s} -> {n}")