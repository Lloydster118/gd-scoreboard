"""Effective-dated, exact-name menu rules. Unknown menus fail closed."""
from datetime import date
import copy
import re

MAINS = (
    "Chicken Tagine", "Summer Risotto", "Tom Sld Burrata", "Pork T-hawk",
    "Duck Breast", "Ricotta Ravioli", "Chicken Leek Pie", "Scallops Crusted",
    "Burger & Fries", "Sea Bream", "Devon Crab", "MalabarFishCurry",
    "Chickpea Sld MC", "Sirloin 8oz", "Fillet 7oz", "Fish & Chips",
    "PF Steak & Chips", "PF Pork Loin", "PF Haddock Fille", "PF Tomato Penne",
    "CH Beef Burger", "CH Chicken Roula", "CH Sausage &Mash",
    "CH Veg Bolognese", "CH Grilled Trout", "Sunday Beef", "Sunday Chicken",
    "Sunday Pork Loin", "SM Apricot Tagin", "SM Summer Risott",
)
STARTERS = (
    "Cheese Souffle", "Fish Soup", "Prawn Cocktail", "Chicken Wings",
    "Goats Cheese", "Houmous &Falafel", "Beetroot Terrine", "Crab & Avocado",
    "CH Crudites Houm", "CH Breadsticks", "PF S'cornChowder",
    "PFPottedMackerel", "PF Plum Salad",
)
NIBBLES = (
    "Baguette & Dips", "Olives Rustica", "Scotch Egg", "Blyth Saus Roll",
    "Houmous &Falafel", "Tomato Arancini",
    "Charcuterie Plat", "Vegetarian Board", "Cheese Selection",
)
DEFAULT_MENUS = [{
    "name": "Confirmed outgoing menu",
    "start": "2026-09-14", "end": "2026-09-23",
    "mains": {**{name: 1 for name in MAINS}, "Chateaubriand": 2},
    "targets": {"1": list(NIBBLES), "2": list(STARTERS)},
}]


def is_staff_food(description):
    return bool(re.search(r"(?<!\w)SF(?!\w)", str(description), re.I))


def validate_menus(menus):
    if not isinstance(menus, list) or not menus:
        raise ValueError("Menus must be a nonempty JSON list.")
    result = copy.deepcopy(menus)
    periods = []
    for menu in result:
        start, end = date.fromisoformat(menu["start"]), date.fromisoformat(menu["end"])
        if start > end or not str(menu.get("name", "")).strip():
            raise ValueError("Each menu needs a name and an ordered date range.")
        if any(start <= b and end >= a for a, b in periods):
            raise ValueError("Menu date ranges must not overlap.")
        periods.append((start, end))
        if not isinstance(menu["mains"], dict) or not menu["mains"]:
            raise ValueError("Each confirmed menu needs main-course mappings.")
        for name, weight in menu["mains"].items():
            if not isinstance(name, str) or not name.strip() or type(weight) is not int or weight < 1:
                raise ValueError("Main mappings need exact names and positive integer weights.")
        if not isinstance(menu["targets"], dict):
            raise ValueError("Targets must map week numbers to lists of exact names.")
        for week, items in menu["targets"].items():
            if week not in {"1", "2", "3", "4", "5"} or not isinstance(items, list):
                raise ValueError("Invalid target week or product list.")
            if any(not isinstance(x, str) or not x.strip() or is_staff_food(x) for x in items):
                raise ValueError("Target names must be nonblank and cannot be staff food.")
            if len(set(items)) != len(items):
                raise ValueError("Duplicate target names.")
    return sorted(result, key=lambda m: m["start"])


def menu_on(day, menus):
    return next((m for m in menus if date.fromisoformat(m["start"]) <= day
                 <= date.fromisoformat(m["end"])), None)
