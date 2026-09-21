import copy
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from src.config import WEEKS
from src.menus import DEFAULT_MENUS, STARTERS, is_staff_food, validate_menus
from src.processing import (
    EXCLUDED_OWNER, load_transactions, score_accounts, weekly_leaderboard,
    overall_leaderboard, validate_replacement, fingerprint,
)
from src.storage import (
    GitHubStore, LocalStore, StorageError, empty_state, replacement_state, encode, decode,
)

ROSTER = {"A": "Alpha", "A alias": "Alpha", "B": "Beta", "M": "Manager"}
COMPETITORS = {"Alpha", "Beta"}


def row(account="0001", employee="A", item="Burger & Fries", qty=1, day="14/09/2026",
        kind="Sale", covers=2, table="203", amount=15, payment=0):
    return {"Date": day, "Time": "12:00:00", "Order No": "00001", "Account ID": account,
            "Type": kind, "Description": item, "Employee": employee, "Table": table,
            "Covers": covers, "Quantity": qty, "Sales Amount": amount, "Payment Amount": payment}


def paid(account="0001", **kwargs):
    return row(account=account, employee="M", kind="Payment", item="CardPay",
               qty=0, amount=0, payment=30, **kwargs)


def frame(rows):
    return load_transactions(pd.DataFrame(rows).to_csv(index=False).encode())


def score(rows, **kwargs):
    return score_accounts(frame(rows), roster=ROSTER, **kwargs)


def board(result, week=WEEKS[0]):
    return weekly_leaderboard(result, week, ROSTER, COMPETITORS).set_index("Display")


class ScoringTests(unittest.TestCase):
    def test_separate_sittings_same_table(self):
        r = score([row("01"), paid("01"), row("02"), paid("02")])
        self.assertEqual(board(r).loc["Alpha", "eligible_tables"], 2)
        self.assertEqual(len(r.accounts), 2)

    def test_multiple_orders_and_payments_one_account(self):
        a = row(qty=1); b = row(item="Scotch Egg", qty=3); b["Order No"] = "999"
        r = score([a, b, paid(), paid()])
        self.assertEqual(board(r).loc["Alpha", "eligible_tables"], 1)
        self.assertEqual(board(r).loc["Alpha", "target_units"], 3)
        self.assertEqual(board(r).loc["Alpha", "portions_per_100_tables"], 300)

    def test_identical_item_rows_are_not_deduplicated(self):
        snack = row(item="Scotch Egg")
        r = score([row(), snack, snack, paid()])
        self.assertEqual(board(r).loc["Alpha", "target_units"], 2)

    def test_cross_table_credit_and_alias(self):
        r = score([row(employee="B"), row(employee="A alias", item="Olives Rustica", qty=2), paid()])
        b = board(r)
        self.assertEqual(b.loc["Beta", "eligible_tables"], .5)
        self.assertEqual(b.loc["Alpha", "target_units"], 2)
        self.assertEqual(b.loc["Alpha", "eligible_tables"], .5)
        self.assertEqual(b.loc["Alpha", "portions_per_100_tables"], 400)
        self.assertEqual(b.loc["Alpha", "status"], "Building sample")
        self.assertEqual(b.loc["Alpha", "points"], 0)

    def test_manager_owned_keeps_competitor_credit(self):
        r = score([row(employee="M"), row(item="Olives Rustica", qty=4), paid()])
        self.assertEqual(board(r).loc["Alpha", "eligible_tables"], .5)
        self.assertEqual(board(r).loc["Alpha", "target_units"], 4)
        self.assertEqual(board(r).loc["Manager", "status"], "Not competing")
        self.assertEqual(board(r).loc["Manager", "points"], 0)
        self.assertEqual(board(r).loc["Manager", "eligible_tables"], .5)

    def test_zero_target_account_in_denominator(self):
        self.assertEqual(board(score([row(), paid()])).loc["Alpha", "eligible_tables"], 1)
        self.assertEqual(board(score([row(), paid()])).loc["Alpha", "target_units"], 0)

    def test_majority_main_portions(self):
        r = score([row(employee="A", qty=2, covers=3), row(employee="B", covers=3), paid(covers=3)])
        self.assertEqual(r.accounts.iloc[0]["owner"], "Alpha")

    def test_chateaubriand_weight_two(self):
        r = score([row(item="Chateaubriand", covers=3), row(employee="B", covers=3), paid(covers=3)])
        self.assertEqual(r.accounts.iloc[0]["owner"], "Alpha")

    def test_tie_held_but_item_credit_kept(self):
        r = score([row(), row(employee="B"), row(item="Scotch Egg"), paid()])
        self.assertFalse(r.accounts.iloc[0]["counted"])
        self.assertIn("Tied", r.accounts.iloc[0]["issues"])
        self.assertEqual(board(r).loc["Alpha", "target_units"], 1)

    def test_unknown_main_held(self):
        r = score([row(item="New unknown main"), row(item="Scotch Egg"), paid()])
        self.assertFalse(r.accounts.iloc[0]["counted"])
        self.assertFalse(r.unknown_products.empty)

    def test_staff_food_anywhere_excluded(self):
        r = score([row(item="Burger SF & Fries"), row(item="Scotch Egg SF"), paid()])
        self.assertTrue(r.accounts.empty)
        self.assertTrue(is_staff_food("sf Burger"))
        self.assertFalse(is_staff_food("Sfumato"))

    def test_pf_zero_price_is_main(self):
        r = score([row(item="PF Tomato Penne", amount=0), paid()])
        self.assertEqual(r.accounts.iloc[0]["owner"], "Alpha")

    def test_package_and_modifier_not_main(self):
        r = score([row(item="Seasonal Set 2Cr"), row(item="Pepper Sauce"), paid()])
        self.assertFalse(r.accounts.iloc[0]["counted"])

    def test_large_walkin_is_not_automatically_preorder(self):
        r = score([row(qty=14, covers=14), paid(covers=14)])
        self.assertTrue(r.accounts.iloc[0]["counted"])

    def test_cover_mismatch_is_informational_not_ownership_blocker(self):
        r = score([row(qty=20, covers=2), paid()])
        self.assertTrue(r.accounts.iloc[0]["counted"])
        self.assertEqual(r.accounts.iloc[0]["issues"], "")
        self.assertIn("Covers", r.accounts.iloc[0]["data_notes"])

    def test_missing_payment_holds_numerator_and_denominator(self):
        r = score([row(), row(item="Scotch Egg")])
        self.assertFalse(r.accounts.iloc[0]["counted"])
        self.assertTrue(r.credits.empty)

    def test_reviewed_non_main_retains_credit_without_opportunity(self):
        rows = [row(item="Scotch Egg"), paid()]
        reviews = {"0001": {"fingerprint": fingerprint(frame(rows)), "note": "Standalone snack visit",
                             "no_main_confirmed": True}}
        r = score(rows, reviews=reviews)
        self.assertEqual(r.credits.target_units.sum(), 1)
        self.assertFalse(r.accounts.iloc[0]["counted"])
        self.assertEqual(r.accounts.iloc[0]["issues"], "")
        self.assertFalse(board(r)["provisional"].any())
        # A financial blocker must not be hidden by the dining classification.
        unpaid = [row(item="Scotch Egg")]
        reviews["0001"]["fingerprint"] = fingerprint(frame(unpaid))
        r = score(unpaid, reviews=reviews)
        self.assertIn("No positive payment", r.accounts.iloc[0]["issues"])
        self.assertTrue(r.credits.empty)

    def test_non_main_review_conflict_and_expiry_fail_closed(self):
        rows = [row(), paid()]
        reviews = {"0001": {"fingerprint": fingerprint(frame(rows)), "note": "Incorrect classification",
                             "no_main_confirmed": True}}
        r = score(rows, reviews=reviews)
        self.assertIn("conflicts", r.accounts.iloc[0]["issues"])
        self.assertFalse(r.accounts.iloc[0]["counted"])
        reviews["0001"]["fingerprint"] = "stale"
        r = score(rows, reviews=reviews)
        self.assertIn("Review expired", r.accounts.iloc[0]["issues"])

    def test_deposit_requires_review_then_counts(self):
        rows = [row(), row(kind="Ledger", item="Deposit Red", qty=0, amount=0, payment=30)]
        r = score(rows)
        self.assertIn("Deposit", r.accounts.iloc[0]["issues"])
        review = {"0001": {"fingerprint": fingerprint(frame(rows)), "note": "Settlement checked",
                          "settlement_confirmed": True}}
        self.assertTrue(score(rows, reviews=review).accounts.iloc[0]["counted"])

    def test_correction_holds_until_final_sales_review(self):
        rows = [row(), row(item="Scotch Egg", qty=3), paid(),
                row(kind="Clear", item="Scotch Egg", qty=-1)]
        r = score(rows)
        self.assertTrue(r.credits.empty)
        review = {"0001": {"fingerprint": fingerprint(frame(rows)), "note": "Checked bill",
                          "final_sales": [
                              {"Date": "2026-09-14", "Employee": "A", "Description": "Burger & Fries", "Quantity": 1, "Sales Amount": 15},
                              {"Date": "2026-09-14", "Employee": "A", "Description": "Scotch Egg", "Quantity": 2, "Sales Amount": 10},
                          ]}}
        self.assertEqual(board(score(rows, reviews=review)).loc["Alpha", "target_units"], 2)

    def test_transfers_not_blindly_double_counted(self):
        r = score([row(), row(item="Scotch Egg"), paid(),
                   row(kind="Merged - from account", item="Scotch Egg", qty=-1)])
        self.assertTrue(r.credits.empty)

    def test_unrelated_drink_void_does_not_block(self):
        r = score([row(), row(kind="Liquor Wastage", item="Beer", qty=-1), paid()])
        self.assertTrue(r.accounts.iloc[0]["counted"])

    def test_stale_review_expires(self):
        rows = [row(), paid()]
        review = {"0001": {"fingerprint": fingerprint(frame(rows)), "note": "Owner checked", "owner": "Beta"}}
        self.assertEqual(score(rows, reviews=review).accounts.iloc[0]["owner"], "Beta")
        rows.append(row(item="Scotch Egg"))
        new = score(rows, reviews=review)
        self.assertEqual(new.accounts.iloc[0]["owner"], "Alpha")
        self.assertIn("expired", new.accounts.iloc[0]["issues"])

    def test_preorder_not_inferred_from_sm_prefix_alone(self):
        r = score([row(item="SM Summer Risott"), paid()])
        self.assertTrue(r.accounts.iloc[0]["counted"])

    def test_explicit_preorder_holds_owner_not_week_one_extras(self):
        r = score([row(), row(item="Pre-Order Dinner"), row(item="Scotch Egg"), paid()])
        self.assertFalse(r.accounts.iloc[0]["counted"])
        self.assertEqual(board(r).loc["Alpha", "target_units"], 1)

    def test_later_preorder_holds_until_eligibility_confirmed(self):
        rows = [row(day="21/09/2026"), row(day="21/09/2026", item="Pre-Order Dinner"),
                row(day="21/09/2026", item="Cheese Souffle"), paid(day="21/09/2026")]
        reviews = {"0001": {"fingerprint": fingerprint(frame(rows)), "note": "Owner verified", "owner": "Alpha"}}
        r = score(rows, reviews=reviews)
        self.assertFalse(r.accounts.iloc[0]["counted"])
        self.assertTrue(r.credits.empty)

    def test_exact_qualified_ties_share_rank(self):
        rows = []
        for employee in ("A", "B"):
            for i in range(15):
                account = employee + str(i)
                rows += [row(account, employee=employee), row(account, employee=employee, item="Scotch Egg"), paid(account)]
        b = board(score(rows))
        self.assertEqual(b.loc["Alpha", "rank"], 1)
        self.assertEqual(b.loc["Beta", "rank"], 1)
        self.assertEqual(b.loc["Beta", "points"], 80)

    def test_main_account_with_zero_cover_or_no_table_is_not_lost(self):
        for kwargs in ({"covers": 0}, {"table": ""}, {"covers": 0, "table": ""}):
            r = score([row(**kwargs), row(item="Scotch Egg", **kwargs), paid(**kwargs)])
            self.assertEqual(board(r).loc["Alpha", "eligible_tables"], 1)
            self.assertEqual(board(r).loc["Alpha", "target_units"], 1)
            self.assertNotEqual(r.accounts.iloc[0]["data_notes"], "")

    def test_no_table_and_no_mains_still_not_dining_candidate(self):
        r = score([row(item="Beer", table="", covers=0), paid(table="", covers=0)])
        self.assertTrue(r.accounts.empty)

    def test_zero_cover_does_not_bypass_missing_payment(self):
        r = score([row(covers=0)])
        self.assertFalse(r.accounts.iloc[0]["counted"])

    def test_drink_transfer_does_not_block_lunch_nibbles(self):
        r = score([row(qty=4, covers=3), row(item="Olives Rustica"),
                   row(item="Charcuterie Plat"), row(kind="Item moved - from account",
                   item="DeluxHotChoco", qty=-3), paid()])
        self.assertEqual(board(r).loc["Alpha", "eligible_tables"], 1)
        self.assertEqual(board(r).loc["Alpha", "target_units"], 2)

    def test_dessert_merge_does_not_block_week_one_main(self):
        r = score([row(), row(kind="Merged - to account", item="Ice Cream"), paid()])
        self.assertTrue(r.accounts.iloc[0]["counted"])

    def test_next_week_starter_clear_does_not_block_week_one(self):
        for day, expected in [("19/09/2026", True), ("22/09/2026", False)]:
            r = score([row(day=day), row(day=day, item="Cheese Souffle"),
                       row(day=day, item="Cheese Souffle", kind="Clear", qty=-1),
                       paid(day=day)])
            self.assertEqual(bool(r.accounts.iloc[0]["counted"]), expected)

    def test_main_transfer_still_held(self):
        r = score([row(), row(kind="Merged - to account"), paid()])
        self.assertFalse(r.accounts.iloc[0]["counted"])

    def test_multiple_table_numbers_still_held(self):
        r = score([row(table="101"), row(table="102"), paid()])
        self.assertFalse(r.accounts.iloc[0]["counted"])

    def test_wings_only_week_two_not_week_one(self):
        for day, expected in [("19/09/2026", 0), ("22/09/2026", 1)]:
            r = score([row(day=day), row(day=day, item="Chicken Wings"), paid(day=day)])
            self.assertEqual(r.credits.target_units.sum(), expected)

    def test_negative_review_quantities_rejected(self):
        rows = [row(), paid()]
        reviews = {"0001": {"fingerprint": fingerprint(frame(rows)), "note": "Invalid adjustment",
                           "final_sales": [{"Date": "2026-09-14", "Employee": "A", "Description": "Scotch Egg",
                                            "Quantity": -1, "Sales Amount": 1}]}}
        with self.assertRaises(ValueError):
            score(rows, reviews=reviews)

    def test_reverse_payment_requires_separate_confirmation(self):
        rows = [row(), paid(), row(kind="Reverse Pay", qty=0, item="CardPay", payment=-30)]
        reviews = {"0001": {"fingerprint": fingerprint(frame(rows)), "note": "Final sales checked",
                           "final_sales": [{"Date": "2026-09-14", "Employee": "A", "Description": "Burger & Fries",
                                            "Quantity": 1, "Sales Amount": 15}]}}
        self.assertFalse(score(rows, reviews=reviews).accounts.iloc[0]["counted"])

    def test_missing_week_targets_hold_even_if_mains_defined(self):
        menus = copy.deepcopy(DEFAULT_MENUS)
        menus[0]["end"] = "2026-10-18"
        r = score([row(day="05/10/2026"), paid(day="05/10/2026")], menus=menus)
        self.assertFalse(r.accounts.iloc[0]["counted"])
        self.assertEqual(board(r, WEEKS[3])["points"].sum(), 0)

    def test_all_account_exclusion_removes_credit_too(self):
        rows = [row(), row(item="Scotch Egg"), paid()]
        reviews = {"0001": {"fingerprint": fingerprint(frame(rows)), "note": "Not a guest bill", "exclude": True}}
        r = score(rows, reviews=reviews)
        self.assertFalse(r.accounts.iloc[0]["counted"])
        self.assertTrue(r.credits.empty)
        self.assertEqual(r.accounts.iloc[0]["issues"], "")
        self.assertEqual(r.accounts.iloc[0]["data_notes"], "Excluded by review")
        self.assertFalse(board(r)["provisional"].any())

    def test_starters_and_menu_boundary(self):
        self.assertEqual(len(STARTERS), 13)
        self.assertNotIn("Devon Crab", STARTERS)
        for day, expected in [("23/09/2026", 2), ("24/09/2026", 0)]:
            r = score([row(day=day), row(day=day, item="Cheese Souffle", qty=2), paid(day=day)])
            self.assertEqual(board(r, WEEKS[1]).loc["Alpha", "target_units"], expected)

    def test_additional_courses_require_main_not_package_or_owner_override(self):
        for number, day, target in [(2, "21/09/2026", "Test Starter"),
                                     (4, "05/10/2026", "Test Dessert")]:
            menus = [{"name": "Synthetic course test", "start": "2026-09-14", "end": "2026-10-18",
                      "mains": {"Burger & Fries": 1},
                      "targets": {str(n): [] for n in range(1, 6)}}]
            menus[0]["targets"][str(number)] = [target]
            for extras in [[], [row(day=day, item="Resident Package")],
                           [row(day=day, item="Test Starter")]]:
                with self.subTest(category=number, extras=len(extras)):
                    rows = [row(day=day, item=target), *extras, paid(day=day)]
                    reviews = {"0001": {"fingerprint": fingerprint(frame(rows)),
                                         "note": "Owner confirmed, no main evidence", "owner": "Alpha"}}
                    r = score(rows, menus=menus, reviews=reviews)
                    self.assertTrue(r.credits.empty)
                    self.assertTrue(r.categories[number].credits.empty)

    def test_package_courses_count_at_zero_price_with_main_and_shared_seller(self):
        for number, day, target in [(2, "21/09/2026", "Test Starter"),
                                     (4, "05/10/2026", "Test Dessert")]:
            menus = [{"name": "Synthetic course test", "start": "2026-09-14", "end": "2026-10-18",
                      "mains": {"Burger & Fries": 1},
                      "targets": {str(n): [] for n in range(1, 6)}}]
            menus[0]["targets"][str(number)] = [target]
            for starter in (False, True):
                rows = [row(day=day, amount=0), row(day=day, item=target, employee="B", qty=2, amount=0),
                        row(day=day, item="Resident Package"), paid(day=day)]
                if starter:
                    rows.append(row(day=day, item="Other Starter", amount=0))
                with self.subTest(category=number, starter=starter):
                    r = score(rows, menus=menus)
                    for result in (r, r.categories[number]):
                        b = board(result, WEEKS[number - 1])
                        self.assertEqual(b.loc["Beta", "target_units"], 2)
                        self.assertEqual(b.loc["Beta", "eligible_tables"], .5)
                        self.assertEqual(b.loc["Alpha", "eligible_tables"], .5)
                        self.assertEqual(b.loc["Beta", "target_revenue"], 0)

    def test_reviewed_removed_main_cannot_enable_starter_credit(self):
        rows = [row(day="21/09/2026"), row(day="21/09/2026", item="Cheese Souffle"), paid(day="21/09/2026")]
        sales = frame(rows).query("Type == 'Sale'")[["Date", "Employee", "Description", "Quantity", "Sales Amount"]]
        sales["Date"] = sales["Date"].astype(str)
        sales.loc[sales.Description.eq("Burger & Fries"), "Quantity"] = 0
        reviews = {"0001": {"fingerprint": fingerprint(frame(rows)), "note": "Main cancelled",
                             "final_sales": sales.to_dict("records"), "no_main_confirmed": True}}
        r = score(rows, reviews=reviews)
        self.assertTrue(r.credits.empty)
        self.assertFalse(r.accounts.iloc[0]["counted"])

    def test_rolling_starters_still_require_main_after_launch_week(self):
        menus = copy.deepcopy(DEFAULT_MENUS)
        menus[0]["end"] = "2026-10-18"
        menus[0]["targets"].update({"3": [], "4": [], "5": []})
        for with_main in (False, True):
            rows = [row(day="05/10/2026", item="Cheese Souffle"), paid(day="05/10/2026")]
            if with_main:
                rows.append(row(day="05/10/2026"))
            r = score(rows, menus=menus)
            self.assertEqual(r.categories[2].credits.target_units.sum(), int(with_main))

    def test_package_allowance_without_actual_course_earns_no_credit(self):
        r = score([row(day="21/09/2026"), row(day="21/09/2026", item="Resident Package"),
                   paid(day="21/09/2026")])
        self.assertTrue(r.credits.empty)
        self.assertTrue(r.accounts.iloc[0]["counted"])

    def test_new_menu_does_not_rewrite_old_days(self):
        menus = copy.deepcopy(DEFAULT_MENUS)
        menus.append({"name": "Test seasonal", "start": "2026-09-24", "end": "2026-09-27",
                      "mains": {"New Main": 1}, "targets": {"2": ["New Starter"]}})
        rows = [row("01", day="23/09/2026"), row("01", day="23/09/2026", item="Cheese Souffle"), paid("01", day="23/09/2026"),
                row("02", day="24/09/2026", item="New Main"), row("02", day="24/09/2026", item="New Starter", qty=2), paid("02", day="24/09/2026")]
        r = score(rows, menus=menus)
        self.assertEqual(board(r, WEEKS[1]).loc["Alpha", "target_units"], 3)
        self.assertEqual(board(r, WEEKS[1]).loc["Alpha", "eligible_tables"], 2)

    def test_unconfirmed_future_week_no_ranking_points(self):
        r = score([row(day="05/10/2026"), paid(day="05/10/2026")])
        self.assertEqual(board(r, WEEKS[3])["points"].sum(), 0)

    def test_no_overlapping_menus(self):
        with self.assertRaises(ValueError):
            validate_menus(DEFAULT_MENUS * 2)

    def test_minimum_threshold_observers_and_overall(self):
        rows = []
        for i in range(15):
            rows += [row(str(i)), row(str(i), item="Scotch Egg", qty=2), paid(str(i))]
        r = score(rows)
        self.assertEqual(board(r).loc["Alpha", "points"], 80)
        overall = overall_leaderboard(r, ROSTER, COMPETITORS).set_index("Display")
        self.assertEqual(overall.loc["Alpha", "total_points"], 80)
        self.assertEqual(overall.loc["Manager", "total_points"], 0)

    def test_missing_account_id_rejected(self):
        with self.assertRaises(ValueError):
            frame([row(account="")])

    def test_leading_zero_account_id_preserved(self):
        self.assertEqual(frame([row()]).iloc[0]["Account ID"], "0001")

    def test_zonal_thousands_separators(self):
        self.assertEqual(frame([row(payment="1,074.15")]).iloc[0]["Payment Amount"], 1074.15)
        self.assertEqual(frame([row(amount="-1,197.50")]).iloc[0]["Sales Amount"], -1197.5)
        with self.assertRaises(ValueError):
            frame([row(payment="1,07.15")])

    def test_invalid_dates_and_numbers_rejected(self):
        for altered in [row(day="not a date"), row(qty="NaN"), row(covers=-1)]:
            with self.assertRaises(ValueError):
                frame([altered])

    def test_empty_roster_accounts_never_competitor(self):
        r = score_accounts(frame([row(), paid()]), roster={})
        self.assertEqual(r.accounts.iloc[0]["owner"], EXCLUDED_OWNER)


class StorageTests(unittest.TestCase):
    def payload(self):
        return pd.DataFrame([row(), row(item="Scotch Egg"), row(item="Scotch Egg"), paid()]).to_csv(index=False).encode()

    def test_idempotent_replacement_preserves_repeats(self):
        a, changed = replacement_state(empty_state(), self.payload(), today=date(2026, 9, 20))
        b, changed_again = replacement_state(a, self.payload(), today=date(2026, 9, 20))
        self.assertTrue(changed)
        self.assertFalse(changed_again)
        self.assertEqual(a, b)
        self.assertEqual(len(load_transactions(a["csv"].encode())), 4)

    def test_partial_upload_rejected(self):
        with self.assertRaises(ValueError):
            validate_replacement(frame([row(day="18/09/2026")]))

    def test_missing_accounts_and_rows_rejected(self):
        old = frame([row("1"), row("2"), row("2")])
        with self.assertRaises(ValueError):
            validate_replacement(frame([row("1")]), old)
        with self.assertRaises(ValueError):
            validate_replacement(frame([row("1"), row("2")]), old)
        validate_replacement(frame([row("1")]), old, allow_reduction=True)

    def test_future_dates_rejected(self):
        with self.assertRaises(ValueError):
            validate_replacement(frame([row(), row(day="21/09/2026")]), today=date(2026, 9, 20))

    def test_restart_and_atomic_invalid_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory) / "state.gz")
            state, version = store.load()
            new, _ = replacement_state(state, self.payload())
            saved = store.save(new, version)
            restored, loaded = LocalStore(store.path).load()
            self.assertEqual(new, restored)
            self.assertEqual(saved, loaded)
            with self.assertRaises(ValueError):
                replacement_state(restored, b"bad,file\n1,2")
            self.assertEqual(store.load()[0], new)
            with self.assertRaises(StorageError):
                store.save(state, None)

    def test_snapshot_roundtrip(self):
        self.assertEqual(decode(encode(empty_state())), empty_state())

    def test_customer_details_not_persisted(self):
        d = pd.DataFrame([row(), paid()])
        d["Customer Name"] = "PRIVATE CUSTOMER"
        new, _ = replacement_state(empty_state(), d.to_csv(index=False).encode())
        self.assertNotIn("PRIVATE CUSTOMER", new["csv"])

    def test_refuse_public_storage(self):
        store = GitHubStore("example/private-data", "test-only")
        with patch.object(store, "_request", return_value={"private": False}):
            with self.assertRaises(StorageError):
                store.load()
            with self.assertRaises(StorageError):
                store.save(empty_state(), None)

    def test_github_save_uses_expected_sha(self):
        store = GitHubStore("example/private-data", "test-only")
        with patch.object(store, "_request", side_effect=[{"private": True}, {"content": {"sha": "new"}}]) as request:
            self.assertEqual(store.save(empty_state(), "old"), "new")
            self.assertEqual(request.call_args.args[2]["sha"], "old")


if __name__ == "__main__":
    unittest.main()
