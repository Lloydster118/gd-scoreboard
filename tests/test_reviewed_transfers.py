"""Synthetic evidence-only transfer regressions; no workplace data."""
import unittest

from src.processing import fingerprint, SALE_FIELDS, category_leaderboard
from src.config import WEEKS
from test_scoring import row, paid, frame, score, board, ROSTER, COMPETITORS


class ReviewedTransferTests(unittest.TestCase):
    def fixture(self):
        rows = [row("source"), row("source", kind="Item moved - from account", qty=-1),
                row("dest", employee="M", kind="Item moved - to account"), paid("dest")]
        raw = frame(rows)
        final = raw[raw["Account ID"].eq("source") & raw.Type.eq("Sale")][SALE_FIELDS].copy()
        final["Date"] = final.Date.astype(str)
        reviews = {
            "source": {"fingerprint": fingerprint(raw[raw["Account ID"].eq("source")]),
                       "note": "Matched full transfer", "exclude": True},
            "dest": {"fingerprint": fingerprint(raw[raw["Account ID"].eq("dest")]),
                     "note": "Paid matched transfer, preserve original seller",
                     "transfer_only": True, "final_sales": final.to_dict("records"),
                     "linked_fingerprints": {
                         "source": fingerprint(raw[raw["Account ID"].eq("source")])}},
        }
        return rows, reviews

    def test_paid_reviewed_transfer_counts_once_weekly_and_rolling(self):
        rows, reviews = self.fixture()
        result = score(rows, reviews=reviews)
        self.assertEqual(board(result).loc["Alpha", "eligible_tables"], 1)
        self.assertEqual(board(result).loc["Manager", "eligible_tables"], 0)
        rolling = category_leaderboard(result, WEEKS[0], ROSTER, COMPETITORS).set_index("Display")
        self.assertEqual(rolling.loc["Alpha", "eligible_tables"], 1)
        self.assertTrue(result.accounts.issues.eq("").all())

    def test_unreviewed_incoming_does_not_automatically_score(self):
        rows, _ = self.fixture()
        self.assertEqual(board(score(rows)).eligible_tables.sum(), 0)

    def test_stale_destination_stays_visible_and_held(self):
        rows, reviews = self.fixture()
        reviews["dest"]["fingerprint"] = "stale"
        result = score(rows, reviews=reviews)
        dest = result.accounts.set_index("account_id").loc["dest"]
        self.assertIn("Review expired", dest.issues)
        self.assertFalse(dest.counted)
        self.assertTrue(result.credits.empty)

    def test_changed_source_invalidates_destination(self):
        rows, reviews = self.fixture()
        rows[0]["Employee"] = "B"
        result = score(rows, reviews=reviews)
        dest = result.accounts.set_index("account_id").loc["dest"]
        self.assertIn("Review expired", dest.issues)
        self.assertFalse(dest.counted)

    def test_missing_final_sales_cannot_count(self):
        rows, reviews = self.fixture()
        del reviews["dest"]["final_sales"]
        result = score(rows, reviews=reviews)
        self.assertIn("Transfer-only", result.accounts.set_index("account_id").loc["dest"].issues)
        self.assertEqual(board(result).eligible_tables.sum(), 0)

    def test_payment_still_required(self):
        rows, reviews = self.fixture()
        rows = [r for r in rows if r["Type"] != "Payment"]
        reviews["dest"]["fingerprint"] = fingerprint(frame(rows)[lambda x: x["Account ID"].eq("dest")])
        result = score(rows, reviews=reviews)
        self.assertIn("No positive payment", result.accounts.set_index("account_id").loc["dest"].issues)
        self.assertEqual(board(result).eligible_tables.sum(), 0)

    def test_week_two_destination_enters_both_active_categories(self):
        rows, reviews = self.fixture()
        for r in rows:
            r["Date"] = "22/09/2026"
        raw = frame(rows)
        for aid in reviews:
            reviews[aid]["fingerprint"] = fingerprint(raw[raw["Account ID"].eq(aid)])
        reviews["dest"]["linked_fingerprints"]["source"] = reviews["source"]["fingerprint"]
        reviews["dest"]["final_sales"][0]["Date"] = "2026-09-22"
        result = score(rows, reviews=reviews)
        self.assertEqual(board(result, WEEKS[1]).loc["Alpha", "eligible_tables"], 1)
        self.assertEqual(category_leaderboard(result, WEEKS[0], ROSTER, COMPETITORS)
                         .set_index("Display").loc["Alpha", "eligible_tables"], 1)
