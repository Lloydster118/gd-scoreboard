"""Account-based scoring, retaining payment and exception evidence.

Ambiguous correction/transfer semantics are never guessed. A reviewed account
can supply its verified final sales, with a fingerprint that expires on change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import hashlib
import io
import json

import pandas as pd

from .config import (
    ELIGIBLE_ROSTER, COMPETITORS, WEEKS, MIN_TABLES_WEEKLY,
    RANK_POINTS, DEFAULT_RANK_POINTS, CAMPAIGN_START,
)
from .menus import DEFAULT_MENUS, is_staff_food, menu_on, validate_menus

REQUIRED_COLS = {
    "Date", "Time", "Order No", "Account ID", "Type", "Description",
    "Employee", "Table", "Covers", "Quantity", "Sales Amount",
}
STORED_COLS = sorted(REQUIRED_COLS | {"Payment Amount"})
SAFE_TYPES = {"Sale", "Payment", "Discount", "Correcte d Discount"}
SALE_FIELDS = ["Date", "Employee", "Description", "Quantity", "Sales Amount"]
EXCLUDED_OWNER = "(outside competition roster)"


def load_transactions(source):
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    df = pd.read_csv(source, dtype=str, keep_default_na=False)
    df.columns = df.columns.str.strip()
    if df.columns.duplicated().any():
        raise ValueError("Duplicate CSV column names.")
    missing = REQUIRED_COLS - set(df)
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")
    if df.empty:
        raise ValueError("The export contains no transactions.")
    for col in REQUIRED_COLS:
        df[col] = df[col].str.strip()
    parsed = pd.to_datetime(df["Date"], format="mixed", dayfirst=True, errors="coerce")
    if parsed.isna().any():
        raise ValueError("Invalid or missing transaction dates.")
    df["Date"] = parsed.dt.date
    for col in ("Covers", "Quantity", "Sales Amount", "Payment Amount"):
        if col not in df:
            df[col] = "0"
        text = df[col].str.strip().replace("", "0")
        commas = text.str.contains(",", regex=False)
        valid_grouped = text.str.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?")
        if (commas & ~valid_grouped).any():
            raise ValueError(f"Invalid thousands separators in {col}.")
        values = pd.to_numeric(text.str.replace(",", "", regex=False), errors="coerce")
        if values.isna().any() or not values.map(lambda x: float("-inf") < x < float("inf")).all():
            raise ValueError(f"Invalid numeric values in {col}.")
        if col in ("Covers", "Quantity") and (values % 1 != 0).any():
            raise ValueError(f"{col} must contain whole numbers.")
        df[col] = values
    if (df["Covers"] < 0).any():
        raise ValueError("Negative covers need source-data correction.")
    if df["Account ID"].eq("").any():
        raise ValueError("Missing Account ID; cannot safely identify visits.")
    return df[STORED_COLS].copy()


def validate_replacement(new, old=None, allow_reduction=False, today=None):
    """Validate before any persistence. Never append or deduplicate item rows."""
    today = today or date.today()
    if new["Date"].min() != CAMPAIGN_START:
        raise ValueError("Upload a complete cumulative export starting 14 September 2026.")
    if new["Date"].max() > today:
        raise ValueError("The export contains future-dated transactions.")
    if old is not None and not allow_reduction:
        if new["Date"].max() < old["Date"].max():
            raise ValueError("The new export ends earlier than the saved export.")
        if not set(old["Account ID"]).issubset(set(new["Account ID"])):
            raise ValueError("Previously uploaded accounts are missing.")
        before = old.groupby("Date").size()
        after = new.groupby("Date").size().reindex(before.index, fill_value=0)
        if (after < before).any():
            raise ValueError("Some dates contain fewer rows; confirm a deliberate corrected replacement.")
    return new


def fingerprint(account):
    stable = account[STORED_COLS].astype(str).to_dict("records")
    lines = sorted(json.dumps(r, sort_keys=True) for r in stable)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


@dataclass
class ScoringResult:
    accounts: pd.DataFrame
    credits: pd.DataFrame
    unknown_products: pd.DataFrame
    categories: dict = field(default_factory=dict)


def _reviewed_sales(review, account):
    final = review.get("final_sales")
    if final is None:
        return None
    if not isinstance(final, list):
        raise ValueError("final_sales must be a list.")
    result = pd.DataFrame(final, columns=SALE_FIELDS)
    if result.empty:
        return result
    result["Date"] = result["Date"].map(lambda x: date.fromisoformat(str(x)))
    if not result["Date"].isin(account["Date"]).all():
        raise ValueError("Reviewed sale dates must exist on this account.")
    for col in ("Quantity", "Sales Amount"):
        result[col] = pd.to_numeric(result[col], errors="raise")
        if not result[col].map(lambda x: float("-inf") < x < float("inf")).all():
            raise ValueError("Reviewed sales must be finite.")
    if (result["Quantity"] < 0).any() or (result["Quantity"] % 1 != 0).any():
        raise ValueError("Reviewed quantities must be nonnegative whole portions.")
    for col in ("Employee", "Description"):
        if not result[col].map(lambda x: isinstance(x, str) and bool(x.strip())).all():
            raise ValueError("Reviewed sales need exact employee and product names.")
    return result


def score_accounts(raw, menus=None, reviews=None, roster=None, category=None):
    menus = validate_menus(DEFAULT_MENUS if menus is None else menus)
    reviews = reviews or {}
    roster = ELIGIBLE_ROSTER if roster is None else roster
    accounts, credits, unknown = [], [], []
    for account_id, account in raw.groupby("Account ID", sort=False):
        original = account[(account["Type"] == "Sale") & (account["Quantity"] > 0)]
        original = original[~original["Description"].map(is_staff_food)]
        if original.empty:
            continue
        day = original["Date"].min()
        if not any(w.start <= day <= w.end for w in WEEKS):
            continue
        tables = sorted(set(original["Table"]) - {"", "nan", "None"})
        covers_values = set(original.loc[original["Covers"] > 0, "Covers"])
        covers = max(covers_values, default=0)
        if not tables or covers <= 0:
            # A till default is not proof that no dining visit happened.
            # Keep otherwise identifiable main-course accounts in the audit.
            has_main = any(
                (mapping := menu_on(r["Date"], menus)) is not None
                and r["Description"] in mapping["mains"]
                for _, r in original.iterrows()
            )
            if not has_main:
                continue
        fp = fingerprint(account)
        supplied = reviews.get(str(account_id), {})
        review = supplied if supplied.get("fingerprint") == fp and supplied.get("note", "").strip() else {}
        issues, blockers, data_notes = [], [], []
        if supplied and not review:
            issues.append("Review expired: account changed or note missing")
        if review.get("exclude", False):
            accounts.append(dict(account_id=account_id, Date=day, table=", ".join(tables),
                                 covers=covers, owner=None, counted=False, credit_allowed=False,
                                 issues="", fingerprint=fp, data_notes="Excluded by review",
                                 owner_identity=None))
            continue
        settled = (account["Type"].eq("Payment") & account["Payment Amount"].gt(0)).any()
        deposit = (account["Type"].eq("Ledger") & account["Description"].eq("Deposit Red")
                   & account["Payment Amount"].gt(0)).any()
        if not settled and not review.get("settlement_confirmed"):
            blockers.append("Deposit redemption needs settlement review" if deposit else "No positive payment evidence")
        if account["Type"].eq("Reverse Pay").any() and not review.get("settlement_confirmed"):
            blockers.append("Reversed payment needs settlement confirmation")
        # Only mains and that sale date's incentive targets affect this score.
        # A drink/dessert movement must not discard otherwise valid food sales.
        # Refund/reversal evidence still requires conservative settlement review.
        correction = False
        for _, row in account.iterrows():
            if row["Type"] in SAFE_TYPES and not (row["Type"] == "Sale" and row["Quantity"] < 0):
                continue
            if is_staff_food(row["Description"]):
                continue
            menu = menu_on(row["Date"], menus)
            week = (WEEKS[category - 1] if category else
                    next((w for w in WEEKS if w.start <= row["Date"] <= w.end), None))
            relevant = bool(menu and (row["Description"] in menu["mains"] or
                            (week and row["Description"] in menu["targets"].get(str(week.number), []))))
            financial_reversal = any(x in row["Type"].lower() for x in ("reverse", "refund"))
            if relevant or financial_reversal:
                correction = True
        final_sales = _reviewed_sales(review, account) if review else None
        if correction and final_sales is None:
            blockers.append("Corrections/transfers: verified final sales required")
        sales = original[SALE_FIELDS].copy() if final_sales is None else final_sales.copy()
        sales = sales[~sales["Description"].map(is_staff_food)]
        owner_weights = {}
        missing_menu = False
        for _, row in sales.iterrows():
            menu = menu_on(row["Date"], menus)
            if menu is None:
                missing_menu = True
                unknown.append(dict(Date=row["Date"], Description=row["Description"], reason="No confirmed menu"))
                continue
            weight = menu["mains"].get(row["Description"], 0)
            if weight and row["Quantity"] > 0:
                person = roster.get(row["Employee"], EXCLUDED_OWNER + ": " + row["Employee"])
                owner_weights[person] = owner_weights.get(person, 0) + row["Quantity"] * weight
            elif row["Description"] not in set(sum(menu["targets"].values(), [])):
                unknown.append(dict(Date=row["Date"], Description=row["Description"],
                                    reason="Not mapped as main or confirmed target; may be drink/modifier"))
        if missing_menu:
            blockers.append("No confirmed menu for sale date")
        for sale_day in set(sales["Date"]):
            active = (WEEKS[category - 1] if category else
                      next((w for w in WEEKS if w.start <= sale_day <= w.end), None))
            mapping = menu_on(sale_day, menus)
            if active and mapping and str(active.number) not in mapping["targets"]:
                blockers.append(f"Week {active.number} targets not confirmed")
        sale_weeks = {w.number for w in WEEKS if sales["Date"].between(w.start, w.end).any()}
        if len(sale_weeks) > 1:
            blockers.append("Account spans incentive weeks; check source Account ID")
        owner = None
        if owner_weights:
            highest = max(owner_weights.values())
            winners = [p for p, qty in owner_weights.items() if qty == highest]
            if len(winners) == 1:
                owner = winners[0]
            else:
                issues.append("Tied main-course ownership")
        else:
            issues.append("No recognised main-course ownership")
        preorder = original["Description"].isin(["Pre-Order Dinner", "Group Dining 3CR"]).any()
        # Confirmed private operational exception, configured as a review rather
        # than hard-coding any real customer's account in public source.
        if review.get("preorder"):
            preorder = True
        if preorder:
            issues.append("Preorder: serving owner needs confirmation")
            owner = None
            if any(w.number > 1 and sales["Date"].between(w.start, w.end).any() for w in WEEKS):
                if not review.get("preorder_targets_confirmed") or final_sales is None:
                    blockers.append("Preselected courses: verified target eligibility and final sales required")
        if not tables:
            data_notes.append("Table number missing; Account ID identifies visit")
        if covers <= 0 or len(covers_values) > 1 or sum(owner_weights.values()) > covers:
            data_notes.append("Covers missing/inconsistent; not used to assign ownership")
        if len(tables) > 1:
            issues.append("Multiple table numbers: ownership needs review")
            owner = None
        override = review.get("owner")
        if override:
            if override not in set(roster.values()) | {EXCLUDED_OWNER}:
                raise ValueError("Reviewed owner is not in the current roster.")
            owner = override
            issues = [x for x in issues if x.startswith("Review expired")]
        owner_identity = owner
        if owner and owner.startswith(EXCLUDED_OWNER):
            owner = EXCLUDED_OWNER
        if owner is None:
            issues.append("Owner unresolved; denominator held")
        if review.get("no_main_confirmed"):
            if owner_weights or override or preorder:
                blockers.append("No-main review conflicts with recognised main ownership; review again")
            else:
                issues = [x for x in issues if x not in (
                    "No recognised main-course ownership", "Owner unresolved; denominator held")]
                data_notes.append("Reviewed non-main account: no opportunity; item credit subject to category rules")
        # Missing owners must not silently improve another person's rate:
        # affected weeks are explicitly provisional until reviewed.
        issues.extend(blockers)
        credit_allowed = not blockers
        if credit_allowed:
            for _, row in sales.iterrows():
                week = (WEEKS[category - 1] if category else
                        next((w for w in WEEKS if w.start <= row["Date"] <= w.end), None))
                menu = menu_on(row["Date"], menus)
                if week is None or menu is None:
                    continue
                if str(week.number) not in menu["targets"]:
                    issues.append(f"Week {week.number} targets not confirmed")
                    continue
                # Starters and desserts reward an additional course with a
                # recognised main, not standalone snacks/sweets. Owner overrides
                # and package charges alone are not main-course evidence.
                # Zero-priced included courses are eligible actual portions.
                if week.number in (2, 4) and not owner_weights:
                    continue
                if preorder and week.number > 1 and not review.get("preorder_targets_confirmed"):
                    issues.append("Preselected courses: target eligibility needs review")
                    continue
                if row["Description"] in menu["targets"][str(week.number)] and row["Quantity"] > 0:
                    person = roster.get(row["Employee"], EXCLUDED_OWNER + ": " + row["Employee"])
                    if person:
                        credits.append(dict(account_id=account_id, Date=row["Date"], week=week.number,
                                            Display=person, target_units=row["Quantity"],
                                            target_revenue=row["Sales Amount"]))
        accounts.append(dict(account_id=account_id, Date=day, table=", ".join(tables),
                             covers=covers, owner=owner, counted=bool(owner) and not blockers,
                             credit_allowed=credit_allowed, issues="; ".join(dict.fromkeys(issues)),
                             fingerprint=fp, data_notes="; ".join(data_notes),
                             owner_identity=owner_identity))
    result = ScoringResult(
        pd.DataFrame(accounts, columns=["account_id", "Date", "table", "covers", "owner",
                                       "counted", "credit_allowed", "issues", "fingerprint", "data_notes",
                                       "owner_identity"]),
        pd.DataFrame(credits, columns=["account_id", "Date", "week", "Display",
                                      "target_units", "target_revenue"]),
        pd.DataFrame(unknown, columns=["Date", "Description", "reason"]).drop_duplicates(),
    )
    if category is None and not raw.empty:
        for theme in WEEKS:
            # Preserve whole accounts and fingerprints, including cross-week
            # exceptions. A partial account must never become a valid visit.
            first_days = raw[raw["Type"].eq("Sale") & raw["Quantity"].gt(0)].groupby("Account ID")["Date"].min()
            ids = first_days[first_days.between(theme.start, WEEKS[-1].end)].index
            subset = raw[raw["Account ID"].isin(ids)]
            if not subset.empty:
                result.categories[theme.number] = score_accounts(
                    subset, menus, reviews, roster, category=theme.number)
    return result


def weekly_leaderboard(result, week, roster=None, competitors=None, *, period_end=None):
    roster = ELIGIBLE_ROSTER if roster is None else roster
    competitors = COMPETITORS if competitors is None else competitors
    accounts = result.accounts
    end = week.end if period_end is None else period_end
    accounts = accounts[accounts["Date"].between(week.start, end)]
    units = result.credits[(result.credits["week"] == week.number)
                           & result.credits["Date"].between(week.start, end)]
    opportunities = {}
    sellers = units.groupby("account_id")["Display"].agg(set).to_dict()
    for account in accounts[accounts["counted"]].itertuples():
        identity = getattr(account, "owner_identity", account.owner)
        participants = sellers.get(account.account_id, set()) | {identity}
        share = 1.0 / len(participants)
        for person in participants:
            opportunities[person] = opportunities.get(person, 0.0) + share
    rows = []
    for person in sorted(set(roster.values())):
        own_units = units[units["Display"].eq(person)]
        n, qty = opportunities.get(person, 0.0), own_units["target_units"].sum()
        status = ("Not competing" if person not in competitors else
                  "Qualified" if n >= MIN_TABLES_WEEKLY - 1e-9 else
                  "Building sample" if n else "Rate unavailable" if qty else "No recorded shift")
        rows.append(dict(Display=person, eligible_tables=n, target_units=qty,
                         target_revenue=own_units["target_revenue"].sum(),
                         portions_per_100_tables=qty / n * 100 if n else float("nan"),
                         status=status, is_competitor=person in competitors))
    board = pd.DataFrame(rows)
    if board.empty:
        return board
    order = {"Qualified": 0, "Building sample": 1, "Rate unavailable": 2,
             "No recorded shift": 3, "Not competing": 4}
    board["_sort"] = board["status"].map(order)
    board = board.sort_values(["_sort", "portions_per_100_tables", "target_revenue", "Display"],
                              ascending=[True, False, False, True]).drop(columns="_sort").reset_index(drop=True)
    board["rank"] = pd.Series([None] * len(board), dtype=object)
    rank, previous = 0, None
    for pos, idx in enumerate(board.index[board["status"].eq("Qualified")], start=1):
        key = (board.at[idx, "portions_per_100_tables"], board.at[idx, "target_revenue"])
        if key != previous:
            rank = pos
        board.at[idx, "rank"] = rank
        previous = key
    board["points"] = [RANK_POINTS.get(int(r), DEFAULT_RANK_POINTS) if r is not None else 0 for r in board["rank"]]
    board["provisional"] = bool(accounts["issues"].ne("").any())
    return board


def category_leaderboard(result, week, roster=None, competitors=None):
    rolling = result.categories.get(week.number)
    if rolling is None:
        rolling = ScoringResult(result.accounts.iloc[:0], result.credits.iloc[:0],
                                result.unknown_products.iloc[:0])
    return weekly_leaderboard(rolling, week, roster, competitors, period_end=WEEKS[-1].end)


def overall_leaderboard(result, roster=None, competitors=None):
    boards = [category_leaderboard(result, w, roster, competitors).assign(week=w.number) for w in WEEKS]
    long = pd.concat(boards, ignore_index=True)
    if long.empty:
        return long
    overall = (long.groupby(["Display", "is_competitor"], as_index=False)
            .agg(total_points=("points", "sum"),
                 categories_qualified=("status", lambda x: int(x.eq("Qualified").sum())))
            .sort_values(["is_competitor", "total_points", "Display"], ascending=[False, False, True]))
    overall["overall_score"] = overall["total_points"] / max(RANK_POINTS.values()) * (100 / len(WEEKS))
    for week in WEEKS:
        points = long[long["week"].eq(week.number)].set_index("Display")["points"]
        overall[f"category_{week.number}"] = overall["Display"].map(points) / max(RANK_POINTS.values()) * 20
    overall["provisional"] = bool(long["provisional"].any())
    return overall
