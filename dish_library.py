"""
dish_library.py
-----------------
A static, hand-curated reference library of real, common restaurant dishes
by category, used when Menu Creation has ONLY a restaurant list to work
from -- no comp-store menu export, no LLM call. This replaces
menu_item_bank.py / item_sourcing.py from the earlier (menu-data-required)
build.

WHAT THIS IS AND ISN'T
This is not invented per-request by a model -- it's a fixed dataset written
into this file, the same kind of static reference table as
input_normalizer.CATEGORY_FC_BENCHMARK already is in this codebase (real,
standard industry figures, checked once, reused deterministically). Every
ingredient list below is a real, standard version of that dish as sold at
actual restaurants in that category -- not a fabricated combination.

WHAT CHANGES ABOUT THE EVIDENCE STORY vs. the menu-data-sourced build
Previously, "this item is proven" meant "N other real restaurants in the
dataset sell something like it" (a cross-market trend count). Without a
menu file, that evidence doesn't exist. The honest replacement, still
grounded in real data from the ONE file this module has: how many
restaurants IN THE TARGET ZCTA's own restaurant list already carry this
category (from creation_engine.select_comparables' total_found count).
That's real local-market evidence, just a different kind -- category
presence, not a proven-recipe trend count -- and every downstream field
that mentions it is worded to reflect that difference honestly.

TIER-BASED VARIATION
Each category has 2+ base templates (for variety across a batch) and a
short list of "upgrade ingredients" -- real, standard upsell add-ons for
that category (e.g. bacon, avocado, aged cheddar for a burger). Premium
tier adds 1 upgrade ingredient; Premium Edge adds 2. This is what makes a
Family/Premium/Premium Edge trio of items in the same category genuinely
different dishes, not the same dish re-priced three times.
"""
import random
from dataclasses import dataclass
from typing import Optional

@dataclass
class DishTemplate:
    base_ingredients: list
    description: str
    portion_note: str = "Standard individual portion."


@dataclass
class GeneratedDish:
    ingredients: str
    description: str
    price: float
    category: str
    evidence_count: int          
    indulgent_lean: float
    portion_note: str


# ---------------------------------------------------------------------------
# 1. Dish templates by category (matches creation_engine.CATEGORY_KEYWORDS)
# ---------------------------------------------------------------------------
DISH_TEMPLATES = {
    "Wings": [
        DishTemplate(
            ["chicken wings", "buffalo sauce", "celery sticks", "blue cheese dressing"],
            "Crispy fried chicken wings tossed in buffalo sauce, served with celery and blue cheese."),
        DishTemplate(
            ["chicken wings", "dry rub seasoning", "brown sugar", "ranch dressing"],
            "Oven-baked chicken wings coated in a sweet-and-savory dry rub, served with ranch."),
    ],
    "Burgers": [
        DishTemplate(
            ["beef patty", "american cheese", "lettuce", "tomato", "onion", "pickles", "sesame bun"],
            "A grilled beef patty topped with american cheese, lettuce, tomato, onion, and pickles on a toasted sesame bun."),
        DishTemplate(
            ["beef patty", "cheddar cheese", "caramelized onions", "mushrooms", "brioche bun"],
            "A grilled beef patty topped with melted cheddar, caramelized onions, and sautéed mushrooms on a brioche bun."),
    ],
    "Pizza": [
        DishTemplate(
            ["pizza dough", "marinara sauce", "mozzarella cheese", "pepperoni"],
            "Hand-tossed pizza dough topped with marinara, mozzarella, and pepperoni."),
        DishTemplate(
            ["pizza dough", "olive oil", "mozzarella cheese", "fresh basil", "roma tomatoes"],
            "Hand-tossed pizza dough with olive oil, fresh mozzarella, basil, and roma tomatoes."),
    ],
    "Sandwiches": [
        DishTemplate(
            ["sliced turkey", "swiss cheese", "lettuce", "tomato", "mayo", "sourdough bread"],
            "Sliced turkey and swiss cheese with lettuce, tomato, and mayo on toasted sourdough."),
        DishTemplate(
            ["grilled chicken breast", "provolone cheese", "spinach", "roasted red peppers", "ciabatta bread"],
            "Grilled chicken breast with provolone, spinach, and roasted red peppers on ciabatta."),
    ],
    "BBQ": [
        DishTemplate(
            ["smoked pulled pork", "bbq sauce", "coleslaw", "brioche bun"],
            "Slow-smoked pulled pork tossed in bbq sauce, topped with coleslaw on a brioche bun."),
        DishTemplate(
            ["smoked beef brisket", "bbq rub", "pickles", "white bread", "bbq sauce"],
            "Hickory-smoked beef brisket with a dry bbq rub, served with pickles and white bread."),
    ],
    "Seafood": [
        DishTemplate(
            ["fried catfish", "cornmeal breading", "tartar sauce", "hush puppies", "coleslaw"],
            "Cornmeal-breaded fried catfish served with tartar sauce, hush puppies, and coleslaw."),
        DishTemplate(
            ["grilled shrimp", "garlic butter", "lemon", "rice pilaf"],
            "Grilled shrimp finished in garlic butter and lemon, served over rice pilaf."),
    ],
    "Mexican": [
        DishTemplate(
            ["seasoned ground beef", "cheddar cheese", "lettuce", "pico de gallo", "flour tortilla"],
            "Seasoned ground beef with cheddar, lettuce, and pico de gallo wrapped in a flour tortilla."),
        DishTemplate(
            ["grilled chicken", "black beans", "rice", "pico de gallo", "sour cream", "corn tortillas"],
            "Grilled chicken with black beans and rice, topped with pico de gallo and sour cream on corn tortillas."),
    ],
    "Breakfast": [
        DishTemplate(
            ["scrambled eggs", "cheddar cheese", "bacon", "biscuit"],
            "Fluffy scrambled eggs with melted cheddar and crispy bacon on a fresh-baked biscuit."),
        DishTemplate(
            ["buttermilk pancakes", "maple syrup", "butter", "seasonal berries"],
            "Stacked buttermilk pancakes with butter, maple syrup, and seasonal berries."),
    ],
    "Sushi/Asian": [
        DishTemplate(
            ["sushi rice", "nori", "crab", "avocado", "cucumber"],
            "Sushi rice and nori rolled with crab, avocado, and cucumber."),
        DishTemplate(
            ["stir-fried noodles", "soy sauce", "vegetables", "scrambled egg", "scallions"],
            "Stir-fried noodles tossed in soy sauce with vegetables, egg, and scallions."),
    ],
    "Southern": [
        DishTemplate(
            ["fried chicken", "buttermilk batter", "mashed potatoes", "gravy", "cornbread"],
            "Buttermilk-battered fried chicken served with mashed potatoes, gravy, and cornbread."),
        DishTemplate(
            ["shrimp", "stone-ground grits", "cheddar cheese", "andouille sausage", "cajun seasoning"],
            "Sautéed shrimp and andouille sausage over cheddar stone-ground grits, cajun-seasoned."),
    ],
    "Italian": [
        DishTemplate(
            ["spaghetti", "marinara sauce", "meatballs", "parmesan cheese", "fresh basil"],
            "Spaghetti tossed in marinara with house-made meatballs, parmesan, and fresh basil."),
        DishTemplate(
            ["fettuccine", "alfredo sauce", "grilled chicken", "parmesan cheese", "cracked pepper"],
            "Fettuccine tossed in alfredo sauce with grilled chicken, parmesan, and cracked pepper."),
    ],
    "Chicken": [
        DishTemplate(
            ["fried chicken tenders", "buttermilk batter", "honey mustard"],
            "Hand-breaded buttermilk chicken tenders served with honey mustard."),
        DishTemplate(
            ["grilled chicken breast", "lemon herb marinade", "roasted vegetables"],
            "Lemon-herb marinated grilled chicken breast with roasted seasonal vegetables."),
    ],
    "Fast Food": [
        DishTemplate(
            ["beef patty", "american cheese", "pickles", "onion", "mustard", "ketchup", "sesame bun"],
            "A classic grilled beef patty with american cheese, pickles, onion, mustard, and ketchup."),
        DishTemplate(
            ["crispy chicken fillet", "lettuce", "mayo", "toasted bun"],
            "A crispy breaded chicken fillet with lettuce and mayo on a toasted bun."),
    ],
    "Dessert": [
        DishTemplate(
            ["chocolate cake", "chocolate ganache", "vanilla ice cream"],
            "Rich chocolate cake with chocolate ganache, served with vanilla ice cream."),
        DishTemplate(
            ["vanilla ice cream", "hot fudge", "whipped cream", "chopped peanuts", "cherry"],
            "Vanilla ice cream topped with hot fudge, whipped cream, peanuts, and a cherry."),
    ],
}

DEFAULT_CATEGORY = "Fast Food"

# Real, standard upsell ingredients per category -- used to differentiate
# Premium and Premium Edge tier dishes from the Family-tier base template.
UPGRADE_INGREDIENTS = {
    "Wings": ["nashville hot glaze", "garlic parmesan tossing", "smoked applewood bacon bits"],
    "Burgers": ["applewood smoked bacon", "aged cheddar", "fried egg", "garlic aioli"],
    "Pizza": ["prosciutto", "truffle oil", "fresh burrata", "wild mushrooms"],
    "Sandwiches": ["applewood bacon", "avocado", "garlic aioli", "aged provolone"],
    "BBQ": ["burnt ends", "smoked gouda", "bourbon glaze"],
    "Seafood": ["jumbo lump crab", "garlic butter drizzle", "cajun blackening seasoning"],
    "Mexican": ["carne asada", "queso fundido", "roasted poblano crema"],
    "Breakfast": ["applewood bacon", "avocado", "hollandaise sauce"],
    "Sushi/Asian": ["spicy tuna", "eel sauce", "tempura flakes"],
    "Southern": ["pimento cheese", "benne seed", "bourbon glaze"],
    "Italian": ["prosciutto", "wild mushrooms", "truffle oil"],
    "Chicken": ["nashville hot glaze", "garlic parmesan tossing", "applewood bacon"],
    "Fast Food": ["applewood bacon", "aged cheddar", "fried egg"],
    "Dessert": ["salted caramel drizzle", "toasted pecans", "espresso ganache"],
}

# Real, standard fast-casual/QSR baseline prices per category (industry-
# typical figures, same convention as CATEGORY_FC_BENCHMARK -- a labeled
# estimate, not sourced data, since no real local pricing file exists here).
PRICE_BASELINE_BY_CATEGORY = {
    "Wings": 10.99, "Burgers": 9.49, "Pizza": 13.99, "Sandwiches": 9.99,
    "BBQ": 12.99, "Seafood": 14.99, "Mexican": 10.49, "Breakfast": 8.99,
    "Sushi/Asian": 11.99, "Southern": 11.49, "Italian": 12.49, "Chicken": 9.99,
    "Fast Food": 7.99, "Dessert": 5.99,
}
DEFAULT_PRICE_BASELINE = 9.99

TIER_PRICE_MULTIPLIER = {"Family": 0.85, "Premium": 1.15, "Premium Edge": 1.55}
TIER_UPGRADE_COUNT = {"Family": 0, "Premium": 1, "Premium Edge": 2}

_INDULGENT_KWS = ('fried', 'cheese', 'bacon', 'crispy', 'loaded', 'buttermilk',
                   'gravy', 'battered', 'ganache', 'glaze', 'butter')
_HEALTHY_KWS = ('grilled', 'roasted', 'fresh', 'lean', 'herb', 'vegetables')


def _indulgent_lean(text: str) -> float:
    t = text.lower()
    ind = sum(1 for kw in _INDULGENT_KWS if kw in t)
    heal = sum(1 for kw in _HEALTHY_KWS if kw in t)
    total = ind + heal
    return 50.0 if total == 0 else round(100.0 * ind / total, 1)


def _round_to_menu_price(x: float) -> float:
    """Round to a natural-looking menu price ending in .49/.99, matching
    common QSR/fast-casual pricing convention."""
    whole = int(x)
    frac = x - whole
    ending = 0.49 if frac < 0.75 else 0.99
    return round(whole + ending, 2)


def select_dish(category: str, tier: str, template_index: int,
                 evidence_count: int, household_size: float) -> GeneratedDish:
    """
    category: a creation_engine.CATEGORY_KEYWORDS label (e.g. "Burgers").
    tier: "Family" | "Premium" | "Premium Edge".
    template_index: rotates deterministically through this category's
                     templates so repeated calls for the same category
                     (across different tiers/slots in a batch) don't
                     always return the identical base dish.
    evidence_count: real count of restaurants in the TARGET ZCTA's own
                     restaurant list already carrying this category
                     (from creation_engine.select_comparables' total_found)
                     -- the local-market evidence signal for this build.
    """
    templates = DISH_TEMPLATES.get(category, DISH_TEMPLATES[DEFAULT_CATEGORY])
    template = templates[template_index % len(templates)]

    ingredients = list(template.base_ingredients)
    upgrades = UPGRADE_INGREDIENTS.get(category, UPGRADE_INGREDIENTS[DEFAULT_CATEGORY])
    n_upgrades = TIER_UPGRADE_COUNT.get(tier, 0)
    added = upgrades[:n_upgrades]
    ingredients = ingredients + added

    description = template.description
    if added:
        description = description.rstrip('.') + f", finished with {', '.join(added)}."

    baseline = PRICE_BASELINE_BY_CATEGORY.get(category, DEFAULT_PRICE_BASELINE)
    price = _round_to_menu_price(baseline * TIER_PRICE_MULTIPLIER.get(tier, 1.0))

    portion_note = (f"Standard household portion sized for this ZIP's average household size "
                     f"of {household_size}." if tier == "Family" else template.portion_note)

    ingredients_str = ', '.join(i[0].upper() + i[1:] for i in ingredients)

    return GeneratedDish(
        ingredients=ingredients_str,
        description=description[0].upper() + description[1:],
        price=price,
        category=category,
        evidence_count=evidence_count,
        indulgent_lean=_indulgent_lean(ingredients_str + ' ' + description),
        portion_note=portion_note,
    )


if __name__ == '__main__':
    for tier in ("Family", "Premium", "Premium Edge"):
        d = select_dish("Burgers", tier, template_index=0, evidence_count=4, household_size=2.5)
        print(tier, '->', d)