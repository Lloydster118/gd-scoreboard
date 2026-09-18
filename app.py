"""
George & Dragon — Floor Team Upsell Incentive Scoreboard
=========================================================

Public-facing scoreboard for the 5-week floor-team upselling incentive.
Anyone with the link can view the leaderboard, rules, and how-it-works.
Only the supervisor (with the admin PIN) can upload new Zonal data.

Data is stored server-side as CSV in ./data/ so uploads persist between
visits. Future weeks auto-lock until their Monday.
"""

from __future__ import annotations

import datetime as dt
import io
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from src.config import (
    WEEKS,
    ELIGIBLE_ROSTER,
    MIN_TABLES_WEEKLY,
    PRIZE_WEEKLY_GBP,
    PRIZE_OVERALL_GBP,
    RANK_POINTS,
    ROSTER_SOURCE,
    ROSTER_SECRETS_ERROR,
)
from src.processing import (
    load_transactions,
    clean_sales,
    weekly_leaderboard,
    overall_leaderboard,
    diagnostics,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
COMBINED_CSV = DATA_DIR / "combined_transactions.csv"

# Admin PIN: set via Streamlit secrets in production. Falls back to a local
# default so the app still runs in dev. Change this in .streamlit/secrets.toml
# on Streamlit Cloud: `admin_pin = "your-pin"`.
def _load_admin_pin() -> str:
    """Read admin PIN from Streamlit secrets, defaulting to 'gd2026'.

    Never lets a missing or malformed secrets.toml crash app startup:
    both st.secrets access and .get() can raise StreamlitSecretNotFoundError
    if the whole file failed to parse (e.g. bad TOML in an unrelated section).
    """
    if not hasattr(st, "secrets"):
        return "gd2026"
    try:
        return st.secrets.get("admin_pin", "gd2026")
    except Exception:
        return "gd2026"

ADMIN_PIN = _load_admin_pin()

st.set_page_config(
    page_title="G&D Upsell Scoreboard",
    page_icon="🍽️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .main .block-container {padding-top: 1.5rem; max-width: 1200px;}
    .kpi {background:#0e1117; padding:1rem 1.25rem; border-radius:12px;
          border:1px solid #262730;}
    .kpi h3 {margin:0; font-size:0.75rem; color:#9ca3af; font-weight:600;
             letter-spacing:0.05em; text-transform:uppercase;}
    .kpi p  {margin:0.25rem 0 0 0; font-size:1.6rem; font-weight:700; color:#fafafa;}
    .locked-card {background:#0e1117; padding:1.5rem; border-radius:12px;
                  border:1px dashed #374151; text-align:center; color:#6b7280;}
    .rule-card {background:#0e1117; padding:1.25rem 1.5rem; border-radius:12px;
                border:1px solid #262730; margin-bottom:0.75rem;}
    .rule-card h4 {margin:0 0 0.5rem 0; color:#fafafa;}
    .rule-card p {margin:0; color:#9ca3af; font-size:0.95rem;}
    .prize-hero {background:linear-gradient(135deg,#1e293b,#0f172a);
                 padding:2rem; border-radius:16px; border:1px solid #334155;
                 text-align:center;}
    .prize-hero h2 {margin:0 0 0.5rem 0; color:#fbbf24;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_persisted_data() -> pd.DataFrame | None:
    """Load the combined CSV that survives across visits."""
    if not COMBINED_CSV.exists():
        return None
    try:
        return load_transactions(COMBINED_CSV)
    except Exception:
        return None


def append_upload(new_df_raw: pd.DataFrame) -> pd.DataFrame:
    """Merge a new upload into the persisted store. Dedupes on all columns."""
    if COMBINED_CSV.exists():
        existing = pd.read_csv(COMBINED_CSV, low_memory=False)
        combined = pd.concat([existing, new_df_raw], ignore_index=True)
    else:
        combined = new_df_raw.copy()
    # Dedupe: identical rows (same order line, same date, same everything) collapse.
    combined = combined.drop_duplicates()
    combined.to_csv(COMBINED_CSV, index=False)
    return combined


def week_status(week, today: dt.date) -> str:
    """One of: 'locked' (future), 'live' (current), 'closed' (past)."""
    if today < week.start:
        return "locked"
    if today > week.end:
        return "closed"
    return "live"


def current_or_next_week(today: dt.date):
    for w in WEEKS:
        if w.start <= today <= w.end:
            return w
    # If we're before the campaign, return week 1; if after, return last week.
    if today < WEEKS[0].start:
        return WEEKS[0]
    return WEEKS[-1]


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("🍽️ George & Dragon — Upsell Scoreboard")
st.caption("Marlow · Floor Team · 5-Week Upsell Incentive")

# ---------------------------------------------------------------------------
# Navigation (top-level tabs, no sidebar for viewers)
# ---------------------------------------------------------------------------
tab_leaderboard, tab_weeks, tab_rules, tab_how, tab_admin = st.tabs([
    "🏆 Leaderboard",
    "📅 5-Week View",
    "📋 Rules",
    "🧮 How It Works",
    "🔒 Admin",
])

# Load persistent data once.
df_raw = load_persisted_data()
sales = clean_sales(df_raw) if df_raw is not None else None
today = dt.date.today()
current_week = current_or_next_week(today)

# ---------------------------------------------------------------------------
# TAB 1: Leaderboard (current week + overall)
# ---------------------------------------------------------------------------
with tab_leaderboard:
    # Prize hero
    st.markdown(
        f"""
        <div class="prize-hero">
            <h2>£{PRIZE_WEEKLY_GBP} weekly · £{PRIZE_OVERALL_GBP} overall</h2>
            <p style="color:#cbd5e1; margin:0;">
                Highest table-conversion rate wins. Fair. Transparent. Live.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("")

    if sales is None or sales.empty:
        st.info(
            "The scoreboard will appear here once the first Zonal upload lands. "
            "Ask Harry when the first drop is going in."
        )
    else:
        # KPI row
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            week_label = f"Week {current_week.number}"
            if today < WEEKS[0].start:
                week_label = "Campaign starts " + WEEKS[0].start.strftime("%d %b")
            elif today > WEEKS[-1].end:
                week_label = "Campaign closed"
            st.markdown(
                f'<div class="kpi"><h3>Now</h3><p>{week_label}</p></div>',
                unsafe_allow_html=True,
            )
        with k2:
            st.markdown(
                f'<div class="kpi"><h3>This week\'s theme</h3>'
                f'<p style="font-size:1.05rem">{current_week.name}</p></div>',
                unsafe_allow_html=True,
            )
        with k3:
            st.markdown(
                f'<div class="kpi"><h3>Weekly prize</h3><p>£{PRIZE_WEEKLY_GBP}</p></div>',
                unsafe_allow_html=True,
            )
        with k4:
            st.markdown(
                f'<div class="kpi"><h3>Overall prize</h3><p>£{PRIZE_OVERALL_GBP}</p></div>',
                unsafe_allow_html=True,
            )

        st.markdown("---")

        # This week's leaderboard (only if week is live or closed, not locked)
        status = week_status(current_week, today)
        st.subheader(f"This week — {current_week.name}")

        if status == "locked":
            st.info(f"Week {current_week.number} unlocks on {current_week.start.strftime('%A %d %B')}.")
        else:
            board = weekly_leaderboard(sales, current_week)
            if board.empty:
                st.info("No activity recorded in this week's data yet.")
            else:
                display_board = board[[
                    "rank", "Display", "eligible_tables", "tables_with_target",
                    "conversion_pct", "target_revenue", "status", "is_competitor",
                ]].rename(columns={
                    "rank": "#",
                    "Display": "Server",
                    "eligible_tables": "Tables",
                    "tables_with_target": "Hits",
                    "conversion_pct": "Conversion %",
                    "target_revenue": "Revenue £",
                    "status": "Status",
                })

                def _grey_observers(row):
                    # Managers (observers) are shown for their own visibility
                    # but greyed out so the competition list stays visually
                    # dominant.
                    if not row["is_competitor"]:
                        return ["color: #888; font-style: italic"] * len(row)
                    return [""] * len(row)

                styled = (
                    display_board.style
                    .apply(_grey_observers, axis=1)
                    .format({"Conversion %": "{:.1f}%", "Revenue £": "£{:.2f}",
                             "#": "{:.0f}"}, na_rep="—")
                    .background_gradient(
                        subset=pd.IndexSlice[display_board["is_competitor"], "Conversion %"],
                        cmap="Greens",
                    )
                )
                # Hide the is_competitor helper column from the rendered view.
                st.dataframe(
                    styled,
                    use_container_width=True,
                    hide_index=True,
                    column_config={"is_competitor": None},
                )

                comp_board = board[board["is_competitor"]]
                qualified = comp_board[comp_board["status"] == "Qualified"]
                if not qualified.empty:
                    top = qualified.iloc[0]
                    st.success(
                        f"🥇 Leading this week: **{top['Display']}** — "
                        f"{top['conversion_pct']:.1f}% conversion "
                        f"({int(top['tables_with_target'])} of {int(top['eligible_tables'])} tables)."
                    )
                else:
                    st.warning(
                        f"No competitor has hit the {MIN_TABLES_WEEKLY}-table threshold yet — "
                        "leaderboard is provisional."
                    )

        st.markdown("---")

        # Overall standings
        st.subheader(f"Overall standings — £{PRIZE_OVERALL_GBP} campaign prize")
        st.caption(
            "Average points per week across the 5-week campaign. "
            "Missed weeks count as zero. No minimum-weeks gate."
        )
        overall = overall_leaderboard(sales)
        if overall.empty:
            st.info("Standings will populate as weeks close.")
        else:
            show = overall.rename(columns={
                "Display": "Server",
                "qualified_weeks": "Qualified weeks",
                "weeks_worked": "Weeks worked",
                "total_points": "Total pts",
                "avg_points": "Avg pts / week",
                "avg_conversion_pct": "Avg conversion %",
            })[["Server", "Weeks worked", "Qualified weeks",
                "Total pts", "Avg pts / week", "Avg conversion %",
                "is_competitor"]]

            def _grey_observers_overall(row):
                if not row["is_competitor"]:
                    return ["color: #888; font-style: italic"] * len(row)
                return [""] * len(row)

            styled = (
                show.style
                .apply(_grey_observers_overall, axis=1)
                .format({"Avg pts / week": "{:.1f}", "Avg conversion %": "{:.1f}%",
                         "Total pts": "{:.0f}"})
                .background_gradient(
                    subset=pd.IndexSlice[show["is_competitor"], "Avg pts / week"],
                    cmap="Blues",
                )
            )
            st.dataframe(
                styled,
                use_container_width=True,
                hide_index=True,
                column_config={"is_competitor": None},
            )

            comp_overall = overall[overall["is_competitor"]]
            if not comp_overall.empty:
                top = comp_overall.iloc[0]
                st.success(
                    f"🏆 Provisional overall leader: **{top['Display']}** — "
                    f"avg {top['avg_points']:.1f} pts/week across the campaign."
                )

# ---------------------------------------------------------------------------
# TAB 2: 5-Week View (with locked future weeks)
# ---------------------------------------------------------------------------
with tab_weeks:
    st.subheader("The 5-week campaign at a glance")
    st.caption("Each week unlocks on its Monday. Weekly winner = highest conversion % (min 15 tables).")

    for w in WEEKS:
        status = week_status(w, today)

        with st.container():
            cols = st.columns([1, 4, 1])
            with cols[0]:
                icon = {"locked": "🔒", "live": "🟢", "closed": "✅"}[status]
                st.markdown(f"### {icon} Week {w.number}")
                st.caption(f"{w.start.strftime('%a %d %b')} → {w.end.strftime('%a %d %b')}")
            with cols[1]:
                st.markdown(f"**{w.name}**")
                if status == "locked":
                    st.markdown(
                        f'<div class="locked-card">Unlocks {w.start.strftime("%A %d %B")} — items revealed on the day.</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("Target items: " + ", ".join(w.items))
            with cols[2]:
                if status == "locked":
                    st.markdown("`Locked`")
                elif status == "live":
                    st.markdown("**Live now**")
                else:
                    st.markdown("Closed")

            # Board only if unlocked and we have data
            if status != "locked" and sales is not None and not sales.empty:
                board = weekly_leaderboard(sales, w)
                if not board.empty:
                    # Competitors only in the 5-week compact view. Observers
                    # (managers) live on the main Leaderboard tab, not here.
                    comp_only = board[board["is_competitor"]]
                    top3 = comp_only.head(3)[["rank", "Display", "conversion_pct",
                                              "tables_with_target", "eligible_tables", "status"]]
                    top3 = top3.rename(columns={
                        "rank": "#", "Display": "Server",
                        "conversion_pct": "Conv %",
                        "tables_with_target": "Hits",
                        "eligible_tables": "Tables",
                        "status": "Status",
                    })
                    st.dataframe(
                        top3.style.format({"Conv %": "{:.1f}%", "#": "{:.0f}"}, na_rep="—"),
                        use_container_width=True, hide_index=True,
                    )
            st.markdown("---")

# ---------------------------------------------------------------------------
# TAB 3: Rules
# ---------------------------------------------------------------------------
with tab_rules:
    st.subheader("The rules — read once, ask if unclear")

    st.markdown(
        f"""
        <div class="rule-card">
            <h4>£{PRIZE_WEEKLY_GBP} weekly prize</h4>
            <p>Awarded to the server with the <b>highest table-conversion rate</b> in a given week.
            Minimum <b>{MIN_TABLES_WEEKLY} eligible tables</b> served that week to qualify —
            this stops one lucky big-spend table skewing a small sample.</p>
        </div>
        <div class="rule-card">
            <h4>£{PRIZE_OVERALL_GBP} overall prize</h4>
            <p>Awarded at the end of the 5 weeks to the server with the <b>highest average points per week</b>
            across the whole campaign. No minimum-weeks requirement — whatever you work, you work.
            Missed or below-threshold weeks count as <b>0 points</b> in the average, not N/A.</p>
        </div>
        <div class="rule-card">
            <h4>What counts as a "hit"</h4>
            <p>Each week has a theme (nibbles, starters, sides, desserts, coffees & digestives).
            A table counts as a hit if it orders <b>one or more</b> of that week's target items.
            Two hits on one table still counts as one — we're measuring whether you <b>created the opportunity</b>,
            not upselling volume per table.</p>
        </div>
        <div class="rule-card">
            <h4>Conversion rate = fairness</h4>
            <p>Conversion % = (tables with a hit) ÷ (eligible tables you served) × 100.
            A part-timer with 20 tables and 8 hits (40%) beats a full-timer with 60 tables and 18 hits (30%).
            Time on the floor doesn't win — technique does.</p>
        </div>
        <div class="rule-card">
            <h4>What's excluded</h4>
            <p>Voids, wastage, staff meals, payments-only lines, zero-cover orders (breakfast/rooms), and
            any employee not on the competition roster. Only genuine sales to genuine restaurant covers count.</p>
        </div>
        <div class="rule-card">
            <h4>Weekly ranking points (feeds the overall)</h4>
            <p>1st = 80 · 2nd = 70 · 3rd = 60 · 4th = 50 · 5th = 40 · 6th = 30 · 7th = 20 · 8th = 10.
            Qualified but below 8th = 10 pts. Below the {MIN_TABLES_WEEKLY}-table threshold or absent = 0.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# TAB 4: How It Works (deeper breakdown)
# ---------------------------------------------------------------------------
with tab_how:
    st.subheader("How the scoreboard is calculated")
    st.markdown(
        """
        ### 1. The data source
        All numbers come from the **Zonal Detailed Transaction Report** — the same data
        that runs the till. No manual counting, no self-reporting, no arguing with the
        board over "I did more than that".

        Harry uploads a fresh export as often as possible (daily or weekly). Each upload
        is merged into the running store, with duplicates automatically stripped.

        ### 2. Cleaning the data
        Every raw upload gets filtered down to:
        - **Type = "Sale"** only (no voids, no wastage, no payment-only lines)
        - **Employees on the competition roster** only
        - **Covers > 0** only (excludes breakfast, rooms, functions)

        ### 3. Building "table opportunities"
        Zonal has one row per menu-item line. We collapse that to one row per unique
        `(Order No, Employee)` pair. That's a **table opportunity** — one chance to upsell.

        ### 4. Counting hits
        For each week's theme, we look at which table opportunities contain **at least one**
        of that week's target items. A table with three Prawn Cocktails = **one hit**, not three.

        ### 5. Ranking
        ```
        conversion % = (tables with ≥1 target item) / (eligible tables) × 100
        ```
        The board sorts on conversion %. Ties broken by target-item revenue.

        ### 6. Weekly prize
        Highest conversion % **among qualified servers** (≥15 eligible tables) wins the £10.
        Below 15 tables = "Building sample" — visible on the board but not prize-eligible for that week.

        ### 7. Overall prize
        Each weekly rank gives points (1st=80 down to 8th=10). Points are summed across
        all 5 weeks and divided by 5. Highest average wins the £50. Absence or below-threshold
        counts as 0 — no exceptions, no adjustments.

        ### 8. Why it's honest
        - **Sample floor** stops a lucky Tuesday winning
        - **Table-level conversion** stops volume gaming
        - **Same data everyone sees** — Zonal, not Harry's judgement
        - **Weeks auto-unlock** so nobody sees the board before it's fair to publish
        """
    )

    st.markdown("---")
    st.caption(
        "Questions or a number that looks wrong? Grab Harry on shift — the raw data is "
        "in the Zonal report and the calculation is above. Nothing hidden."
    )

# ---------------------------------------------------------------------------
# TAB 5: Admin (PIN-gated — CSV upload)
# ---------------------------------------------------------------------------
with tab_admin:
    st.subheader("Admin — upload new Zonal data")
    st.caption("PIN-protected. Only Harry uploads. Everyone else, back to the leaderboard.")

    pin_input = st.text_input("Admin PIN", type="password", key="admin_pin_input")

    if pin_input == "":
        st.info("Enter the admin PIN to unlock upload.")
    elif pin_input != ADMIN_PIN:
        st.error("Wrong PIN.")
    else:
        st.success("Admin unlocked.")

        st.markdown("### Upload Zonal Detailed Transaction Report")
        st.caption(
            "Same CSV format as always. Drop the day's or the week's export — "
            "duplicates are stripped automatically. Multiple uploads accumulate."
        )
        upload = st.file_uploader(
            "Zonal export (.csv)",
            type=["csv"],
            accept_multiple_files=False,
        )
        if upload is not None:
            try:
                new_raw = pd.read_csv(upload, low_memory=False)
                combined = append_upload(new_raw)
                st.success(
                    f"Merged. Combined store now has {len(combined):,} rows across "
                    f"{combined['Date'].nunique() if 'Date' in combined else '?'} unique dates."
                )
                st.info("Refresh the Leaderboard tab to see the new numbers.")
            except Exception as e:
                st.error(f"Upload failed: {e}")

        st.markdown("---")
        st.markdown("### Roster source")
        st.write(f"Loaded from: **{ROSTER_SOURCE}**  \u2014  {len(ELIGIBLE_ROSTER)} names")
        if ROSTER_SECRETS_ERROR:
            st.error(f"Streamlit Secrets error: {ROSTER_SECRETS_ERROR}")
        with st.expander("Show loaded roster (Zonal name \u2192 display name)"):
            st.json(dict(ELIGIBLE_ROSTER))

        st.markdown("---")
        st.markdown("### Current data store")
        if sales is None or df_raw is None:
            st.info("No data uploaded yet.")
        else:
            diag = diagnostics(df_raw)
            c1, c2, c3 = st.columns(3)
            c1.metric("Total rows", f"{diag['rows_total']:,}")
            c2.metric("Sale rows", f"{diag['sale_rows']:,}")
            c3.metric("Date range",
                      f"{diag['date_min']} → {diag['date_max']}"
                      if diag['date_min'] else "—")

            if diag["unmapped_employees"]:
                st.warning(
                    "Sale employees NOT in the eligible roster. Add them to "
                    "`src/config.py`, `src/roster_local.py`, or Streamlit Cloud "
                    "Secrets (`[[roster]]` array-of-tables) if they should be on the board:"
                )
                st.write(diag["unmapped_employees"])
            else:
                st.success("All sale employees mapped to the roster.")

        st.markdown("---")
        st.markdown("### Danger zone")
        if st.button("🗑️  Reset all uploaded data", type="secondary"):
            if COMBINED_CSV.exists():
                COMBINED_CSV.unlink()
                st.success("Store wiped. Refresh to confirm.")
            else:
                st.info("Nothing to wipe.")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown("---")
st.caption(
    "George & Dragon Marlow · Heartwood Collection · "
    "5-week upsell campaign · Numbers from Zonal · Questions to Harry"
)
