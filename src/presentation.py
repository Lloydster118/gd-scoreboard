"""Presentation only: no scoring, storage or real staff data."""
import base64
from functools import lru_cache
from html import escape
from pathlib import Path

import pandas as pd

ASSETS = Path(__file__).resolve().parents[1] / "assets"
LIGHT = """--bg:#fff7f1;--surface:#fffdf9;--ink:#283c32;--muted:#616451;
--accent:#56623b;--on-accent:#fffdf5;--line:#ded8c9;--peach:#e4cbb8;
--wash:#f1eddf;--notice:#f4ead5;"""
DARK = """--bg:#19231e;--surface:#222e26;--ink:#f6eee4;--muted:#c3c7b4;
--accent:#b9c58e;--on-accent:#202a1c;--line:#455044;--peach:#e4cbb8;
--wash:#2c382d;--notice:#3b3729;"""


def stylesheet(mode="System"):
    tokens = ":root{" + (DARK if mode == "Dark" else LIGHT) + "}"
    if mode == "System":
        tokens += "@media(prefers-color-scheme:dark){:root{" + DARK + "}}"
    return (ASSETS / "theme.css").read_text().replace("/* THEME_TOKENS */", tokens)


@lru_cache(maxsize=1)
def artwork():
    return "data:image/webp;base64," + base64.b64encode(
        (ASSETS / "george-dragon.webp").read_bytes()).decode()


def masthead():
    return f"""<header class="gd-masthead">
<img src="{artwork()}" width="48" height="48" alt="George and the dragon, the pub artwork">
<div><div class="gd-wordmark">George &amp; Dragon</div>
<span class="gd-eyebrow">Marlow · The team scoreboard</span></div></header>"""


def hero(current):
    return f"""<section class="gd-hero" aria-label="Five-week team incentive">
<div class="gd-hero-copy"><span class="gd-eyebrow">Five weeks. Lasting habits.</span>
<h1>A little extra.<br>A lasting habit.</h1>
<p>Week {current.number}: {escape(['Nibbles', 'Starters', 'Sides & upgrades', 'Desserts', 'After dinner'][current.number-1])}.
Every qualifying portion counts.</p></div>
<img src="{artwork()}" alt="" width="200" height="200"></section>
<div class="gd-prizes" aria-label="Competition at a glance">
<div><strong>£10</strong><span>Weekly prize</span></div>
<div><strong>£50</strong><span>Five-week prize</span></div>
<div><strong>15</strong><span>Table opportunities to qualify</span></div></div>"""


def fmt(value, places=1):
    if pd.isna(value):
        return "—"
    return f"{float(value):.{places}f}".rstrip("0").rstrip(".") if places else f"{float(value):.0f}"


def weekly_cards(board):
    rows = []
    maximum = max(1, board.portions_per_100_tables.max()) if not board.empty else 1
    for _, person in board.iterrows():
        rank = fmt(person["rank"], 0)
        rate = person["portions_per_100_tables"]
        width = 0 if pd.isna(rate) else max(0, min(100, float(rate) / maximum * 100))
        status = escape(str(person["status"]))
        if status == "Building sample":
            status = f"{fmt(person['eligible_tables'])} / 15 opportunities"
        rows.append(f"""<div class="gd-row" role="listitem" data-qualified="{str(person['status'] == 'Qualified').lower()}">
<div class="gd-rank">{rank}</div><div class="gd-name">{escape(str(person['Display']))}
<span class="gd-status">{status}</span></div>
<div><span class="gd-score">{fmt(rate)}</span><span class="gd-mobile-label"> /100</span>
<div class="gd-track" aria-hidden="true"><span style="width:{width:.2f}%"></span></div></div>
<div class="gd-number">{fmt(person['target_units'], 0)} <span class="gd-mobile-label">portions</span></div>
<div class="gd-number">{fmt(person['eligible_tables'])} <span class="gd-mobile-label">tables</span></div></div>""")
    return """<div class="gd-board"><div class="gd-board-head" aria-hidden="true">
<span>Rank</span><span>Team member</span><span>Portions / 100 tables</span><span>Portions</span><span>Opportunities</span>
</div><div role="list" aria-label="Weekly leaderboard">""" + "".join(rows) + "</div></div>"


def overall_cards(board):
    rows = []
    # Preserve supplied ordering and show equal scores with equal visual ranks.
    ranks = board.overall_score.rank(method="min", ascending=False)
    for index, person in board.iterrows():
        value = float(person["overall_score"])
        rank = fmt(ranks.loc[index], 0) if person["categories_qualified"] else "—"
        pips = "".join(
            f'<span class="gd-pip"><span style="width:{max(0,min(100,float(person[f"category_{n}"])*5)):.2f}%"></span></span>'
            for n in range(1, 6))
        rows.append(f"""<div class="gd-row" role="listitem"><div class="gd-rank">{rank}</div>
<div class="gd-name">{escape(str(person['Display']))}<span class="gd-status">
{int(person['categories_qualified'])} of 5 categories qualified</span></div>
<div><span class="gd-score">{fmt(value)}</span><span class="gd-status">out of 100</span></div>
<div class="gd-category-cell"><div class="gd-pips" aria-hidden="true">{pips}</div>
<span class="gd-status">Five categories · 20 points each</span></div></div>""")
    return """<div class="gd-board gd-overall"><div class="gd-board-head" aria-hidden="true">
<span>Rank</span><span>Team member</span><span>Overall score</span><span>Category contributions</span></div>
<div role="list" aria-label="Overall leaderboard">""" + "".join(rows) + "</div></div>"


def campaign_path(weeks, today):
    pieces = []
    for week in weeks:
        active = week.start <= today <= week.end
        status = "Weekly focus" if active else "Tracking for £50" if today > week.end else "Coming next"
        pieces.append(f"""<div class="gd-step" data-active="{str(active).lower()}">
<small>WEEK {week.number} · {week.start:%d %b}</small><strong>{escape(week.name)}</strong>
<small>{status}</small></div>""")
    return '<section class="gd-path" aria-label="Five-week campaign">' + "".join(pieces) + "</section>"


def empty_card(title, description):
    return f'<div class="gd-empty"><strong>{escape(title)}</strong><p>{escape(description)}</p></div>'
