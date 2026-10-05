"""How the £50 overall score moves: week-end snapshots, points grid and person view.

Everything here is derived from the same scored result the leaderboards use, so
nothing is stored separately and nothing can drift from the official numbers.
Snapshots re-rank each rolling category using only sales up to a cut-off date.
They use today's reviews and menus, so a past snapshot shows what the data says
now, not what the site displayed on the day.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from .config import RANK_POINTS, WEEKS
from .processing import weekly_leaderboard

_MAX_POINTS = max(RANK_POINTS.values())
POINTS_PER_CATEGORY = 20.0
SHORT_NAMES = {1: "Nibbles", 2: "Starters", 3: "Sides & upgrades", 4: "Desserts", 5: "After dinner"}


def ordinal(n):
    n = int(n)
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


@dataclass(frozen=True)
class Snapshot:
    label: str
    cutoff: object  # datetime.date
    boards: dict  # category number -> ranked competitor board (qualified or not)


def snapshot_dates(data_end, weeks=WEEKS):
    """Week ends already passed, plus the latest data day when it is mid-week."""
    dates = [(f"End of week {w.number}", w.end) for w in weeks if w.end < data_end]
    dates.append(("Latest", data_end))
    return dates


def build_snapshots(result, roster, competitors, data_end, weeks=WEEKS):
    snapshots = []
    for label, cutoff in snapshot_dates(data_end, weeks):
        boards = {}
        for week in weeks:
            rolling = result.categories.get(week.number)
            if rolling is None or week.start > cutoff:
                continue
            board = weekly_leaderboard(rolling, week, roster, competitors, period_end=cutoff)
            boards[week.number] = board[board["is_competitor"]].reset_index(drop=True)
        if boards:
            snapshots.append(Snapshot(label, cutoff, boards))
    return snapshots


def points_table(snapshot):
    """Per-person category points (out of 20) and overall score at one snapshot."""
    people = {}
    for number, board in snapshot.boards.items():
        for row in board.itertuples():
            entry = people.setdefault(row.Display, {})
            entry[number] = row.points / _MAX_POINTS * POINTS_PER_CATEGORY
    table = pd.DataFrame.from_dict(people, orient="index").reindex(
        columns=[w.number for w in WEEKS]).fillna(0.0)
    table.index.name = "Display"
    table["total"] = table.sum(axis=1)
    return table.sort_values(["total"], ascending=False, kind="stable")


def overall_ranks(table):
    return table["total"].rank(method="min", ascending=False).astype(int)


def grid(snapshots):
    """Latest points grid with movement since the previous snapshot."""
    latest = points_table(snapshots[-1])
    before = points_table(snapshots[-2]) if len(snapshots) > 1 else None
    ranks = overall_ranks(latest)
    rows = []
    for person, row in latest.iterrows():
        out = {"Rank": int(ranks[person]), "Server": person}
        for week in WEEKS:
            if week.number not in snapshots[-1].boards:
                continue
            now = row[week.number]
            prev = before.at[person, week.number] if before is not None and person in before.index else 0.0
            delta = now - prev if before is not None and week.number in snapshots[-2].boards else None
            out[SHORT_NAMES[week.number]] = _with_delta(now, delta)
        total_delta = None
        if before is not None:
            total_delta = row["total"] - (before.at[person, "total"] if person in before.index else 0.0)
        out["Overall / 100"] = _with_delta(row["total"], total_delta)
        prev_rank = None
        if before is not None and person in before.index:
            prev_rank = int(overall_ranks(before)[person])
        out["Place move"] = _rank_move(int(ranks[person]), prev_rank)
        rows.append(out)
    return pd.DataFrame(rows)


def _fmt(value):
    return f"{value:g}"


def _with_delta(value, delta):
    text = _fmt(value)
    if delta is None or abs(delta) < 1e-9:
        return text
    return f"{text} ({'+' if delta > 0 else '−'}{_fmt(abs(delta))})"


def _rank_move(now, before):
    if before is None:
        return "–"
    if now < before:
        return f"▲ {before - now}"
    if now > before:
        return f"▼ {now - before}"
    return "–"


def history(snapshots):
    """Overall score of everyone at each snapshot."""
    frame = pd.DataFrame({s.label + f" ({s.cutoff:%d %b})": points_table(s)["total"] for s in snapshots})
    frame = frame.fillna(0.0)
    latest = frame.iloc[:, -1]
    frame = frame.loc[latest.sort_values(ascending=False, kind="stable").index]
    frame = frame[(frame > 0).any(axis=1)]
    out = frame.map(_fmt).reset_index().rename(columns={"index": "Server", "Display": "Server"})
    return out


def category_path(snapshots, number):
    """Rank and rate of everyone in one category at each snapshot."""
    labels, series = [], {}
    for snap in snapshots:
        board = snap.boards.get(number)
        if board is None:
            continue
        labels.append(f"{snap.label} ({snap.cutoff:%d %b})")
        for row in board.itertuples():
            if row.status == "Qualified":
                cell = f"{ordinal(row.rank)} · {row.portions_per_100_tables:.1f}"
            elif row.eligible_tables:
                cell = f"building ({row.eligible_tables:g} tables)"
            else:
                continue
            series.setdefault(row.Display, {})[labels[-1]] = cell
    frame = pd.DataFrame.from_dict(series, orient="index").reindex(columns=labels).fillna("–")
    last = snapshots[-1].boards.get(number)
    order = [p for p in last["Display"] if p in frame.index] if last is not None else list(frame.index)
    return frame.loc[order].reset_index().rename(columns={"index": "Server"})


def _overtake_need(board, row):
    """Extra portions (same tables) needed to pass the next qualified person up."""
    qualified = board[board["status"].eq("Qualified")]
    above = qualified[qualified["portions_per_100_tables"] > row.portions_per_100_tables + 1e-9]
    if above.empty or not row.eligible_tables:
        return None
    target = above.iloc[-1]
    need = math.floor(target.portions_per_100_tables * row.eligible_tables / 100 - row.target_units + 1e-9) + 1
    return target.Display, max(need, 1)


def person_view(snapshots, result, person):
    """Per-category summary for one person at the latest snapshot."""
    latest, previous = snapshots[-1], snapshots[-2] if len(snapshots) > 1 else None
    rows = []
    for week in WEEKS:
        board = latest.boards.get(week.number)
        if board is None or person not in set(board["Display"]):
            continue
        row = board[board["Display"].eq(person)].iloc[0]
        qualified = row.status == "Qualified"
        prev_rank = prev_rate = None
        if previous is not None and week.number in previous.boards:
            old = previous.boards[week.number]
            old = old[old["Display"].eq(person)]
            if not old.empty and old.iloc[0].status == "Qualified":
                prev_rank, prev_rate = int(old.iloc[0]["rank"]), old.iloc[0].portions_per_100_tables
        need = _overtake_need(board, row) if qualified else None
        if qualified:
            place = ordinal(row["rank"]) + (f" (was {ordinal(prev_rank)})" if prev_rank and prev_rank != row["rank"] else "")
            note = "Leading" if row["rank"] == 1 and need is None else (
                f"{need[1]} more portion{'s' if need[1] != 1 else ''} passes {need[0]}" if need else "")
        else:
            place = "Not yet qualified"
            note = f"{row.eligible_tables:g} of 15 tables" if row.eligible_tables else "No tables yet"
        rows.append({"Category": SHORT_NAMES[week.number], "Place": place,
                     "Points / 20": _fmt(row.points / _MAX_POINTS * POINTS_PER_CATEGORY),
                     "Portions": int(row.target_units), "Tables": _fmt(row.eligible_tables),
                     "Per 100 tables": ("Unavailable" if pd.isna(row.portions_per_100_tables)
                                        else f"{row.portions_per_100_tables:.1f}"
                                        + (f" ({row.portions_per_100_tables - prev_rate:+.1f})" if prev_rate is not None
                                           and abs(row.portions_per_100_tables - prev_rate) >= 0.05 else "")),
                     "Next step": note})
    return pd.DataFrame(rows)


def best_tables(result, person, limit=5):
    """The person's biggest credited single accounts across all categories."""
    found = {}
    accounts = result.accounts.set_index("account_id")
    for week in WEEKS:
        rolling = result.categories.get(week.number)
        if rolling is None:
            continue
        credits = rolling.credits
        credits = credits[credits["Display"].eq(person) & credits["week"].eq(week.number)]
        for aid, group in credits.groupby("account_id"):
            units = int(group["target_units"].sum())
            if aid not in accounts.index:
                continue
            info = accounts.loc[aid]
            found[(aid, week.number)] = {
                "Date": f"{info['Date']:%a %d %b}", "Table": info["table"] or "–",
                "Category": SHORT_NAMES[week.number], "Portions": units, "_date": info["Date"]}
    rows = sorted(found.values(), key=lambda r: (-r["Portions"], r["_date"]))[:limit]
    return pd.DataFrame([{k: v for k, v in r.items() if k != "_date"} for r in rows])
