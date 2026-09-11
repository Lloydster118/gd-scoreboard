"""
George & Dragon — Upsell Incentive Live Scoreboard
==================================================

A single-page Streamlit dashboard for the 5-week floor-team upselling
incentive. Upload a Zonal Detailed Transaction Report; the app filters
to the eligible floor roster, classifies items into weekly themes,
computes fair table-conversion rates, and renders both the current
week's £10 leaderboard and the rolling £50 overall standings.

Data is processed in-session only. Nothing is persisted server-side.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import streamlit as st

from src.config import (
    WEEKS,
    ELIGIBLE_ROSTER,
    MIN_TABLES_WEEKLY,
    MIN_QUALIFIED_WEEKS,
    PRIZE_WEEKLY_GBP,
    PRIZE_OVERALL_GBP,
    RANK_POINTS,
)
from src.processing import (
    load_transactions,
    clean_sales,
    weekly_leaderboard,
    overall_leaderboard,
    diagnostics,
)

st.set_page_config(
    page_title="G&D Upsell Scoreboard",
    page_icon="🍽️",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .main .block-container {padding-top: 2rem;}
    .kpi {background:#0e1117; padding:1rem 1.25rem; border-radius:12px;
          border:1px solid #262730;}
    .kpi h3 {margin:0; font-size:0.85rem; color:#9ca3af; font-weight:500;}
    .kpi p  {margin:0.25rem 0 0 0; font-size:1.6rem; font-weight:700; color:#fafafa;}
    .rank-1 {color:#f4c542; font-weight:700;}
    .status-qualified {color:#22c55e;}
    .status-building {color:#eab308;}
    .status-none {color:#6b7280;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar — data source & week selector
# ---------------------------------------------------------------------------
st.sidebar.title("🍽️ G&D Scoreboard")
st.sidebar.caption(f"5-week upselling incentive — £{PRIZE_WEEKLY_GBP} weekly / £{PRIZE_OVERALL_GBP} overall")

upload = st.sidebar.file_uploader(
    "Upload Zonal Detailed Transaction Report (.csv)",
    type=["csv"],
    accept_multiple_files=False,
)

use_sample = st.sidebar.toggle("Use pre-loaded pre-promo baseline (dev)", value=False,
                               help="Only works locally when the source CSV is available.")

st.sidebar.markdown("---")
st.sidebar.markdown("**Roster (competition entrants):**")
for zonal, disp in ELIGIBLE_ROSTER.items():
    st.sidebar.markdown(f"- {disp} _(Zonal: `{zonal}`)_")
st.sidebar.markdown("- Paige _(new starter — add Zonal name when active)_")

st.sidebar.markdown("---")
st.sidebar.markdown(
    f"**Rules**\n\n"
    f"- Min {MIN_TABLES_WEEKLY} eligible tables/week to win £{PRIZE_WEEKLY_GBP}.\n"
    f"- Min {MIN_QUALIFIED_WEEKS} qualified weeks to win £{PRIZE_OVERALL_GBP}.\n"
    f"- Absence = N/A, not zero.\n"
    f"- Score = tables that bought target item ÷ eligible tables served."
)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
df_raw = None
if upload is not None:
    df_raw = load_transactions(upload)
elif use_sample:
    p = Path("/home/user/workspace/branched_contexts/a7f3e32e-e844-480e-8908-726ddd7ec370/"
             "attachments/Detailed-Transaction-Report-5-week-pre-promo_"
             "Detailed-Transaction-Report_Detailed-Transaction.csv")
    if p.exists():
        df_raw = load_transactions(p)
    else:
        st.sidebar.warning("Sample CSV not found in this environment.")

if df_raw is None:
    st.title("George & Dragon — Upsell Incentive Scoreboard")
    st.info("👈 Upload the latest Zonal export in the sidebar to see the live scoreboard.")
    st.markdown(
        f"""
        ### How it works

        1. Export the **Detailed Transaction Report** from Zonal covering the campaign period so far.
        2. Drop the CSV into the uploader on the left.
        3. The board recalculates instantly. No data is stored anywhere.

        ### What's measured

        For each of the {len(WEEKS)} weekly themes:

        - Only genuine `Sale` lines for the {len(ELIGIBLE_ROSTER)} eligible floor-team members are counted.
        - Every unique order/table for a server is one **opportunity**.
        - A table counts as a **hit** if it bought one or more of that week's target items.
        - Weekly rank = **conversion % = hits / opportunities**.
        - £{PRIZE_WEEKLY_GBP} weekly winner requires ≥ {MIN_TABLES_WEEKLY} eligible tables.
        - £{PRIZE_OVERALL_GBP} overall winner = highest **average points** across ≥ {MIN_QUALIFIED_WEEKS} qualified weeks.
        - Weeks with no recorded shift are marked **N/A** and excluded from the average — nobody is penalised for approved absence.
        """
    )
    st.stop()

# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------
diag = diagnostics(df_raw)
sales = clean_sales(df_raw)

# ---------------------------------------------------------------------------
# Header + KPI row
# ---------------------------------------------------------------------------
st.title("George & Dragon — Upsell Incentive Scoreboard")
st.caption(
    f"Data range: **{diag['date_min']} → {diag['date_max']}**  ·  "
    f"{diag['rows_total']:,} lines, {diag['sale_rows']:,} sales after cleaning."
)

k1, k2, k3, k4 = st.columns(4)
today = dt.date.today()
current_week = next((w for w in WEEKS if w.start <= today <= w.end), None)

with k1:
    st.markdown(f"""<div class="kpi"><h3>CURRENT WEEK</h3>
    <p>{'Week ' + str(current_week.number) if current_week else 'Off-campaign'}</p></div>""",
    unsafe_allow_html=True)
with k2:
    theme = current_week.name if current_week else "—"
    st.markdown(f"""<div class="kpi"><h3>THEME</h3><p style="font-size:1.1rem">{theme}</p></div>""",
    unsafe_allow_html=True)
with k3:
    st.markdown(f"""<div class="kpi"><h3>WEEKLY PRIZE</h3><p>£{PRIZE_WEEKLY_GBP}</p></div>""",
    unsafe_allow_html=True)
with k4:
    st.markdown(f"""<div class="kpi"><h3>OVERALL PRIZE</h3><p>£{PRIZE_OVERALL_GBP}</p></div>""",
    unsafe_allow_html=True)

st.markdown("")

# ---------------------------------------------------------------------------
# Week selector
# ---------------------------------------------------------------------------
default_index = current_week.number - 1 if current_week else 0
tabs = st.tabs([f"Week {w.number}" for w in WEEKS] + ["🏆 Overall (£50)"])

for idx, w in enumerate(WEEKS):
    with tabs[idx]:
        st.subheader(f"Week {w.number} — {w.name}")
        st.caption(f"{w.start.strftime('%a %d %b')} → {w.end.strftime('%a %d %b')}  ·  "
                   f"Target items: {', '.join(w.items)}")

        board = weekly_leaderboard(sales, w)

        if board.empty:
            st.info("No activity in this week's date range yet.")
            continue

        # KPIs for the week
        total_tables = int(board["eligible_tables"].sum())
        total_hits = int(board["tables_with_target"].sum())
        overall_conv = (total_hits / total_tables * 100) if total_tables else 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Eligible tables served", f"{total_tables:,}")
        c2.metric("Tables with target item", f"{total_hits:,}")
        c3.metric("Team conversion rate", f"{overall_conv:.1f}%")

        # Leaderboard table
        display_board = board[[
            "rank", "Display", "eligible_tables", "tables_with_target",
            "conversion_pct", "target_units", "target_revenue", "status", "points",
        ]].rename(columns={
            "rank": "#",
            "Display": "Server",
            "eligible_tables": "Tables",
            "tables_with_target": "Hits",
            "conversion_pct": "Conversion %",
            "target_units": "Units",
            "target_revenue": "Revenue £",
            "status": "Status",
            "points": "Points",
        })

        def _style_status(v):
            colors = {
                "Qualified": "color:#22c55e;font-weight:600",
                "Building sample": "color:#eab308;font-weight:600",
                "No recorded shift": "color:#6b7280",
            }
            return colors.get(v, "")

        styled = (
            display_board.style
            .format({"Conversion %": "{:.1f}%", "Revenue £": "£{:.2f}", "#": "{:.0f}"},
                    na_rep="—")
            .map(_style_status, subset=["Status"])
            .background_gradient(subset=["Conversion %"], cmap="Greens")
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)

        # Winner call-out
        qualified = board[board["status"] == "Qualified"]
        if not qualified.empty:
            top = qualified.iloc[0]
            st.success(
                f"🏅 **Week {w.number} leader:** {top['Display']} — "
                f"{top['conversion_pct']:.1f}% conversion "
                f"({int(top['tables_with_target'])} of {int(top['eligible_tables'])} tables)."
            )
        else:
            st.warning(
                f"No server has hit the {MIN_TABLES_WEEKLY}-table threshold yet — "
                "leaderboard is provisional."
            )

# ---------------------------------------------------------------------------
# Overall tab
# ---------------------------------------------------------------------------
with tabs[-1]:
    st.subheader(f"Overall standings — £{PRIZE_OVERALL_GBP} programme prize")
    st.caption(
        f"Score = mean weekly points across QUALIFIED weeks. "
        f"Requires ≥ {MIN_QUALIFIED_WEEKS} qualified weeks to win. "
        f"Weekly rank points: {RANK_POINTS}"
    )

    overall = overall_leaderboard(sales)
    if overall.empty:
        st.info("Nothing to rank yet.")
    else:
        show = overall.rename(columns={
            "Display": "Server",
            "qualified_weeks": "Qualified weeks",
            "total_points": "Total points",
            "avg_points": "Avg points",
            "avg_conversion_pct": "Avg conversion %",
            "prize_eligible": f"Eligible for £{PRIZE_OVERALL_GBP}",
        })[["Server", "Qualified weeks", "Total points", "Avg points",
            "Avg conversion %", f"Eligible for £{PRIZE_OVERALL_GBP}"]]

        styled = (
            show.style
            .format({"Avg points": "{:.1f}", "Avg conversion %": "{:.1f}%"})
            .background_gradient(subset=["Avg points"], cmap="Blues")
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)

        eligible = overall[overall["prize_eligible"]]
        if not eligible.empty:
            top = eligible.iloc[0]
            st.success(
                f"🏆 **Provisional overall leader:** {top['Display']} — "
                f"avg {top['avg_points']:.1f} pts across {int(top['qualified_weeks'])} qualified weeks."
            )

# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
with st.expander("🔧 Diagnostics & data quality"):
    st.json(diag, expanded=False)
    if diag["unmapped_employees"]:
        st.warning(
            "The following Employee names appear in the sales data but are NOT in the "
            "eligible roster. If any of them are competing (e.g. Paige's new profile), "
            "add them to `src/config.py → ELIGIBLE_ROSTER`."
        )
        st.write(diag["unmapped_employees"])
    else:
        st.success("All sale employees mapped to the roster.")
