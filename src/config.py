"""
Configuration for the George & Dragon Upsell Incentive Scoreboard.

All roster, taxonomy, thresholds and scoring rules live here so the app
logic stays generic. Change values here to re-tune the incentive without
touching processing code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# ---------------------------------------------------------------------------
# Roster (competitors + observing managers).
# Each Person has:
#   - display: the name shown on the leaderboard
#   - aliases: one or more exact Zonal `Employee` strings (case-sensitive)
#              that all resolve to this same person. Enables Zonal duplicate
#              profiles (e.g. "Jessica Yates" and "jess yates") to be scored
#              as one person, and typos ("Summer Smmith") to display cleanly.
#   - competitor: True = ranked and prize-eligible; False = shown on the
#              board with metrics but greyed out, no rank, no prize (managers
#              wanting visibility into their own numbers).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Person:
    display: str
    aliases: tuple[str, ...]
    competitor: bool = True

# NOTE: the roster in this public repo is EXAMPLE data. Real employees are
# configured via `src/roster_local.py` (git-ignored) or Streamlit Cloud
# Secrets. See loaders below.
ROSTER: tuple[Person, ...] = (
    Person("Server 1", ("Server One",)),
    Person("Server 2", ("Server Two",)),
    Person("Server 3", ("Server Three",)),
    Person("Server 4", ("Server Four",)),
    Person("Manager A", ("Manager One",), competitor=False),
)

# Loader state — surfaced in the Admin tab so we can see which override,
# if any, took effect instead of silently falling back.
ROSTER_SOURCE: str = "example (src/config.py)"
ROSTER_SECRETS_ERROR: str | None = None

# Optional local override: if `src/roster_local.py` exists (git-ignored) and
# defines ROSTER, it replaces the example above. Legacy ELIGIBLE_ROSTER dict
# is also accepted (all entries become competitors).
try:
    from . import roster_local as _local  # type: ignore
    if hasattr(_local, "ROSTER"):
        ROSTER = tuple(_local.ROSTER)
        ROSTER_SOURCE = "src/roster_local.py (ROSTER)"
    elif hasattr(_local, "ELIGIBLE_ROSTER"):
        ROSTER = tuple(
            Person(display=str(v), aliases=(str(k),), competitor=True)
            for k, v in dict(_local.ELIGIBLE_ROSTER).items()
        )
        ROSTER_SOURCE = "src/roster_local.py (ELIGIBLE_ROSTER, legacy)"
except ImportError:
    pass

# Streamlit Cloud override. Preferred format is an array-of-tables so one
# person can have multiple Zonal aliases and be marked competitor or not:
#
#     [[roster]]
#     display = "Jess"
#     aliases = ["Jessica Yates", "jess yates"]
#     competitor = false
#
# Legacy flat-mapping format ([roster] with "Zonal" = "Display") is still
# accepted; every entry becomes a competitor with one alias.
try:
    import streamlit as _st  # type: ignore
    _secret_roster = None
    if hasattr(_st, "secrets"):
        try:
            _secret_roster = _st.secrets["roster"]
        except Exception:
            _secret_roster = _st.secrets.get("roster") if hasattr(_st.secrets, "get") else None
    if _secret_roster:
        # Detect shape: list-of-tables vs flat dict.
        _parsed: list[Person] = []
        if isinstance(_secret_roster, (list, tuple)):
            for entry in _secret_roster:
                disp = str(entry["display"]).strip()
                aliases_raw = entry.get("aliases") if hasattr(entry, "get") else entry["aliases"]
                aliases = tuple(str(a).strip() for a in aliases_raw)
                comp = bool(entry.get("competitor", True)) if hasattr(entry, "get") else True
                if disp and aliases:
                    _parsed.append(Person(display=disp, aliases=aliases, competitor=comp))
            _shape = f"array-of-tables, {len(_parsed)} people"
        else:
            for k in _secret_roster:
                zonal = str(k).strip()
                disp = str(_secret_roster[k]).strip()
                if zonal and disp:
                    _parsed.append(Person(display=disp, aliases=(zonal,), competitor=True))
            _shape = f"flat mapping, {len(_parsed)} entries"
        if _parsed:
            ROSTER = tuple(_parsed)
            ROSTER_SOURCE = f"Streamlit Cloud Secrets ({_shape})"
except Exception as _e:  # noqa: BLE001
    ROSTER_SECRETS_ERROR = f"{type(_e).__name__}: {_e}"

# ---------------------------------------------------------------------------
# Derived lookups used by the processing engine.
#   ELIGIBLE_ROSTER: exact Zonal name -> display name (many aliases -> one
#                    display; enables the Zonal-duplicate collapse)
#   COMPETITORS:     set of display names that are prize-eligible; observers
#                    are excluded from ranking and prize logic
# ---------------------------------------------------------------------------

ELIGIBLE_ROSTER: dict[str, str] = {
    alias: p.display for p in ROSTER for alias in p.aliases
}
COMPETITORS: set[str] = {p.display for p in ROSTER if p.competitor}
OBSERVERS: set[str] = {p.display for p in ROSTER if not p.competitor}

# ---------------------------------------------------------------------------
# Transaction hygiene: only these Type values contribute to scoring.
# Everything else (voids, wastage, payments, merges, etc.) is stripped out.
# ---------------------------------------------------------------------------

VALID_SALE_TYPES: set[str] = {"Sale"}

# ---------------------------------------------------------------------------
# Incentive campaign definition.
# Each week has a theme and a list of qualifying menu items (Description
# values as they appear in Zonal). Items may appear in more than one week
# by design (e.g. Cheese Selection = shareable board in Week 1 AND cheese
# course in Week 4). Double counting inside a single week's leaderboard
# is prevented by table-level conversion (one qualifying table = one hit).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WeekTheme:
    number: int
    name: str
    start: date
    end: date
    items: tuple[str, ...]

CAMPAIGN_START = date(2026, 9, 14)  # Monday, Paige's first shift

WEEKS: tuple[WeekTheme, ...] = (
    WeekTheme(
        number=1,
        name="Nibbles + Premium Sharing / Occasion Food",
        start=date(2026, 9, 14),
        end=date(2026, 9, 20),
        items=(
            "Baguette & Dips",
            "Olives Rustica",
            "Scotch Egg",
            "Blyth Saus Roll",
            "Chicken Wings",
            "Houmous &Falafel",
            "Tomato Arancini",
            "Charcuterie Plat",
            "Vegetarian Board",
            "Cheese Selection",
        ),
    ),
    WeekTheme(
        number=2,
        name="Starters",
        start=date(2026, 9, 21),
        end=date(2026, 9, 27),
        items=(
            "Prawn Cocktail",
            "Devon Crab",
            "Cheese Souffle",
            "Goats Cheese",
            "Crab & Avocado",
            "Beetroot Terrine",
            "Fish Soup",
            "Scallops Crusted",
            "Sun Beet Parcel",
            "Tomato Burrata",
        ),
    ),
    WeekTheme(
        number=3,
        name="Sides & Upgrades",
        start=date(2026, 9, 28),
        end=date(2026, 10, 4),
        items=(
            "TripleCook Chips",
            "Tom. Salad Side",
            "Green Beans",
            "Mixed Leaf Salad",
            "Parslied Potato",
            "Ratatouille",
            "Pepper Sauce",
            "Bearnaise Sauce",
            "Roquefort Sauce",
            "Black GarlicMayo",
        ),
    ),
    WeekTheme(
        number=4,
        name="Desserts",
        start=date(2026, 10, 5),
        end=date(2026, 10, 11),
        items=(
            "StickyToffee Pud",
            "PistachioSouffle",
            "Basque Cheesecak",
            "Dark Choc Mousse",
            "Chocolat Cremeux",
            "Ice Cream",
            "Cheese Selection",
            "Strawberry Theme",
        ),
    ),
    WeekTheme(
        number=5,
        name="Teas, Coffees & Digestives",
        start=date(2026, 10, 12),
        end=date(2026, 10, 18),
        items=(
            "Americano Coffee",
            "Cappuccino",
            "Latte",
            "Flat White",
            "Espresso Coffee",
            "Tea Gold",
            "Tea Peppermint",
            "Amaretto",
            "Baby Guinness",
            "Espresso Martini",
        ),
    ),
)

# ---------------------------------------------------------------------------
# Fairness rules.
# ---------------------------------------------------------------------------

# Minimum eligible tables served in a week before a server is ranked
# for the £10 weekly prize. Lowered from 20 to 15 to open eligibility
# to shorter-week/part-time staff while still filtering pure noise.
MIN_TABLES_WEEKLY: int = 15

# Overall £50 prize: no minimum-weeks gate. Whoever posts the best
# average points across the campaign wins, however many weeks they worked.
# (Set to 0 so old code paths still evaluate cleanly.)
MIN_QUALIFIED_WEEKS: int = 0

# Weekly ranking -> points (0-80).
# Anyone qualified below 8th gets 10 participation points.
RANK_POINTS: dict[int, int] = {
    1: 80, 2: 70, 3: 60, 4: 50, 5: 40, 6: 30, 7: 20, 8: 10,
}
DEFAULT_RANK_POINTS: int = 10  # qualified but below 8th
UNQUALIFIED_POINTS: int = 0    # worked but below MIN_TABLES_WEEKLY

# Prize values.
PRIZE_WEEKLY_GBP: int = 10
PRIZE_OVERALL_GBP: int = 50

# Historical fields remain for compatibility with imports. The scoring engine
# uses effective-dated menus, never these undated future-week reference lists.
from dataclasses import replace as _replace
from .menus import NIBBLES, STARTERS
WEEKS = tuple(_replace(w, items=NIBBLES if w.number == 1 else
                       STARTERS if w.number == 2 else ()) for w in WEEKS)
