"""
Data processing for the Zonal Detailed Transaction Report.

The Zonal export has one row per menu-item line inside an order. To score
fairly we need to reduce this to:
  1. A clean sales-only transaction dataset (no voids/waste/payments).
  2. A table-level opportunity view: (order, employee, date) with a single
     Covers value per order.
  3. Per-week aggregates that count each qualifying table once, so a table
     with two Prawn Cocktails only counts as one converted opportunity.

Everything downstream reads these three views.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

from .config import (
    ELIGIBLE_ROSTER,
    COMPETITORS,
    OBSERVERS,
    VALID_SALE_TYPES,
    WEEKS,
    WeekTheme,
    MIN_TABLES_WEEKLY,
    RANK_POINTS,
    DEFAULT_RANK_POINTS,
    UNQUALIFIED_POINTS,
)

REQUIRED_COLS = {
    "Date", "Time", "Order No", "Type", "Description",
    "Employee", "Table", "Covers", "Quantity", "Sales Amount",
}


def load_transactions(source) -> pd.DataFrame:
    """Load a Zonal CSV (path, file-like or bytes) into a normalised frame."""
    df = pd.read_csv(source, low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")

    # Parse date/time
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce").dt.date

    # Sales amount comes through as strings with quotes on some exports.
    df["Sales Amount"] = pd.to_numeric(df["Sales Amount"], errors="coerce").fillna(0.0)
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0).astype(int)
    df["Covers"] = pd.to_numeric(df["Covers"], errors="coerce").fillna(0).astype(int)

    df["Employee"] = df["Employee"].astype(str).str.strip()
    df["Description"] = df["Description"].astype(str).str.strip()
    df["Type"] = df["Type"].astype(str).str.strip()

    return df


def clean_sales(df: pd.DataFrame) -> pd.DataFrame:
    """Keep genuine positive sales for the eligible roster only.

    Every row's `Display` is looked up through ELIGIBLE_ROSTER, which maps
    any Zonal alias to the single canonical person name. This is where the
    many-to-one collapse happens — downstream code groups by `Display`,
    never `Employee`, so duplicate Zonal profiles score as one person.
    """
    eligible = set(ELIGIBLE_ROSTER)
    sales = df[
        df["Type"].isin(VALID_SALE_TYPES)
        & df["Employee"].isin(eligible)
        & (df["Quantity"] > 0)
    ].copy()
    sales["Display"] = sales["Employee"].map(ELIGIBLE_ROSTER)
    return sales


def build_table_view(sales: pd.DataFrame) -> pd.DataFrame:
    """One row per (Order No, Display) — the true table-opportunity view.

    Groups by canonical Display (not raw Employee alias) so a table that
    somehow ended up under both of a person's Zonal profiles still counts
    as one table for them. Zonal repeats Covers on every line of the order;
    taking the max per order removes the double-count.
    """
    grouped = (
        sales.groupby(["Order No", "Display", "Date"], as_index=False)
        .agg(covers=("Covers", "max"),
             lines=("Quantity", "count"),
             revenue=("Sales Amount", "sum"))
    )
    # Restaurant tables only: exclude zero-cover orders (breakfast/rooms/etc).
    grouped = grouped[grouped["covers"] > 0]
    return grouped


def _week_of(d: date) -> WeekTheme | None:
    for w in WEEKS:
        if w.start <= d <= w.end:
            return w
    return None


def tables_with_target_hit(sales: pd.DataFrame, week: WeekTheme) -> pd.DataFrame:
    """Return distinct (Order No, Display) tables that bought >=1 target item in this week."""
    mask = (
        (sales["Date"] >= week.start)
        & (sales["Date"] <= week.end)
        & (sales["Description"].isin(week.items))
    )
    hits = sales.loc[mask, ["Order No", "Display"]].drop_duplicates()
    return hits


def weekly_leaderboard(sales: pd.DataFrame, week: WeekTheme) -> pd.DataFrame:
    """Compute the £10 leaderboard for a single week.

    Metric: conversion rate = (tables with >=1 target item) / (eligible tables served).
    Sub-metric: incremental revenue = sum(Sales Amount for target items) / eligible tables.

    Observers (managers marked competitor=False in the roster) appear on the
    board with the same computed metrics but are excluded from ranking and
    from prize-point allocation — status = "Not competing", points = 0.
    """
    tv = build_table_view(sales)
    week_tv = tv[(tv["Date"] >= week.start) & (tv["Date"] <= week.end)]

    # Aggregate at the DISPLAY level, not the raw Employee alias. This is what
    # merges Jess's two Zonal profiles and Yasmin's two Zonal profiles into
    # one row on the board.
    per_server = (
        week_tv.groupby(["Display"], as_index=False)
        .agg(eligible_tables=("Order No", "nunique"),
             covers=("covers", "sum"))
    )

    hits = tables_with_target_hit(sales, week)
    per_server_hits = (
        hits.groupby(["Display"], as_index=False)
        .agg(tables_with_target=("Order No", "nunique"))
    )
    board = per_server.merge(per_server_hits, on=["Display"], how="left")
    board["tables_with_target"] = board["tables_with_target"].fillna(0).astype(int)

    # Target revenue (for secondary sort / display).
    mask = (
        (sales["Date"] >= week.start)
        & (sales["Date"] <= week.end)
        & (sales["Description"].isin(week.items))
    )
    rev = (
        sales.loc[mask]
        .groupby(["Display"], as_index=False)
        .agg(target_revenue=("Sales Amount", "sum"),
             target_units=("Quantity", "sum"))
    )
    board = board.merge(rev, on=["Display"], how="left")
    board[["target_revenue", "target_units"]] = board[["target_revenue", "target_units"]].fillna(0)

    board["conversion_pct"] = (
        board["tables_with_target"] / board["eligible_tables"].where(board["eligible_tables"] > 0)
    ).fillna(0) * 100

    # Split competitors vs observers up-front. Observers keep their metrics
    # for visibility but are never ranked, always sorted to the bottom.
    board["is_competitor"] = board["Display"].isin(COMPETITORS)
    board["status"] = [
        _status_label(t) if c else "Not competing"
        for t, c in zip(board["eligible_tables"], board["is_competitor"])
    ]

    # Sort: competitors first (by status then metrics), observers at the
    # bottom (by conversion_pct so the manager view still shows a ranking
    # among themselves).
    board = board.sort_values(
        ["is_competitor", "status", "conversion_pct", "target_revenue"],
        ascending=[False, True, False, False],
    ).reset_index(drop=True)

    # Rank only qualified competitor rows.
    qualified_mask = board["status"] == "Qualified"
    board["rank"] = None
    board.loc[qualified_mask, "rank"] = range(1, qualified_mask.sum() + 1)
    if len(board) == 0:
        board["points"] = pd.Series(dtype=int)
    else:
        board["points"] = board.apply(_points_for_row, axis=1).astype(int)
    return board


def _status_label(eligible_tables: int) -> str:
    if eligible_tables == 0:
        return "No recorded shift"
    if eligible_tables < MIN_TABLES_WEEKLY:
        return "Building sample"
    return "Qualified"


def _points_for_row(row) -> int:
    if row["status"] == "Not competing":
        return 0  # observers never earn prize points
    if row["status"] == "No recorded shift":
        return 0  # absence = 0, per campaign rules
    if row["status"] == "Building sample":
        return UNQUALIFIED_POINTS
    return RANK_POINTS.get(int(row["rank"]), DEFAULT_RANK_POINTS)


def overall_leaderboard(sales: pd.DataFrame) -> pd.DataFrame:
    """Combine all weekly boards into the £50 overall standings.

    Score = total(points) across ALL 5 weeks, divided by 5 (the full campaign).
    Weeks with no recorded shift OR below the table threshold count as 0 points
    — absence is not N/A. There is no minimum-weeks gate: whoever posts the
    highest average across the 5 weeks wins the £50.
    """
    rows: list[dict] = []
    for w in WEEKS:
        board = weekly_leaderboard(sales, w)
        for _, r in board.iterrows():
            rows.append({
                "week": w.number,
                "Display": r["Display"],
                "status": r["status"],
                "points": r["points"],
                "conversion_pct": r["conversion_pct"],
            })
    long = pd.DataFrame(rows)

    # Ensure every roster member (including observers) has a row for every
    # week so the overall board is complete. Absent competitor weeks = 0
    # points; absent observer weeks stay "Not competing".
    all_displays = sorted(set(ELIGIBLE_ROSTER.values()))
    seen = set(zip(long.get("Display", []), long.get("week", []))) if not long.empty else set()
    extra = []
    for disp in all_displays:
        is_comp = disp in COMPETITORS
        for w in WEEKS:
            if (disp, w.number) not in seen:
                extra.append({
                    "week": w.number, "Display": disp,
                    "status": "No recorded shift" if is_comp else "Not competing",
                    "points": 0, "conversion_pct": 0,
                })
    if extra:
        long = pd.concat([long, pd.DataFrame(extra)], ignore_index=True) if not long.empty else pd.DataFrame(extra)

    if long.empty:
        return long

    total_weeks = len(WEEKS)
    agg = (
        long.groupby(["Display"], as_index=False)
        .agg(qualified_weeks=("status", lambda s: (s == "Qualified").sum()),
             weeks_worked=("status", lambda s: (~s.isin(["No recorded shift", "Not competing"])).sum()),
             total_points=("points", "sum"),
             avg_conversion_pct=("conversion_pct", "mean"))
    )
    agg["is_competitor"] = agg["Display"].isin(COMPETITORS)
    # Average is total points divided by the full 5-week campaign, not just
    # weeks worked. Absence = 0, exactly as the rules now state.
    agg["avg_points"] = agg["total_points"] / total_weeks
    # Observers show no prize score — blank it out to avoid confusion.
    agg.loc[~agg["is_competitor"], "avg_points"] = 0.0

    # Only competitors are prize-eligible; observers are shown for visibility.
    agg["prize_eligible"] = agg["is_competitor"]
    agg = agg.sort_values(
        ["is_competitor", "total_points", "avg_conversion_pct"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    return agg


def diagnostics(df: pd.DataFrame) -> dict:
    """Surface data quality issues the supervisor should know about."""
    unmapped = sorted(
        set(df.loc[df["Type"].isin(VALID_SALE_TYPES), "Employee"]) - set(ELIGIBLE_ROSTER)
    )
    return {
        "rows_total": len(df),
        "date_min": df["Date"].min(),
        "date_max": df["Date"].max(),
        "sale_rows": int((df["Type"].isin(VALID_SALE_TYPES)).sum()),
        "unmapped_employees": unmapped,
    }
