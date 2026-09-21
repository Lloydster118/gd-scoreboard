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
from src.presentation import (
    stylesheet, masthead, hero, weekly_cards, overall_cards, campaign_path, empty_card,
)

st.set_page_config(page_title="G&D · Team Scoreboard",
                   page_icon=str(Path(__file__).parent / "assets" / "favicon.png"), layout="wide")
with st.container(key="brandbar"):
    brand, appearance = st.columns([4, 1], vertical_alignment="center")
    with brand:
        st.markdown(masthead(), unsafe_allow_html=True)
    with appearance:
        theme = st.selectbox("Appearance", ["System", "Light", "Dark"],
                             key="appearance_mode", label_visibility="collapsed")
st.markdown("<style>" + stylesheet(theme) + "</style>", unsafe_allow_html=True)


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
    competing = board[board["status"].ne("Not competing")]
    if not competing.empty:
        st.markdown(weekly_cards(competing), unsafe_allow_html=True)
    view = board.rename(columns={
        "rank": "#", "Display": "Server", "eligible_tables": "Table opportunities",
        "target_units": "Portions", "portions_per_100_tables": "Portions / 100 tables",
        "target_revenue": "Target revenue £", "status": "Status",
        "points": "Ranking points",
    })
    view["#"] = view["#"].map(lambda x: str(int(x)) if pd.notna(x) else "")
    view["Portions / 100 tables"] = view["Portions / 100 tables"].map(
        lambda x: f"{x:.1f}" if pd.notna(x) else "Unavailable")
    with st.expander("Full table, managers & CSV export"):
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
st.markdown(hero(current), unsafe_allow_html=True)
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

tabs = st.tabs(["Leaderboard", "5-Week View", "Rules", "How It Works", "Admin"])
with tabs[0]:
    st.subheader("The weekly leaderboard")
    available = [w for w in WEEKS if w.start <= today] or [WEEKS[0]]
    # Show the latest week with data, rather than an unexplained zero board on Monday.
    data_day = raw["Date"].max() if raw is not None else today
    default_week = next((w for w in available if w.start <= data_day <= w.end), available[-1])
    selected_number = st.selectbox("Prize week", [w.number for w in available],
                                  index=available.index(default_week),
                                  format_func=lambda n: f"Week {n} · {['Nibbles', 'Starters', 'Sides & upgrades', 'Desserts', 'After dinner'][n-1]} · {WEEKS[n-1].start:%d %b} to {WEEKS[n-1].end:%d %b}",
                                  key="leaderboard_week")
    selected = WEEKS[selected_number - 1]
    st.caption("Ranked by qualifying portions per 100 shared table opportunities. "
               "Minimum 15 opportunities. Portion credit stays with the seller.")
    if today < selected.start:
        st.info(f"Unlocks on {selected.start:%A %d %B}.")
    elif result is None or load_error:
        st.markdown(empty_card("Ready for the first service",
                               "The scoreboard will appear after a valid cumulative upload. "
                               "An administrator can add the export in Admin."), unsafe_allow_html=True)
    else:
        if state.get("weekly_results", {}).get(str(selected.number)):
            st.success("£10 weekly result frozen. Later sales only affect the overall competition.")
        elif today > selected.end:
            st.caption("Sales window closed · awaiting complete data and review, not yet frozen.")
        if not raw["Date"].between(selected.start, selected.end).any():
            st.markdown(empty_card("A new week is ready",
                                   "No transactions have been uploaded for this prize week yet. "
                                   "Earlier categories continue contributing to the £50 prize."),
                        unsafe_allow_html=True)
        else:
            show_board(weekly_result(state, result, selected, ELIGIBLE_ROSTER, COMPETITORS))
        st.subheader("The bigger picture")
        st.caption("£50 overall · Nibbles and every launched category keep tracking through 18 October. "
                   "Five equally weighted categories contribute up to 20 points each, for a score out of 100.")
        overall = overall_leaderboard(result, ELIGIBLE_ROSTER, COMPETITORS)
        if overall["provisional"].any():
            st.warning("Overall standings remain provisional while rolling-category accounts need review.")
        st.markdown(overall_cards(overall[overall["is_competitor"]]), unsafe_allow_html=True)
        with st.expander("Overall breakdown & CSV export"):
            st.dataframe(overall.drop(columns=["total_points", "provisional"]).rename(columns={
                             "Display": "Server", "overall_score": "Overall / 100",
                             "categories_qualified": "Categories qualified", "is_competitor": "Competing",
                             **{f"category_{w.number}": f"{w.name} / 20" for w in WEEKS}}),
                         hide_index=True, use_container_width=True)
        with st.expander("Ongoing category scores for the £50 prize"):
            for week in WEEKS:
                if today >= week.start:
                    st.subheader(f"{week.name}: ongoing")
                    st.caption(f"Sales from {week.start:%d %b} through 18 Oct. "
                               "At least 15 shared table opportunities required per category.")
                    show_board(category_leaderboard(result, week, ELIGIBLE_ROSTER, COMPETITORS))

with tabs[1]:
    st.subheader("Five weeks. One growing habit.")
    st.markdown(campaign_path(WEEKS, today), unsafe_allow_html=True)
    st.caption("Each weekly £10 result is separate. Every launched category continues counting "
               "towards the £50 overall prize until 18 October.")
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
- **Additional courses:** Starters count alongside a recognised main-course meal. Desserts count alongside a main,
  whether they are the second course or the third after a starter. Standalone starters/desserts do not qualify.
- **Package guests count too:** Qualifying courses actually ordered count, including zero-priced or included
  resident-package courses. A package charge or allowance alone earns no portion credit.
- **Reward the outcome:** Orders count whether the guest was prompted or already intended to order them.
  There is no persuasion test or team-average adjustment to prize scores.
- **Credit follows the sale entry:** You retain credit for qualifying portions you enter on someone else's account.
- **Main-course owner:** Ordinary ownership follows the greatest number of main-course portions entered.
  Chateaubriand counts as two main portions for ownership, not double upsell credit.
- **Shared opportunities:** For each category, split one accepted account equally between its main-course owner
  and distinct qualifying-item sellers. Two people receive 0.5 each; three receive one third each.
  The same person acting as owner and seller receives only one share. Portions stay entirely with their seller.
- **Managers:** Keep their share and their item credit but cannot win prizes. Their share is not redistributed.
- **Zero-target accounts:** Still contribute one opportunity to the main-course owner.
- **Unresolved/no-main accounts:** No opportunity is invented. Valid nibble credit can remain without a main;
  starter/dessert credit requires recognised main-course evidence on the same account.
  Unresolved accounts keep results provisional; reviewed non-dining accounts do not.
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

### Building the additional-course habit
Starter portions qualify with a recognised main-course meal on the same account.
Dessert portions qualify with a main, with or without a starter: both two-course and three-course meals count.
This is account-level evidence, not a claim that each individual diner ordered both courses.
An actually ordered qualifying package course counts even when its item price is zero.
Package allowances alone, standalone starters/desserts and staff food do not earn this credit.
Nibbles keep their existing separate rules. Whether the guest needed persuading is not measured.
Split bills or transferred courses without main-course evidence on the same account need review rather than guessing.

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
                    selected_owner = st.selectbox("Confirmed owner", owner_options,
                                                  index=owner_options.index(previous.get("owner", "Use automatic ownership")),
                                                  key=f"owner_{account_id}")
                    settled = st.checkbox("I verified settlement despite missing ordinary payment evidence",
                                          value=bool(previous.get("settlement_confirmed")), key=f"settled_{account_id}")
                    preorder = st.checkbox("This is a confirmed preorder", value=bool(previous.get("preorder")), key=f"pre_{account_id}")
                    targets_ok = st.checkbox("For later weeks, I verified which preorder items were genuine extra upsells",
                                             value=bool(previous.get("preorder_targets_confirmed")), key=f"target_{account_id}")
                    excluded = st.checkbox("Exclude this account entirely, including all item credit",
                                           value=bool(previous.get("exclude")), key=f"exclude_{account_id}")
                    no_main = st.checkbox("Confirmed non-main account: retain valid item credit, assign no table opportunity",
                                          value=bool(previous.get("no_main_confirmed")), key=f"no_main_{account_id}")
                    note = st.text_input("Review evidence / reason (required)", value=previous.get("note", ""), key=f"note_{account_id}")
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
                                          preorder_targets_confirmed=targets_ok, exclude=excluded, no_main_confirmed=no_main,
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

st.markdown('<footer class="gd-footer"><span>George &amp; Dragon · Marlow · Heartwood Collection</span>'
            '<span>Five weeks. Lasting habits. · Questions to Harry</span></footer>',
            unsafe_allow_html=True)
