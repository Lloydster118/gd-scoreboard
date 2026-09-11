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
    """Keep genuine positive sales for the eligible roster only."""
    eligible = set(ELIGIBLE_ROSTER)
    sales = df[
        df["Type"].isin(VALID_SALE_TYPES)
        & df["Employee"].isin(eligible)
        & (df["Quantity"] > 0)
    ].copy()
    sales["Display"] = sales["Employee"].map(ELIGIBLE_ROSTER)
    return sales


def build_table_view(sales: pd.DataFrame) -> pd.DataFrame:
    """One row per (Order No, Employee) — the true table-opportunity view.

    Zonal repeats Covers on every line of the order; taking the max
    per order removes the double-count.
    """
    grouped = (
        sales.groupby(["Order No", "Employee", "Display", "Date"], as_index=False)
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
    """Return distinct (Order No, Employee) tables that bought >=1 target item in this week."""
    mask = (
        (sales["Date"] >= week.start)
        & (sales["Date"] <= week.end)
        & (sales["Description"].isin(week.items))
    )
    hits = sales.loc[mask, ["Order No", "Employee", "Display"]].drop_duplicates()
    return hits


def weekly_leaderboard(sales: pd.DataFrame, week: WeekTheme) -> pd.DataFrame:
    """Compute the £10 leaderboard for a single week.

    Metric: conversion rate = (tables with >=1 target item) / (eligible tables served).
    Sub-metric: incremental revenue = sum(Sales Amount for target items) / eligible tables.
    """
    tv = build_table_view(sales)
    week_tv = tv[(tv["Date"] >= week.start) & (tv["Date"] <= week.end)]

    per_server = (
        week_tv.groupby(["Employee", "Display"], as_index=False)
        .agg(eligible_tables=("Order No", "nunique"),
             covers=("covers", "sum"))
    )

    hits = tables_with_target_hit(sales, week)
    per_server_hits = (
        hits.groupby(["Employee", "Display"], as_index=False)
        .agg(tables_with_target=("Order No", "nunique"))
    )
    board = per_server.merge(per_server_hits, on=["Employee", "Display"], how="left")
    board["tables_with_target"] = board["tables_with_target"].fillna(0).astype(int)

    # Target revenue (for secondary sort / display).
    mask = (
        (sales["Date"] >= week.start)
        & (sales["Date"] <= week.end)
        & (sales["Description"].isin(week.items))
    )
    rev = (
        sales.loc[mask]
        .groupby(["Employee", "Display"], as_index=False)
        .agg(target_revenue=("Sales Amount", "sum"),
             target_units=("Quantity", "sum"))
    )
    board = board.merge(rev, on=["Employee", "Display"], how="left")
    board[["target_revenue", "target_units"]] = board[["target_revenue", "target_units"]].fillna(0)

    board["conversion_pct"] = (
        board["tables_with_target"] / board["eligible_tables"].where(board["eligible_tables"] > 0)
    ).fillna(0) * 100

    board["status"] = board["eligible_tables"].apply(_status_label)
    board = board.sort_values(
        ["status", "conversion_pct", "target_revenue"],
        ascending=[True, False, False],
    ).reset_index(drop=True)

    # Rank only qualified rows.
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
    if row["status"] == "No recorded shift":
        return 0  # will be treated as N/A in overall average
    if row["status"] == "Building sample":
        return UNQUALIFIED_POINTS
    return RANK_POINTS.get(int(row["rank"]), DEFAULT_RANK_POINTS)


def overall_leaderboard(sales: pd.DataFrame) -> pd.DataFrame:
    """Combine all weekly boards into the £50 overall standings.

    Score = mean(points) over QUALIFIED weeks only. Weeks with 'No recorded shift'
    are treated as N/A and excluded from the denominator. Servers must have
    at least MIN_QUALIFIED_WEEKS to be eligible for the overall prize.
    """
    from .config import MIN_QUALIFIED_WEEKS

    rows: list[dict] = []
    for w in WEEKS:
        board = weekly_leaderboard(sales, w)
        for _, r in board.iterrows():
            rows.append({
                "week": w.number,
                "Employee": r["Employee"],
                "Display": r["Display"],
                "status": r["status"],
                "points": r["points"],
                "conversion_pct": r["conversion_pct"],
            })
    long = pd.DataFrame(rows)
    if long.empty:
        return long

    # Ensure every roster member appears even if never active.
    for name, disp in ELIGIBLE_ROSTER.items():
        if not ((long["Employee"] == name).any()):
            for w in WEEKS:
                long.loc[len(long)] = {
                    "week": w.number, "Employee": name, "Display": disp,
                    "status": "No recorded shift", "points": 0, "conversion_pct": 0,
                }

    qualified = long[long["status"] == "Qualified"]
    agg = (
        qualified.groupby(["Employee", "Display"], as_index=False)
        .agg(qualified_weeks=("week", "nunique"),
             total_points=("points", "sum"),
             avg_points=("points", "mean"),
             avg_conversion_pct=("conversion_pct", "mean"))
    )
    # Add roster members with 0 qualified weeks.
    all_disp = pd.DataFrame(
        [{"Employee": n, "Display": d} for n, d in ELIGIBLE_ROSTER.items()]
    )
    agg = all_disp.merge(agg, on=["Employee", "Display"], how="left").fillna(
        {"qualified_weeks": 0, "total_points": 0, "avg_points": 0, "avg_conversion_pct": 0}
    )
    agg["prize_eligible"] = agg["qualified_weeks"] >= MIN_QUALIFIED_WEEKS
    agg = agg.sort_values(
        ["prize_eligible", "avg_points", "avg_conversion_pct"],
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
