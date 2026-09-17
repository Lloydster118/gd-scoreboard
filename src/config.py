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
# Eligible floor-team roster (competition entrants only).
# Names MUST match the exact Employee spelling in the Zonal export.
# Anything not in this list is excluded from scoring regardless of activity.
# ---------------------------------------------------------------------------

# NOTE: the roster in this public repo is EXAMPLE data. Real employee names
# are configured only in the private/local deployment. If you fork this repo,
# replace these entries with the exact `Employee` strings from your own Zonal
# export. Any Employee not in this dictionary is silently excluded from scoring.
ELIGIBLE_ROSTER: dict[str, str] = {
    # exact Zonal name  ->  display name
    "Server One":   "Server 1",
    "Server Two":   "Server 2",
    "Server Three": "Server 3",
    "Server Four":  "Server 4",
    "Server Five":  "Server 5",
    "Server Six":   "Server 6",
    "Server Seven": "Server 7",
    "Server Eight": "Server 8",
}

# Optional local override: if `src/roster_local.py` exists (git-ignored), it
# replaces the example roster above. This is how the real deployment injects
# real employee names without exposing them in the public repo.
try:
    from .roster_local import ELIGIBLE_ROSTER as _LOCAL_ROSTER  # type: ignore
    ELIGIBLE_ROSTER = _LOCAL_ROSTER
except ImportError:
    pass

# Streamlit Cloud override: if a [roster] section is defined in the app's
# Secrets, use that instead. This is how the deployed app injects real
# employee names without exposing them in this public repo. Format:
#
#     [roster]
#     "Zonal Employee Name" = "Display Name"
#
# Silently ignored outside a Streamlit runtime, in tests, or if the section
# is absent / malformed.
# Set by the loader below so app.py can surface *why* the override didn't
# take effect instead of silently falling back to the example roster.
ROSTER_SOURCE: str = "example (src/config.py)"
ROSTER_SECRETS_ERROR: str | None = None

try:
    from .roster_local import ELIGIBLE_ROSTER as _LOCAL_ROSTER2  # type: ignore  # noqa: F401
    ROSTER_SOURCE = "src/roster_local.py"
except ImportError:
    pass

try:
    import streamlit as _st  # type: ignore
    _secret_roster = None
    if hasattr(_st, "secrets"):
        # Both access styles: .get() works in newer Streamlit, subscript is
        # the reliable path across all versions when the key exists.
        try:
            _secret_roster = _st.secrets["roster"]
        except Exception:
            _secret_roster = _st.secrets.get("roster") if hasattr(_st.secrets, "get") else None
    if _secret_roster:
        # Streamlit's Secrets object behaves like a Mapping; iterate keys
        # directly rather than relying on dict() coercion.
        _parsed = {str(k): str(_secret_roster[k]) for k in _secret_roster}
        if _parsed:
            ELIGIBLE_ROSTER = _parsed
            ROSTER_SOURCE = f"Streamlit Cloud Secrets ([roster], {len(_parsed)} entries)"
except Exception as _e:  # noqa: BLE001
    ROSTER_SECRETS_ERROR = f"{type(_e).__name__}: {_e}"

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
