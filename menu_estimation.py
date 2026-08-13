"""
menu_estimation.py - shared menu item estimation helpers.

This module fills gaps when a row is missing description, ingredients,
ingredient cost, prep cost, or theoretical cost. The logic is local and
deterministic, but it is intentionally written like an AI fallback:
generate a plausible description, infer likely ingredients, price the
ingredients with benchmark costs, estimate prep effort, then calculate
theoretical cost from those pieces.

The returned metadata is meant to be written straight into the workbook
so the output can clearly show what was provided versus estimated.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


CATEGORY_FC_BENCHMARK = {
    'APPETIZER': 0.28, 'BREAKFAST': 0.28, 'BREAKFAST SIDE': 0.22, 'SOUP': 0.28,
    'SALAD': 0.28, 'SAND / WRAP': 0.30, 'SIDE / OTHER': 0.22,
    'ENTREE': 0.2715, 'BURGER': 0.1614,
}
DEFAULT_FC_BENCHMARK = 0.25

# Ingredient cost as a share of theoretical cost. These mirror the
# validated ratios already used in sales_data_builder.py.
CATEGORY_IC_RATIO = {
    'ENTREE': 0.88, 'SIDE / OTHER': 0.80, 'SALAD': 0.85, 'BREAKFAST': 0.82,
    'SAND / WRAP': 0.83, 'BURGER': 0.85, 'BREAKFAST SIDE': 0.80,
    'APPETIZER': 0.85, 'SOUP': 0.82,
}
DEFAULT_IC_RATIO = 0.83

STYLE_BASE_INGREDIENTS = {
    'pizza': ['Pizza Dough', 'Tomato Sauce', 'Mozzarella Cheese'],
    'burger': ['Beef Patty', 'Toasted Bun', 'Lettuce', 'Tomato', 'Onion'],
    'sandwich': ['Bread', 'Lettuce', 'Tomato', 'Signature Sauce'],
    'wrap': ['Tortilla', 'Lettuce', 'Tomato', 'Signature Sauce'],
    'salad': ['Mixed Greens', 'Tomato', 'Cucumber', 'Dressing'],
    'soup': ['Broth', 'Vegetables', 'Herbs'],
    'breakfast': ['Eggs', 'Hash Browns', 'Toast'],
    'appetizer': ['Crispy Base', 'Dipping Sauce'],
    'side': ['Seasoned Side'],
    'entree': ['House Protein', 'Seasonal Vegetables', 'Savory Sauce'],
    'taco': ['Tortilla', 'Protein', 'Fresh Toppings', 'Salsa'],
    'bowl': ['Rice', 'Protein', 'Fresh Toppings', 'Sauce'],
    'pasta': ['Pasta', 'Sauce', 'Parmesan Cheese'],
    'dessert': ['Sweet Base', 'Creamy Topping'],
    'seafood': ['Fresh Seafood', 'Lemon', 'Herbs'],
    'general': ['House Protein', 'Fresh Garnish', 'Signature Sauce'],
}

STYLE_DESCRIPTION_TEMPLATES = {
    'pizza': 'Hand-tossed pizza topped with {primary}, {secondary}, {tertiary}, and {finish}.',
    'burger': 'A grilled burger on a toasted bun with {primary}, {secondary}, {tertiary}, and {finish}.',
    'sandwich': 'A hearty sandwich with {primary}, {secondary}, {tertiary}, and {finish}.',
    'wrap': 'A fresh wrap filled with {primary}, {secondary}, {tertiary}, and {finish}.',
    'salad': 'A fresh salad with {primary}, {secondary}, {tertiary}, and {finish}.',
    'soup': 'A comforting soup simmered with {primary}, {secondary}, {tertiary}, and {finish}.',
    'breakfast': 'A hearty breakfast plate with {primary}, {secondary}, {tertiary}, and {finish}.',
    'appetizer': 'A shareable appetizer featuring {primary}, {secondary}, {tertiary}, and {finish}.',
    'side': 'A classic side of {primary}, seasoned and served hot.',
    'entree': 'A house entree featuring {primary}, {secondary}, {tertiary}, and {finish}.',
    'taco': 'A flavor-packed taco-style dish with {primary}, {secondary}, {tertiary}, and {finish}.',
    'bowl': 'A satisfying bowl with {primary}, {secondary}, {tertiary}, and {finish}.',
    'pasta': 'A pasta dish tossed with {primary}, {secondary}, {tertiary}, and {finish}.',
    'dessert': 'A sweet dessert with {primary}, {secondary}, {tertiary}, and {finish}.',
    'seafood': 'A seafood dish featuring {primary}, {secondary}, {tertiary}, and {finish}.',
    'general': 'A house favorite featuring {primary}, {secondary}, {tertiary}, and {finish}.',
}

STYLE_PREP_MINUTES = {
    'pizza': 12.0,
    'burger': 6.5,
    'sandwich': 5.0,
    'wrap': 5.0,
    'salad': 4.5,
    'soup': 10.0,
    'breakfast': 7.5,
    'appetizer': 6.5,
    'side': 3.5,
    'entree': 9.0,
    'taco': 6.5,
    'bowl': 6.0,
    'pasta': 9.5,
    'dessert': 4.5,
    'seafood': 9.0,
    'general': 6.0,
}

COOKING_METHOD_PREP_BONUS = {
    'grilled': 2.0,
    'fried': 3.0,
    'fried chicken': 3.0,
    'baked': 2.0,
    'roasted': 2.0,
    'smoked': 1.5,
    'blackened': 1.5,
    'seared': 1.5,
    'charbroiled': 2.0,
    'braised': 2.0,
    'slow cooked': 3.0,
    'tossed': 0.5,
    'stuffed': 2.0,
    'loaded': 1.5,
    'crispy': 1.0,
}

INGREDIENT_LIBRARY = [
    ('Pizza Dough', ('pizza dough', 'dough', 'crust', 'flatbread'), 0.85, 'carb'),
    ('Tomato Sauce', ('tomato sauce', 'marinara', 'red sauce', 'pizza sauce'), 0.20, 'sauce'),
    ('Mozzarella Cheese', ('mozzarella', 'mozzarella cheese', 'pizza cheese'), 0.95, 'dairy'),
    ('Cheddar Cheese', ('cheddar', 'cheddar cheese'), 0.80, 'dairy'),
    ('Parmesan Cheese', ('parmesan', 'parm', 'parmesan cheese'), 0.55, 'dairy'),
    ('American Cheese', ('american cheese',), 0.75, 'dairy'),
    ('Beef Patty', ('beef patty', 'burger patty', 'ground beef', 'beef'), 2.10, 'protein'),
    ('Grilled Chicken', ('grilled chicken', 'chicken breast', 'chicken'), 1.90, 'protein'),
    ('Fried Chicken', ('fried chicken', 'crispy chicken'), 2.10, 'protein'),
    ('Steak', ('steak', 'sirloin'), 3.25, 'protein'),
    ('Pulled Pork', ('pulled pork', 'pork'), 1.65, 'protein'),
    ('Bacon', ('bacon',), 0.85, 'protein'),
    ('Sausage', ('sausage',), 0.95, 'protein'),
    ('Ham', ('ham',), 0.90, 'protein'),
    ('Turkey', ('turkey',), 1.25, 'protein'),
    ('Shrimp', ('shrimp',), 2.70, 'protein'),
    ('Fresh Fish', ('fish', 'tilapia', 'catfish', 'cod', 'salmon', 'sea bass', 'mahi'), 2.60, 'protein'),
    ('Tuna', ('tuna',), 2.20, 'protein'),
    ('Crab', ('crab',), 2.90, 'protein'),
    ('Lobster', ('lobster',), 3.80, 'protein'),
    ('Tofu', ('tofu',), 1.10, 'protein'),
    ('Eggs', ('eggs', 'egg'), 0.45, 'protein'),
    ('Mixed Greens', ('mixed greens', 'greens', 'spring mix'), 0.25, 'produce'),
    ('Romaine Lettuce', ('romaine', 'lettuce',), 0.20, 'produce'),
    ('Tomato', ('tomato', 'tomatoes'), 0.18, 'produce'),
    ('Onion', ('onion',), 0.14, 'produce'),
    ('Red Onion', ('red onion',), 0.16, 'produce'),
    ('Cucumber', ('cucumber',), 0.12, 'produce'),
    ('Cilantro', ('cilantro',), 0.06, 'produce'),
    ('Avocado', ('avocado',), 0.72, 'produce'),
    ('Mushrooms', ('mushroom', 'mushrooms'), 0.35, 'produce'),
    ('Bell Peppers', ('bell pepper', 'peppers', 'pepper', 'bell peppers'), 0.22, 'produce'),
    ('Jalapenos', ('jalapeno', 'jalapenos'), 0.10, 'produce'),
    ('Pickles', ('pickle', 'pickles'), 0.10, 'produce'),
    ('Corn', ('corn',), 0.12, 'produce'),
    ('Celery', ('celery',), 0.10, 'produce'),
    ('Broccoli', ('broccoli',), 0.18, 'produce'),
    ('Broth', ('broth', 'stock'), 0.20, 'base'),
    ('Rice', ('rice', 'white rice', 'brown rice'), 0.35, 'carb'),
    ('Pasta', ('pasta', 'noodles', 'spaghetti', 'penne', 'fettuccine', 'macaroni'), 0.50, 'carb'),
    ('Bread', ('bread', 'toast', 'toasted bread'), 0.25, 'carb'),
    ('Bun', ('bun', 'toasted bun', 'burger bun'), 0.35, 'carb'),
    ('Tortilla', ('tortilla',), 0.30, 'carb'),
    ('Hash Browns', ('hash browns',), 0.45, 'carb'),
    ('Fries', ('fries', 'french fries', 'chips'), 0.50, 'carb'),
    ('Croutons', ('croutons',), 0.18, 'carb'),
    ('Biscuit', ('biscuit',), 0.35, 'carb'),
    ('Ranch Dressing', ('ranch', 'ranch dressing'), 0.12, 'sauce'),
    ('Caesar Dressing', ('caesar', 'caesar dressing'), 0.14, 'sauce'),
    ('BBQ Sauce', ('bbq sauce', 'barbecue sauce', 'bbq'), 0.12, 'sauce'),
    ('Buffalo Sauce', ('buffalo sauce', 'hot wing sauce'), 0.12, 'sauce'),
    ('Salsa', ('salsa',), 0.10, 'sauce'),
    ('Queso', ('queso',), 0.24, 'sauce'),
    ('Alfredo Sauce', ('alfredo', 'alfredo sauce'), 0.22, 'sauce'),
    ('Pesto', ('pesto',), 0.20, 'sauce'),
    ('Teriyaki Sauce', ('teriyaki', 'teriyaki sauce'), 0.14, 'sauce'),
    ('Soy Glaze', ('soy glaze', 'soy sauce'), 0.10, 'sauce'),
    ('Chipotle Mayo', ('chipotle mayo', 'aioli', 'garlic aioli'), 0.18, 'sauce'),
    ('Mustard', ('mustard',), 0.05, 'sauce'),
    ('Ketchup', ('ketchup',), 0.05, 'sauce'),
    ('Honey Mustard', ('honey mustard',), 0.08, 'sauce'),
    ('Hot Sauce', ('hot sauce',), 0.05, 'sauce'),
    ('Butter', ('butter',), 0.08, 'dairy'),
    ('Sour Cream', ('sour cream',), 0.18, 'dairy'),
    ('Cream Cheese', ('cream cheese',), 0.35, 'dairy'),
    ('Seasoning Blend', ('seasoning', 'spices', 'herbs', 'pepper', 'salt'), 0.05, 'seasoning'),
    ('Granola', ('granola',), 0.45, 'dry'),
    ('Fruit Mix', ('berries', 'strawberries', 'blueberries', 'banana', 'fruit', 'apple'), 0.60, 'produce'),
]

INGREDIENT_ROLE_FALLBACK = {
    'protein': 1.85,
    'carb': 0.45,
    'produce': 0.20,
    'sauce': 0.12,
    'dairy': 0.80,
    'seasoning': 0.05,
    'base': 0.25,
    'dry': 0.45,
}

STYLE_KEYWORDS = [
    ('pizza', ('pizza', 'flatbread', 'calzone')),
    ('burger', ('burger', 'cheeseburger', 'sliders', 'slider')),
    ('wrap', ('wrap', 'burrito', 'quesadilla', 'rollup')),
    ('sandwich', ('sandwich', 'sub', 'hoagie', 'panini', 'sammich')),
    ('salad', ('salad', 'cobb', 'caesar', 'garden', 'chopped')),
    ('soup', ('soup', 'chowder', 'bisque', 'chili', 'gumbo')),
    ('breakfast', ('breakfast', 'omelet', 'omelette', 'pancake', 'waffle', 'french toast', 'hash', 'biscuit')),
    ('appetizer', ('wings', 'nachos', 'sticks', 'pretzel', 'dip', 'sampler', 'appetizer', 'mozzarella sticks')),
    ('side', ('fries', 'chips', 'side', 'slaw', 'rings', 'potatoes')),
    ('pasta', ('pasta', 'spaghetti', 'penne', 'fettuccine', 'alfredo', 'mac and cheese', 'macaroni')),
    ('taco', ('taco', 'tacos', 'burrito', 'quesadilla', 'enchilada', 'fajita')),
    ('bowl', ('bowl', 'grain bowl', 'rice bowl', 'poke')),
    ('dessert', ('dessert', 'cake', 'pie', 'brownie', 'sundae', 'cookie', 'cheesecake')),
    ('seafood', ('shrimp', 'fish', 'salmon', 'catfish', 'tilapia', 'cod', 'crab', 'lobster')),
]


def _clean_text(value) -> str:
    if value is None:
        return ''
    text = str(value).strip()
    if text.lower() in {'nan', 'none', 'null'}:
        return ''
    return text


def _norm(value) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', _clean_text(value).lower()).strip()


def _titleize_phrase(value: str) -> str:
    value = _clean_text(value)
    if not value:
        return ''
    words = []
    for token in value.split():
        if token.upper() in {'BBQ', 'BLT', 'TACO', 'BOWL', 'MAC', 'PB', 'AIOLI'}:
            words.append(token.upper())
        elif token.lower() in {'bbq', 'blt'}:
            words.append(token.upper())
        else:
            words.append(token.capitalize())
    return ' '.join(words)


def _has_any(text: str, phrases: Sequence[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _first_matching_style(text: str) -> str:
    for style, phrases in STYLE_KEYWORDS:
        if _has_any(text, phrases):
            return style
    return 'general'


def _category_style(category: str) -> str:
    cat = _norm(category)
    if 'burger' in cat:
        return 'burger'
    if 'salad' in cat:
        return 'salad'
    if 'soup' in cat:
        return 'soup'
    if 'breakfast' in cat:
        return 'breakfast'
    if 'wrap' in cat or 'sand' in cat:
        return 'wrap'
    if 'side' in cat:
        return 'side'
    if 'appetizer' in cat or 'app' in cat:
        return 'appetizer'
    if 'pizza' in cat:
        return 'pizza'
    if 'seafood' in cat:
        return 'seafood'
    if 'pasta' in cat:
        return 'pasta'
    return 'general'


def infer_style(name: str, category: str = '') -> str:
    text = _norm(name)
    style = _first_matching_style(text)
    if style != 'general':
        return style
    return _category_style(category)


def _infer_ingest_bucket(value: str) -> str:
    text = _norm(value)
    if not text:
        return 'general'
    if any(k in text for k in ('chicken', 'beef', 'steak', 'pork', 'fish', 'shrimp', 'salmon', 'crab', 'lobster', 'tuna', 'tofu', 'turkey')):
        return 'protein'
    if any(k in text for k in ('bun', 'bread', 'dough', 'crust', 'tortilla', 'rice', 'pasta', 'noodle', 'fries', 'hash', 'potato', 'chip', 'crouton')):
        return 'carb'
    if any(k in text for k in ('cheese', 'cream', 'butter', 'sour cream', 'yogurt', 'mozzarella', 'cheddar', 'parmesan')):
        return 'dairy'
    if any(k in text for k in ('lettuce', 'greens', 'tomato', 'onion', 'cilantro', 'avocado', 'mushroom', 'pepper', 'jalapeno', 'pickle', 'cucumber', 'celery', 'corn', 'broccoli')):
        return 'produce'
    if any(k in text for k in ('sauce', 'dress', 'aioli', 'mayo', 'ranch', 'caesar', 'bbq', 'buffalo', 'marinara', 'alfredo', 'pesto', 'teriyaki', 'salsa', 'glaze', 'mustard', 'ketchup', 'hot sauce')):
        return 'sauce'
    if any(k in text for k in ('season', 'herb', 'salt', 'pepper', 'spice')):
        return 'seasoning'
    return 'general'


def _canonicalize_ingredient(phrase: str) -> Optional[str]:
    text = _norm(phrase)
    if not text:
        return None
    for canonical, aliases, _, _ in INGREDIENT_LIBRARY:
        if any(alias in text for alias in aliases):
            return canonical
    return _titleize_phrase(phrase)


def _split_ingredient_text(text: str) -> List[str]:
    raw = _clean_text(text)
    if not raw:
        return []
    parts = re.split(r'\s*(?:,|;|\||/|\n| & |\band\b|\bwith\b)\s*', raw, flags=re.I)
    return [p.strip() for p in parts if p and p.strip()]


def _unique_preserve_order(values: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        key = _norm(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _lookup_ingredient_cost(ingredient: str) -> float:
    text = _norm(ingredient)
    if not text:
        return 0.0
    for canonical, aliases, cost, group in INGREDIENT_LIBRARY:
        if canonical.lower() in text or any(alias in text for alias in aliases):
            return cost
    bucket = _infer_ingest_bucket(text)
    return INGREDIENT_ROLE_FALLBACK.get(bucket, 0.25)


def _ingredient_share_of_price(category: str) -> float:
    cat = _clean_text(category).upper()
    fc_pct = CATEGORY_FC_BENCHMARK.get(cat, DEFAULT_FC_BENCHMARK)
    ic_ratio = CATEGORY_IC_RATIO.get(cat, DEFAULT_IC_RATIO)
    return fc_pct * ic_ratio


def _estimate_ingredient_list(name: str, category: str, description: str = '') -> List[str]:
    text = _norm(f"{name} {description}")
    style = infer_style(name, category)
    ingredients: List[str] = []

    def add(value: str):
        if value and value not in ingredients:
            ingredients.append(value)

    # Ingredient hints from the text come first so the description can
    # mention the most likely real components.
    if 'bbq' in text:
        add('BBQ Sauce')
    if 'buffalo' in text:
        add('Buffalo Sauce')
    if 'ranch' in text:
        add('Ranch Dressing')
    if 'caesar' in text:
        add('Caesar Dressing')
    if 'alfredo' in text:
        add('Alfredo Sauce')
    if 'pesto' in text:
        add('Pesto')
    if 'teriyaki' in text:
        add('Teriyaki Sauce')
    if 'chipotle' in text or 'aioli' in text:
        add('Chipotle Mayo')
    if 'salsa' in text:
        add('Salsa')
    if 'queso' in text:
        add('Queso')

    if 'chicken' in text:
        add('Grilled Chicken' if 'fried' not in text else 'Fried Chicken')
    elif 'beef' in text or 'burger' in text:
        add('Beef Patty')
    elif 'steak' in text:
        add('Steak')
    elif 'shrimp' in text:
        add('Shrimp')
    elif any(k in text for k in ('fish', 'salmon', 'catfish', 'tilapia', 'cod')):
        add('Fresh Fish')
    elif 'pork' in text:
        add('Pulled Pork')
    elif 'bacon' in text:
        add('Bacon')
    elif 'sausage' in text:
        add('Sausage')
    elif 'turkey' in text:
        add('Turkey')
    elif 'tofu' in text or 'veggie' in text or 'vegetable' in text:
        add('Tofu')

    if 'pizza' in text or style == 'pizza':
        add('Pizza Dough')
        add('Tomato Sauce')
        add('Mozzarella Cheese')
        if 'pepperoni' in text:
            add('Pepperoni')
        if 'mushroom' in text:
            add('Mushrooms')
        if 'onion' in text:
            add('Red Onion')
        if 'cilantro' in text:
            add('Cilantro')
        if 'jalapeno' in text:
            add('Jalapenos')
        if 'olive' in text:
            add('Olives')
    elif style == 'burger':
        add('Toasted Bun')
        add('Lettuce')
        add('Tomato')
        add('Onion')
        add('Cheddar Cheese')
        if 'pickle' in text:
            add('Pickles')
        if 'bacon' in text:
            add('Bacon')
        if 'avocado' in text:
            add('Avocado')
    elif style in {'sandwich', 'wrap'}:
        add('Bread' if style == 'sandwich' else 'Tortilla')
        add('Lettuce')
        add('Tomato')
        add('Signature Sauce')
        if 'onion' in text:
            add('Red Onion')
        if 'pickle' in text:
            add('Pickles')
        if 'cheese' in text:
            add('Cheddar Cheese')
    elif style == 'salad':
        add('Mixed Greens')
        add('Cucumber')
        add('Tomato')
        add('Dressing')
        if 'caesar' in text:
            add('Caesar Dressing')
        if 'avocado' in text:
            add('Avocado')
        if 'crouton' in text:
            add('Croutons')
    elif style == 'soup':
        add('Broth')
        add('Vegetables')
        if 'chicken' in text:
            add('Grilled Chicken')
        if 'beans' in text:
            add('Beans')
        if 'noodle' in text or 'pasta' in text:
            add('Pasta')
    elif style == 'breakfast':
        add('Eggs')
        add('Hash Browns')
        add('Toast')
        if 'bacon' in text:
            add('Bacon')
        if 'sausage' in text:
            add('Sausage')
        if 'cheese' in text:
            add('Cheddar Cheese')
    elif style == 'appetizer':
        if 'wing' in text:
            add('Chicken Wings')
            add('Buffalo Sauce')
        elif 'nacho' in text:
            add('Tortilla Chips')
            add('Queso')
            add('Salsa')
        elif 'pretzel' in text:
            add('Baked Pretzel')
            add('Cheese Sauce')
        elif 'fries' in text or 'chips' in text:
            add('Fries')
            add('Seasoning Blend')
        else:
            add('Crispy Base')
            add('Dipping Sauce')
    elif style == 'side':
        if 'fries' in text:
            add('Fries')
        elif 'chips' in text:
            add('Tortilla Chips')
        elif 'slaw' in text:
            add('Cabbage Slaw')
        else:
            add('Seasoned Side')
    elif style == 'pasta':
        add('Pasta')
        add('Sauce')
        if 'alfredo' in text:
            add('Alfredo Sauce')
        if 'marinara' in text or 'tomato' in text:
            add('Tomato Sauce')
        add('Parmesan Cheese')
    elif style == 'taco':
        add('Tortilla')
        add('Fresh Toppings')
        if 'burrito' in text:
            add('Rice')
            add('Beans')
        add('Salsa')
    elif style == 'bowl':
        add('Rice')
        add('Fresh Toppings')
        add('Sauce')
        if 'poke' in text:
            add('Fresh Fish')
    elif style == 'seafood':
        add('Fresh Fish')
        add('Lemon')
        add('Herbs')
    else:
        if 'quesadilla' in text:
            add('Tortilla')
            add('Cheddar Cheese')
            add('Salsa')
        if 'mac' in text:
            add('Pasta')
            add('Cheddar Cheese')
        if 'loaded' in text:
            add('Cheddar Cheese')
            add('Sour Cream')

    # Fill in the gaps with a style-safe default.
    for ingredient in STYLE_BASE_INGREDIENTS.get(style, STYLE_BASE_INGREDIENTS['general']):
        add(ingredient)

    return _unique_preserve_order(ingredients)


def _format_ingredient_phrase(values: Sequence[str]) -> str:
    clean = [v for v in values if _clean_text(v)]
    if not clean:
        return ''
    if len(clean) == 1:
        return clean[0]
    if len(clean) == 2:
        return f"{clean[0]} and {clean[1]}"
    return ', '.join(clean[:-1]) + f", and {clean[-1]}"


def _pick_description_parts(ingredients: Sequence[str], style: str, name: str, category: str) -> Tuple[str, str, str, str]:
    clean = [v for v in ingredients if _clean_text(v)]
    primary = clean[0] if clean else _titleize_phrase(name)
    secondary = clean[1] if len(clean) > 1 else ''
    tertiary = clean[2] if len(clean) > 2 else ''
    finish = clean[3] if len(clean) > 3 else ''

    if style == 'pizza':
        primary = next((v for v in clean if 'dough' in _norm(v)), primary)
        secondary = next((v for v in clean if 'chicken' in _norm(v) or 'beef' in _norm(v) or 'pepperoni' in _norm(v) or 'shrimp' in _norm(v)), secondary or 'a hearty topping')
        tertiary = next((v for v in clean if 'cheese' in _norm(v)), tertiary or 'melted cheese')
        finish = next((v for v in clean if any(k in _norm(v) for k in ('onion', 'cilantro', 'pepper', 'olive', 'jalapeno'))), finish or 'fresh herbs')
    elif style == 'burger':
        primary = next((v for v in clean if 'patty' in _norm(v) or 'chicken' in _norm(v) or 'fish' in _norm(v) or 'tofu' in _norm(v)), primary)
        secondary = next((v for v in clean if 'cheese' in _norm(v)), secondary or 'melted cheese')
        tertiary = next((v for v in clean if any(k in _norm(v) for k in ('lettuce', 'tomato', 'onion', 'pickle', 'avocado'))), tertiary or 'fresh toppings')
        finish = next((v for v in clean if 'sauce' in _norm(v) or 'mayo' in _norm(v) or 'aioli' in _norm(v) or 'mustard' in _norm(v)), finish or 'house sauce')
    elif style in {'sandwich', 'wrap'}:
        primary = next((v for v in clean if any(k in _norm(v) for k in ('chicken', 'beef', 'turkey', 'ham', 'pork', 'fish', 'tofu', 'egg'))), primary)
        secondary = next((v for v in clean if any(k in _norm(v) for k in ('lettuce', 'greens', 'tomato', 'onion', 'pickle'))), secondary or 'crisp vegetables')
        tertiary = next((v for v in clean if 'cheese' in _norm(v)), tertiary or 'melted cheese')
        finish = next((v for v in clean if 'sauce' in _norm(v) or 'dressing' in _norm(v) or 'aioli' in _norm(v)), finish or 'signature sauce')
    elif style == 'salad':
        primary = next((v for v in clean if 'greens' in _norm(v) or 'lettuce' in _norm(v) or 'romaine' in _norm(v)), primary)
        secondary = next((v for v in clean if any(k in _norm(v) for k in ('chicken', 'steak', 'shrimp', 'salmon', 'tofu'))), secondary or 'a protein topping')
        tertiary = next((v for v in clean if any(k in _norm(v) for k in ('cucumber', 'tomato', 'onion', 'avocado', 'pepper', 'fruit'))), tertiary or 'fresh toppings')
        finish = next((v for v in clean if 'dressing' in _norm(v)), finish or 'house dressing')
    elif style == 'soup':
        primary = next((v for v in clean if 'broth' in _norm(v) or 'soup' in _norm(v)), primary)
        secondary = next((v for v in clean if any(k in _norm(v) for k in ('chicken', 'beef', 'shrimp', 'pork', 'beans'))), secondary or 'a hearty base')
        tertiary = next((v for v in clean if any(k in _norm(v) for k in ('vegetable', 'tomato', 'onion', 'carrot', 'celery'))), tertiary or 'seasonal vegetables')
        finish = next((v for v in clean if 'herb' in _norm(v) or 'seasoning' in _norm(v)), finish or 'fresh herbs')
    elif style == 'breakfast':
        primary = next((v for v in clean if 'egg' in _norm(v)), primary)
        secondary = next((v for v in clean if any(k in _norm(v) for k in ('bacon', 'sausage', 'ham', 'chicken'))), secondary or 'a savory protein')
        tertiary = next((v for v in clean if any(k in _norm(v) for k in ('hash', 'toast', 'biscuit', 'pancake', 'waffle'))), tertiary or 'classic breakfast sides')
        finish = next((v for v in clean if 'cheese' in _norm(v) or 'sauce' in _norm(v) or 'syrup' in _norm(v)), finish or 'warm finishing touch')
    elif style == 'appetizer':
        primary = next((v for v in clean if any(k in _norm(v) for k in ('wing', 'nacho', 'pretzel', 'fries', 'chip', 'stick', 'dip'))), primary)
        secondary = next((v for v in clean if 'sauce' in _norm(v) or 'dressing' in _norm(v) or 'dip' in _norm(v)), secondary or 'a dipping sauce')
        tertiary = next((v for v in clean if any(k in _norm(v) for k in ('cheese', 'jalapeno', 'onion', 'bacon'))), tertiary or 'extra flavor')
        finish = next((v for v in clean if any(k in _norm(v) for k in ('garnish', 'herb', 'cilantro'))), finish or 'a crisp finish')
    elif style == 'side':
        primary = next((v for v in clean if any(k in _norm(v) for k in ('fries', 'chips', 'slaw', 'potato', 'rice'))), primary)
        secondary = next((v for v in clean if 'season' in _norm(v) or 'salt' in _norm(v)), secondary or 'simple seasoning')
        tertiary = next((v for v in clean if any(k in _norm(v) for k in ('cheese', 'bacon', 'sauce'))), tertiary or 'a finishing touch')
        finish = next((v for v in clean if any(k in _norm(v) for k in ('parsley', 'cilantro', 'herb'))), finish or 'fresh herbs')
    elif style == 'pasta':
        primary = next((v for v in clean if 'pasta' in _norm(v) or 'noodle' in _norm(v)), primary)
        secondary = next((v for v in clean if 'sauce' in _norm(v) or 'alfredo' in _norm(v) or 'marinara' in _norm(v)), secondary or 'a rich sauce')
        tertiary = next((v for v in clean if any(k in _norm(v) for k in ('chicken', 'shrimp', 'meatball', 'sausage', 'beef'))), tertiary or 'a protein topping')
        finish = next((v for v in clean if 'cheese' in _norm(v) or 'herb' in _norm(v)), finish or 'fresh herbs')
    elif style == 'taco':
        primary = next((v for v in clean if any(k in _norm(v) for k in ('tortilla', 'shell', 'taco'))), primary)
        secondary = next((v for v in clean if any(k in _norm(v) for k in ('chicken', 'beef', 'shrimp', 'fish', 'pork', 'tofu'))), secondary or 'a seasoned filling')
        tertiary = next((v for v in clean if any(k in _norm(v) for k in ('salsa', 'cilantro', 'onion', 'lettuce', 'tomato'))), tertiary or 'fresh toppings')
        finish = next((v for v in clean if 'cheese' in _norm(v) or 'crema' in _norm(v) or 'sauce' in _norm(v)), finish or 'a bright sauce')
    elif style == 'bowl':
        primary = next((v for v in clean if any(k in _norm(v) for k in ('rice', 'grain', 'noodle'))), primary)
        secondary = next((v for v in clean if any(k in _norm(v) for k in ('chicken', 'beef', 'shrimp', 'fish', 'tofu'))), secondary or 'a protein topping')
        tertiary = next((v for v in clean if any(k in _norm(v) for k in ('greens', 'tomato', 'onion', 'avocado', 'pepper'))), tertiary or 'fresh toppings')
        finish = next((v for v in clean if 'sauce' in _norm(v) or 'dressing' in _norm(v)), finish or 'a finishing sauce')
    elif style == 'seafood':
        primary = next((v for v in clean if any(k in _norm(v) for k in ('fish', 'shrimp', 'salmon', 'crab', 'lobster'))), primary)
        secondary = next((v for v in clean if any(k in _norm(v) for k in ('lemon', 'herb', 'garlic', 'butter'))), secondary or 'bright seasoning')
        tertiary = next((v for v in clean if any(k in _norm(v) for k in ('rice', 'potato', 'vegetable', 'greens'))), tertiary or 'a balanced side')
        finish = next((v for v in clean if any(k in _norm(v) for k in ('sauce', 'butter', 'glaze'))), finish or 'a light finish')
    else:
        primary = next((v for v in clean if any(k in _norm(v) for k in ('protein', 'chicken', 'beef', 'fish', 'shrimp', 'pork', 'tofu'))), primary)
        secondary = next((v for v in clean if any(k in _norm(v) for k in ('vegetable', 'greens', 'tomato', 'onion', 'avocado'))), secondary or 'fresh produce')
        tertiary = next((v for v in clean if any(k in _norm(v) for k in ('sauce', 'dressing', 'aioli', 'mayo', 'ranch'))), tertiary or 'signature sauce')
        finish = next((v for v in clean if any(k in _norm(v) for k in ('herb', 'cheese', 'garnish', 'seasoning'))), finish or 'a final garnish')

    # Fallbacks if any slots are still empty.
    primary = primary or _titleize_phrase(name)
    secondary = secondary or _titleize_phrase(category) or 'balanced flavors'
    tertiary = tertiary or 'seasoned components'
    finish = finish or 'a fresh finish'
    return primary, secondary, tertiary, finish


def _generate_description(name: str, category: str, ingredients: Sequence[str]) -> str:
    style = infer_style(name, category)
    template = STYLE_DESCRIPTION_TEMPLATES.get(style, STYLE_DESCRIPTION_TEMPLATES['general'])
    primary, secondary, tertiary, finish = _pick_description_parts(ingredients, style, name, category)
    if style == 'side' and _clean_text(primary):
        return f"Classic {primary.lower()} prepared to order with simple seasoning and a fresh finish."
    description = template.format(primary=primary, secondary=secondary, tertiary=tertiary, finish=finish)
    return description


def _estimate_ingredient_cost_from_list(ingredients: Sequence[str], category: str, price: Optional[float], style: str) -> Tuple[float, float]:
    if not ingredients:
        anchor = 0.0
        if price is not None and price > 0:
            anchor = float(price) * _ingredient_share_of_price(category)
        return anchor, 0.55 if anchor else 0.0

    ingredient_sum = 0.0
    recognized = 0
    for ingredient in ingredients:
        cost = _lookup_ingredient_cost(ingredient)
        if cost > 0:
            recognized += 1
        ingredient_sum += cost

    if price is not None and price > 0:
        anchor = float(price) * _ingredient_share_of_price(category)
        weight = min(0.75, 0.20 + recognized * 0.08)
        estimate = (weight * ingredient_sum) + ((1.0 - weight) * anchor)
        lower = price * 0.08
        upper = price * 0.55
        estimate = max(lower, min(upper, estimate))
    else:
        estimate = ingredient_sum

    confidence = 70.0 + min(18.0, recognized * 3.0) + (4.0 if style != 'general' else 0.0)
    if price is not None and price > 0:
        confidence += 4.0
    return round(estimate, 2), max(60.0, min(95.0, confidence))


def _estimate_prep_cost_from_profile(ingredients: Sequence[str], category: str, price: Optional[float], style: str, text: str) -> Tuple[float, float]:
    base_minutes = STYLE_PREP_MINUTES.get(style, STYLE_PREP_MINUTES['general'])
    minute_multiplier = 1.0

    text_norm = _norm(text)
    for phrase, bonus in COOKING_METHOD_PREP_BONUS.items():
        if phrase in text_norm:
            base_minutes += bonus
            minute_multiplier += 0.02

    ingredient_count = max(1, len(ingredients))
    complexity = 0.7 * ingredient_count
    if ingredient_count >= 5:
        complexity += 1.0
    if style in {'pizza', 'seafood', 'entree', 'pasta'}:
        complexity += 1.0
    if any(k in text_norm for k in ('loaded', 'combo', 'platter', 'family', 'feast', 'ultimate')):
        complexity += 1.5

    prep_minutes = (base_minutes + complexity) * minute_multiplier
    prep_rate = 0.16  # roughly $9.60 / hour labor allowance
    estimate = prep_minutes * prep_rate

    if price is not None and price > 0:
        lower = price * 0.03
        upper = price * 0.18
        estimate = max(lower, min(upper, estimate))

    confidence = 72.0 + (5.0 if style != 'general' else 0.0) + min(10.0, ingredient_count * 1.2)
    if _has_any(text_norm, tuple(COOKING_METHOD_PREP_BONUS.keys())):
        confidence += 4.0
    if price is not None and price > 0:
        confidence += 3.0
    return round(estimate, 2), max(60.0, min(94.0, confidence))


def _plausible_theoretical_cost(tc: Optional[float], price: Optional[float]) -> bool:
    if tc is None:
        return False
    try:
        tc = float(tc)
    except (TypeError, ValueError):
        return False
    if tc <= 0:
        return False
    if price is None or price <= 0:
        return True
    fc_pct = tc / float(price)
    return 0.05 <= fc_pct <= 0.70


def estimate_menu_item_profile(
    name: str,
    category: str = '',
    description: Optional[str] = None,
    ingredients: Optional[str] = None,
    price: Optional[float] = None,
    theoretical_cost: Optional[float] = None,
    ingredient_cost: Optional[float] = None,
    prep_cost: Optional[float] = None,
) -> Dict[str, object]:
    """
    Returns a dictionary with:
      description, description_source, ingredients, ingredients_source,
      ingredient_cost, ingredient_cost_source, prep_cost, prep_cost_source,
      theoretical_cost, theoretical_cost_source, estimation_method,
      estimation_source, confidence_score, cost_estimated
    """
    name = _clean_text(name)
    category = _clean_text(category)
    style = infer_style(name, category)
    price_num = None
    if price not in (None, ''):
        try:
            price_num = float(price)
        except (TypeError, ValueError):
            price_num = None

    provided_description = _clean_text(description)
    provided_ingredients = _clean_text(ingredients)

    description_generated = not provided_description
    ingredient_generated = not provided_ingredients

    if provided_ingredients:
        ingredient_list = _unique_preserve_order(
            _canonicalize_ingredient(part) or _titleize_phrase(part)
            for part in _split_ingredient_text(provided_ingredients)
        )
    else:
        ingredient_list = _estimate_ingredient_list(name, category, provided_description)

    if not ingredient_list:
        ingredient_list = _estimate_ingredient_list(name, category, provided_description)

    resolved_description = provided_description or _generate_description(name, category, ingredient_list)
    if not resolved_description:
        resolved_description = _titleize_phrase(name)

    if ingredient_generated:
        resolved_ingredients = ', '.join(ingredient_list)
    else:
        resolved_ingredients = ', '.join(ingredient_list) if ingredient_list else provided_ingredients

    if ingredient_cost not in (None, ''):
        try:
            ingredient_cost_num = float(ingredient_cost)
        except (TypeError, ValueError):
            ingredient_cost_num = None
    else:
        ingredient_cost_num = None
    if ingredient_cost_num is not None and ingredient_cost_num > 0:
        ingredient_cost_source = 'Provided'
        ingredient_cost_conf = 100.0
    else:
        ingredient_cost_num, ingredient_cost_conf = _estimate_ingredient_cost_from_list(ingredient_list, category, price_num, style)
        ingredient_cost_source = 'AI Estimated'
        if ingredient_generated:
            ingredient_cost_conf -= 3.0

    if prep_cost not in (None, ''):
        try:
            prep_cost_num = float(prep_cost)
        except (TypeError, ValueError):
            prep_cost_num = None
    else:
        prep_cost_num = None
    if prep_cost_num is not None and prep_cost_num > 0:
        prep_cost_source = 'Provided'
        prep_cost_conf = 100.0
    else:
        prep_cost_num, prep_cost_conf = _estimate_prep_cost_from_profile(
            ingredient_list, category, price_num, style, f"{name} {resolved_description}"
        )
        prep_cost_source = 'AI Estimated'
        if description_generated:
            prep_cost_conf -= 2.0

    if _plausible_theoretical_cost(theoretical_cost, price_num):
        theoretical_cost_num = float(theoretical_cost)
        theoretical_cost_source = 'Provided'
        theoretical_cost_conf = 100.0
    else:
        theoretical_cost_num = round(float(ingredient_cost_num) + float(prep_cost_num), 2)
        if ingredient_cost_source == 'Provided' and prep_cost_source == 'Provided':
            theoretical_cost_source = 'Calculated from ingredient + prep'
            theoretical_cost_conf = 98.0
        elif ingredient_cost_source == 'Provided' or prep_cost_source == 'Provided':
            theoretical_cost_source = 'Calculated from mixed provided and estimated inputs'
            theoretical_cost_conf = 90.0
        else:
            theoretical_cost_source = 'Calculated from estimated ingredient + prep'
            theoretical_cost_conf = (ingredient_cost_conf + prep_cost_conf) / 2.0

        if price_num is not None and price_num > 0:
            fc_pct = theoretical_cost_num / price_num
            if fc_pct < 0.05 or fc_pct > 0.70:
                fallback = price_num * CATEGORY_FC_BENCHMARK.get(category.upper(), DEFAULT_FC_BENCHMARK)
                theoretical_cost_num = round(fallback, 2)
                theoretical_cost_source = 'Category benchmark fallback'
                theoretical_cost_conf = 72.0 if category.upper() in CATEGORY_FC_BENCHMARK else 66.0

    estimation_items = []
    if description_generated:
        estimation_items.append('description template')
    if ingredient_generated:
        estimation_items.append('ingredient extraction template')
    if ingredient_cost_source != 'Provided':
        estimation_items.append('ingredient benchmark model')
    if prep_cost_source != 'Provided':
        estimation_items.append('prep-complexity model')
    if theoretical_cost_source != 'Provided':
        estimation_items.append('theoretical cost calculation')

    if not estimation_items:
        estimation_method = 'values provided by source file'
        estimation_source = 'source file'
        confidence = 100.0
    else:
        estimation_method = ' -> '.join(estimation_items)
        estimation_source = 'local estimator using menu item name, category, and benchmark tables'
        confidence = (
            0.18 * (100.0 if not description_generated else 86.0) +
            0.18 * (100.0 if not ingredient_generated else 82.0) +
            0.22 * float(ingredient_cost_conf) +
            0.16 * float(prep_cost_conf) +
            0.26 * float(theoretical_cost_conf)
        )

    confidence = max(50.0, min(100.0, confidence))

    return {
        'description': resolved_description,
        'description_source': 'Provided' if not description_generated else 'AI Generated',
        'ingredients': resolved_ingredients,
        'ingredients_source': 'Provided' if not ingredient_generated else 'AI Estimated',
        'ingredient_cost': round(float(ingredient_cost_num), 2),
        'ingredient_cost_source': ingredient_cost_source,
        'prep_cost': round(float(prep_cost_num), 2),
        'prep_cost_source': prep_cost_source,
        'theoretical_cost': round(float(theoretical_cost_num), 2),
        'theoretical_cost_source': theoretical_cost_source,
        'estimation_method': estimation_method,
        'estimation_source': estimation_source,
        'confidence_score': round(confidence, 1),
        'cost_estimated': theoretical_cost_source != 'Provided',
        'style': style,
    }
