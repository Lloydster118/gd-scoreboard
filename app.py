"""G&D account-based incentive scoreboard. Private admin data stays server-side."""
from __future__ import annotations

import copy
import datetime as dt
import hmac
import io
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.config import (
    WEEKS, ELIGIBLE_ROSTER, COMPETITORS, MIN_TABLES_WEEKLY,
    PRIZE_WEEKLY_GBP, PRIZE_OVERALL_GBP, ROSTER_SOURCE,
)
from src.menus import DEFAULT_MENUS, menu_on, validate_menus
from src.roster import with_additions
from src.processing import (
    EXCLUDED_OWNER, load_transactions, score_accounts,
    weekly_leaderboard, overall_leaderboard, category_leaderboard,
)
from src.storage import (
    GitHubStore, LocalStore, StorageError, empty_state, replacement_state,
    weekly_result, finalise_week,
)

st.set_page_config(page_title="G&D Upsell Scoreboard", page_icon="🍽️", layout="wide")
st.markdown("""
<style>
[data-testid="stMainBlockContainer"] {padding-top:2rem;max-width:1200px}
h1 {font-size:1.9rem!important}
@media(max-width:600px){h1{font-size:1.5rem!important}h2{font-size:1.25rem!important}}
.prize-hero {background:linear-gradient(135deg,#1e293b,#0f172a);padding:1.5rem;
border-radius:12px;border:1px solid #334155;margin-bottom:1.5rem}
.prize-hero h2 {color:#fbbf24;margin:0 0 .4rem}
.prize-hero p {color:#cbd5e1;margin:0}
</style>
""", unsafe_allow_html=True)


def secret(name, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def get_store():
    settings = secret("storage", {})
    if settings.get("repo") and settings.get("token"):
        return GitHubStore(settings["repo"], settings["token"]), True
    if settings.get("development_local", False):
        return LocalStore(Path(__file__).parent / "data" / "state.json.gz"), False
    return None, False


def show_board(board):
    if board.empty:
        st.info("No roster configured.")
        return
    view = board.rename(columns={
        "rank": "#", "Display": "Server", "eligible_tables": "Table opportunities",
        "target_units": "Portions", "portions_per_100_tables": "Portions / 100 tables",
        "target_revenue": "Target revenue £", "status": "Status",
        "points": "Ranking points",
    })
    view["#"] = view["#"].map(lambda x: str(int(x)) if pd.notna(x) else "")
    view["Portions / 100 tables"] = view["Portions / 100 tables"].map(
        lambda x: f"{x:.1f}" if pd.notna(x) else "Unavailable")
    st.dataframe(view[["#", "Server", "Table opportunities", "Portions", "Portions / 100 tables",
                       "Target revenue £", "Ranking points", "Status"]].style.format({
                           "Portions": "{:.0f}", "Target revenue £": "£{:.2f}",
                           "Table opportunities": "{:.2f}",
                       }, na_rep="Unavailable"),
                 hide_index=True, use_container_width=True)


def save_state(new_state, message):
    if store is None:
        st.error("Connect private storage before saving.")
        return
    try:
        # Validate the complete configuration and reviewed sales before persisting.
        validate_menus(new_state["menus"])
        candidate_roster, _ = with_additions(ELIGIBLE_ROSTER, COMPETITORS,
                                             new_state.get("roster_additions", []))
        if new_state["csv"]:
            score_accounts(load_transactions(new_state["csv"].encode()),
                           new_state["menus"], new_state["reviews"], candidate_roster)
        store.save(new_state, version)
    except Exception as exc:
        st.error(f"Not saved: {exc}")
        return
    st.session_state["saved_message"] = message
    st.rerun()


st.title("George & Dragon · Upsell Scoreboard")
st.caption("Marlow · Floor team · Five-week incentive")
store, durable = get_store()
state, version = empty_state(), None
load_error = None
if store:
    try:
        state, version = store.load()
    except Exception:
        load_error = "The saved snapshot could not be loaded. Rankings and editing are paused; check private storage."

raw, result = None, None
legacy_path = Path(__file__).parent / "data" / "combined_transactions.csv"
if not load_error:
    try:
        ELIGIBLE_ROSTER, COMPETITORS = with_additions(
            ELIGIBLE_ROSTER, COMPETITORS, state.get("roster_additions", []))
        if state["csv"]:
            raw = load_transactions(state["csv"].encode())
        elif legacy_path.exists():
            raw = load_transactions(legacy_path)
        if raw is not None:
            result = score_accounts(raw, state["menus"], state["reviews"], ELIGIBLE_ROSTER)
    except Exception:
        load_error = "Saved data or review configuration is invalid. Rankings are paused; check the admin configuration."

today = dt.datetime.now(ZoneInfo("Europe/London")).date()
current = next((w for w in WEEKS if w.start <= today <= w.end), WEEKS[0] if today < WEEKS[0].start else WEEKS[-1])
if load_error:
    st.error(load_error)
if not durable:
    st.warning("Permanent private storage is not connected. Do not rely on temporary server files surviving a restart.")
if state["upload"]:
    meta = state["upload"]
    uploaded_at = dt.datetime.fromisoformat(meta["at"]).astimezone(ZoneInfo("Europe/London"))
    st.caption(f"Data coverage: {meta['start']} to {meta['end']} · "
               f"Updated: {uploaded_at:%d %b %Y, %H:%M %Z} · {meta['rows']:,} transaction rows")
elif raw is not None:
    st.warning("Legacy temporary data loaded for preview only. Re-upload the full cumulative export into private storage.")
if result is not None:
    review_count = int(result.accounts["issues"].ne("").sum())
    if review_count:
        st.warning(f"Provisional: {review_count} accounts need admin review. Held or unresolved accounts can change rankings; do not award prizes yet.")
    st.caption("Payments are evidence of activity, not a guarantee that every bill is finally settled.")

tabs = st.tabs(["Leaderboard", "5-Week View", "Rules", "How It Works", "Admin"])
with tabs[0]:
    st.markdown(f"""<div class="prize-hero"><h2>£{PRIZE_WEEKLY_GBP} weekly · £{PRIZE_OVERALL_GBP} overall</h2>
    <p>Qualifying portions per 100 accepted main-course accounts. Every valid portion counts.</p></div>""", unsafe_allow_html=True)
    st.caption("One accepted Account ID contributes one table opportunity per category, shared between "
               "the main-course owner and qualifying-item sellers (0.5 each when two people share). "
               "Drinks/snack-only visits are not assigned by main-course ownership. "
               "Unresolved relevant corrections remain excluded pending review.")
    st.subheader(f"Week {current.number}: {current.name}")
    if today < current.start:
        st.info(f"Unlocks on {current.start:%A %d %B}.")
    elif result is None or load_error:
        st.info("The scoreboard will appear after a valid cumulative upload.")
    else:
        show_board(weekly_result(state, result, current, ELIGIBLE_ROSTER, COMPETITORS))
        st.subheader("Overall standings")
        st.caption("£50 habit-building competition: categories keep tracking from launch through 18 October. "
                   "Each contributes up to 20 points: cumulative category ranking points ÷ 80 × 20. "
                   "Five equally weighted categories, 100 points maximum. Not a sum of frozen weekly results.")
        overall = overall_leaderboard(result, ELIGIBLE_ROSTER, COMPETITORS)
        if overall["provisional"].any():
            st.warning("Overall standings remain provisional while rolling-category accounts need review.")
        st.dataframe(overall.drop(columns=["total_points", "provisional"]).rename(columns={
                         "Display": "Server", "overall_score": "Overall / 100",
                         "categories_qualified": "Categories qualified", "is_competitor": "Competing",
                         **{f"category_{w.number}": f"Category {w.number} / 20" for w in WEEKS}}),
                     hide_index=True, use_container_width=True)
        with st.expander("Ongoing category scores for the £50 prize"):
            for week in WEEKS:
                if today >= week.start:
                    st.subheader(f"{week.name}: ongoing")
                    st.caption(f"Sales from {week.start:%d %b} through 18 Oct. "
                               "At least 15 shared table opportunities required per category.")
                    show_board(category_leaderboard(result, week, ELIGIBLE_ROSTER, COMPETITORS))

with tabs[1]:
    for week in WEEKS:
        st.subheader(f"Week {week.number}: {week.name}")
        st.caption(f"{week.start:%d %b} to {week.end:%d %b}")
        if today < week.start:
            st.info(f"Locked until {week.start:%A %d %B}.")
            continue
        for menu in state["menus"]:
            start, end = dt.date.fromisoformat(menu["start"]), dt.date.fromisoformat(menu["end"])
            if start <= week.end and end >= week.start and str(week.number) in menu["targets"]:
                st.caption(f"{max(start, week.start)} to {min(end, week.end)}: "
                           + ", ".join(menu["targets"][str(week.number)]))
        days = [week.start + dt.timedelta(days=i) for i in range((week.end-week.start).days+1)]
        if any(menu_on(d, state["menus"]) is None or str(week.number) not in menu_on(d, state["menus"])["targets"] for d in days):
            st.warning("Some dates have no confirmed target/menu mapping. Those dates will not be guessed.")
        if result is not None and not load_error:
            frozen = state.get("weekly_results", {}).get(str(week.number))
            if frozen:
                st.success("£10 weekly result frozen. Later sales only affect the separate overall competition.")
            elif today > week.end:
                st.warning("Weekly sales window closed. Awaiting complete data and admin review before the £10 result is frozen.")
            else:
                st.caption("£10 weekly competition open. This category continues towards £50 after the weekly window closes.")
            show_board(weekly_result(state, result, week, ELIGIBLE_ROSTER, COMPETITORS))

with tabs[2]:
    st.subheader("Competition rules")
    st.markdown(f"""
- **Weekly score:** Valid qualifying portions ÷ eligible table accounts assigned to you × 100.
- **Every portion counts:** Three of the same qualifying item earn three portion credits. There is no per-account cap.
- **Credit follows the sale entry:** You retain credit for qualifying portions you enter on someone else's account.
- **Main-course owner:** Ordinary ownership follows the greatest number of main-course portions entered.
  Chateaubriand counts as two main portions for ownership, not double upsell credit.
- **Shared opportunities:** For each category, split one accepted account equally between its main-course owner
  and distinct qualifying-item sellers. Two people receive 0.5 each; three receive one third each.
  The same person acting as owner and seller receives only one share. Portions stay entirely with their seller.
- **Managers:** Keep their share and their item credit but cannot win prizes. Their share is not redistributed.
- **Zero-target accounts:** Still contribute one opportunity to the main-course owner.
- **Unresolved/no-main accounts:** Item credit can remain visible, but no opportunity is invented; results stay provisional.
- **Zero assigned tables:** The rate is unavailable, not zero or infinity; no weekly rank.
- **Eligibility:** At least {MIN_TABLES_WEEKLY} shared table opportunities in the relevant scoring window.
  Below it, results are visible but earn no ranking points.
- **Prizes:** £{PRIZE_WEEKLY_GBP} weekly and £{PRIZE_OVERALL_GBP} overall.
- **Ranking points:** 80, 70, 60, 50, 40, 30, 20, 10 for first through eighth; 10 below eighth if qualified.
- **Weekly £10:** Only the launch week's sales count. After Sunday closes, the administrator freezes the result
  once the complete export and account reviews are ready. Subsequent uploads cannot overwrite a frozen result.
- **Overall £50:** Each category continues from its launch until 18 October. Its cumulative rate determines
  a fresh category rank, independently of the weekly prize. Each category contributes ranking points ÷ 80 × 20,
  giving five equal 20% weights and a maximum of 100 overall. Locked, missing and below-threshold categories earn zero.
  Managers never earn competition points. There is no retrospective credit before a category launches.
- **Ties:** Target-item revenue breaks equal rates. Exact rate-and-revenue ties share a rank and require prize review.
- **Review first:** Missing payment evidence, ownership ties and relevant corrections remain flagged.
  Rankings are provisional until the affected accounts are resolved.
""")
with tabs[3]:
    st.subheader("How the score is calculated")
    st.markdown("""
### Accounts, not table labels
Each Account ID represents a visit. Two sittings at the same physical table remain separate;
multiple order numbers and payments on one account do not create extra tables.

### Menu rules follow the transaction date
The confirmed outgoing menu ends on 23 September. From 24 September, new mappings must be
confirmed before scoring. Main-course ownership and target products both use dated rules.
Unconfirmed later-week menus are not activated.

### Valid sales and review
SF-tagged staff food is excluded. Payment may be recorded by any employee on the same account.
Deposit redemption without ordinary payment evidence needs settlement confirmation.
Relevant voids, corrections and transfers require a reviewed final sales list, rather than
blind subtraction. Package charges and modifiers do not become extra mains.
Confirmed preorders need an owner review; later-week preselected-course eligibility needs explicit confirmation.
Large walk-ins are not excluded merely because they have 12 or more guests.

### Cumulative uploads
Upload the complete export from 14 September through the latest completed reporting period.
It replaces, never appends to, the previous dataset. Genuine identical item rows remain intact.
An identical re-upload changes nothing. Invalid files leave the saved snapshot unchanged.
Shorter corrected replacements require explicit admin confirmation. The app cannot prove
that the export included every transaction, so the supervisor must confirm its scope.

### A contribution rate, not a conversion percentage
Six portions over three assigned tables means 200 portions per 100 tables. Scores above 100
are valid. Cross-table selling adds item credit and shares the accepted account opportunity
with the main-course owner, separately for each category.
Large groups count as one account, so this is table-relative rather than covers-relative.
""")

with tabs[4]:
    st.subheader("Admin")
    admin_pin = str(secret("admin_pin", ""))
    entered = st.text_input("Admin PIN", type="password")
    unlocked = st.session_state.get("admin_authenticated", False)
    if not admin_pin:
        st.error("Admin access is disabled until admin_pin is set in Streamlit Secrets.")
    elif not unlocked:
        if st.button("Unlock admin"):
            until = st.session_state.get("locked_until", 0)
            if time.time() < until:
                st.error("Too many attempts in this session. Wait a minute.")
            elif entered and hmac.compare_digest(entered, admin_pin):
                st.session_state["admin_authenticated"] = True
                st.rerun()
            else:
                attempts = st.session_state.get("pin_attempts", 0) + 1
                st.session_state["pin_attempts"] = attempts
                if attempts % 5 == 0:
                    st.session_state["locked_until"] = time.time() + 60
                st.error("Incorrect PIN.")
    if admin_pin and unlocked:
        if st.button("Lock admin"):
            st.session_state["admin_authenticated"] = False
            st.rerun()
        if st.session_state.get("saved_message"):
            st.success(st.session_state.pop("saved_message"))
        st.caption(f"Roster source: {ROSTER_SOURCE}")
        st.caption(f"Private roster additions: {len(state.get('roster_additions', []))}")
        if load_error:
            st.error("Resolve the storage/configuration error before editing. No replacement can be saved.")
        else:
            if not durable:
                st.info("Configure [storage] repo and token in Streamlit Secrets. Never put a token or transaction file in the public code repo.")
            st.subheader("Replace cumulative data")
            upload = st.file_uploader("Full cumulative Zonal CSV from 14 September", type="csv")
            complete = st.checkbox("I confirm this is the complete cumulative export, not a daily or filtered file.")
            reduction = st.checkbox("This is an intentional corrected replacement that may remove earlier rows/accounts.")
            if st.button("Validate and replace", disabled=upload is None or not complete or store is None):
                try:
                    candidate, changed = replacement_state(state, upload.getvalue(), reduction, today)
                    if changed:
                        save_state(candidate, "Cumulative snapshot saved to private storage.")
                    else:
                        st.info("Identical data already saved; no changes made.")
                except Exception as exc:
                    st.error(f"Upload rejected; previous snapshot unchanged: {exc}")
            if state["csv"]:
                st.download_button("Download private scoring-data backup", state["csv"],
                                   "scoreboard-private-backup.csv", "text/csv")
            if result is not None:
                st.subheader("Freeze a completed £10 weekly result")
                st.caption("Weekly sales stop at Sunday midnight UK time. Freeze only after complete uploads and reviews. "
                           "Frozen results cannot be overwritten; rolling £50 scores remain live.")
                finished = [w for w in WEEKS if today > w.end and
                            str(w.number) not in state.get("weekly_results", {})]
                if finished:
                    selected_week = st.selectbox("Completed week to freeze", [w.number for w in finished])
                    freeze_complete = st.checkbox("I confirm the entire week's export is complete and all account reviews are resolved.")
                    if st.button("Freeze weekly prize result", disabled=not freeze_complete or store is None):
                        try:
                            candidate = finalise_week(state, WEEKS[selected_week - 1], today, freeze_complete,
                                                      ELIGIBLE_ROSTER, COMPETITORS)
                            save_state(candidate, "Weekly £10 result frozen. Overall category tracking continues.")
                        except Exception as exc:
                            st.error(f"Result not frozen: {exc}")
                else:
                    st.info("No completed, unfrozen week is ready for finalisation.")
                st.subheader("Accounts requiring review")
                pending = result.accounts[result.accounts["issues"].ne("")]
                st.dataframe(pending.drop(columns="fingerprint"), hide_index=True, use_container_width=True)
                notes = result.accounts[result.accounts["data_notes"].fillna("").ne("")]
                with st.expander("Informational cover/table notes (not automatic exclusions)"):
                    st.dataframe(notes.drop(columns="fingerprint"), hide_index=True, use_container_width=True)
                with st.expander("Ongoing £50 category review queues"):
                    st.caption("Later corrections or missing mappings can affect an ongoing category even when "
                               "the current £10 weekly category is valid. The same account-review controls apply.")
                    for theme in WEEKS:
                        rolling = result.categories.get(theme.number)
                        if rolling is not None:
                            flagged = rolling.accounts[rolling.accounts["issues"].ne("")]
                            st.caption(f"Category {theme.number}: {theme.name} · {len(flagged)} flagged accounts")
                            if not flagged.empty:
                                st.dataframe(flagged.drop(columns="fingerprint"), hide_index=True, use_container_width=True)
                options = result.accounts["account_id"].tolist()
                if options:
                    account_id = st.selectbox("Account to inspect or correct", options)
                    details = result.accounts[result.accounts["account_id"].eq(account_id)].iloc[0]
                    account = raw[raw["Account ID"].eq(account_id)]
                    st.dataframe(account, hide_index=True, use_container_width=True)
                    previous = state["reviews"].get(str(account_id), {})
                    owner_options = ["Use automatic ownership", EXCLUDED_OWNER] + sorted(set(ELIGIBLE_ROSTER.values()))
                    selected_owner = st.selectbox("Confirmed owner", owner_options, key=f"owner_{account_id}")
                    settled = st.checkbox("I verified settlement despite missing ordinary payment evidence", key=f"settled_{account_id}")
                    preorder = st.checkbox("This is a confirmed preorder", value=bool(previous.get("preorder")), key=f"pre_{account_id}")
                    targets_ok = st.checkbox("For later weeks, I verified which preorder items were genuine extra upsells", key=f"target_{account_id}")
                    excluded = st.checkbox("Exclude this account entirely, including all item credit", key=f"exclude_{account_id}")
                    note = st.text_input("Review evidence / reason (required)", key=f"note_{account_id}")
                    final_text = st.text_area(
                        "Verified final sales JSON (optional; required for relevant corrections/transfers)",
                        value=json.dumps(previous.get("final_sales"), indent=2) if previous.get("final_sales") is not None else "",
                        key=f"final_{account_id}",
                        help='A full replacement list for this account: [{"Date":"2026-09-14","Employee":"exact Zonal name","Description":"exact product","Quantity":1,"Sales Amount":5.0}]. Preserve original sale-entry credit. Use [] only when no valid sales remain.',
                    )
                    if st.button("Save reviewed account", disabled=store is None):
                        try:
                            if not note.strip():
                                raise ValueError("Add an evidence note before saving the review.")
                            review = dict(fingerprint=details["fingerprint"], note=note.strip(),
                                          settlement_confirmed=settled, preorder=preorder,
                                          preorder_targets_confirmed=targets_ok, exclude=excluded,
                                          reviewed_at=dt.datetime.now(dt.timezone.utc).isoformat())
                            if selected_owner != "Use automatic ownership":
                                review["owner"] = selected_owner
                            if final_text.strip():
                                review["final_sales"] = json.loads(final_text)
                            if targets_ok and review.get("final_sales") is None:
                                raise ValueError("Provide verified final sales containing only eligible preorder upsells and valid main evidence.")
                            new = copy.deepcopy(state)
                            new["reviews"][str(account_id)] = review
                            save_state(new, "Account review saved. Any subsequent account changes expire this review.")
                        except Exception as exc:
                            st.error(f"Review not saved: {exc}")
                with st.expander("Unmapped product review catalogue"):
                    st.caption("Not every unmapped product is an error: drinks and modifiers are expected. Review potential missing mains.")
                    st.dataframe(result.unknown_products, hide_index=True, use_container_width=True)
            st.subheader("Effective-dated menu configuration")
            st.caption("Add confirmed new periods without overwriting historical mappings. No overlapping date ranges.")
            st.caption("Every new menu period needs mappings for ALL launched categories, including continuing nibbles "
                       "and starters. Missing category mappings pause that category; they are never guessed.")
            menu_text = st.text_area("Menu JSON", value=json.dumps(state["menus"], indent=2), height=300)
            historical = st.checkbox("I intend to correct historical menu rules and have reviewed the effect on previous scores.")
            if st.button("Save menu mappings", disabled=store is None):
                try:
                    proposed = validate_menus(json.loads(menu_text))
                    if not historical:
                        for old in state["menus"]:
                            if dt.date.fromisoformat(old["start"]) <= today and old not in proposed:
                                raise ValueError("Existing historical rules changed; confirm a deliberate correction first.")
                    new = copy.deepcopy(state)
                    new["menus"] = proposed
                    save_state(new, "Dated menu mappings saved.")
                except Exception as exc:
                    st.error(f"Menus not saved: {exc}")

st.divider()
st.caption("George & Dragon Marlow · Heartwood Collection · Zonal transaction evidence · Questions to Harry")
