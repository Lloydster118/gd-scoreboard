"""Explicit, fingerprinted operational ownership; never guess ties."""
import unittest
from src.processing import fingerprint, EXCLUDED_OWNER, category_leaderboard
from src.config import WEEKS
from test_scoring import row, paid, frame, score, board, ROSTER, COMPETITORS


class SharedOwnershipTests(unittest.TestCase):
    def fixture(self):
        rows = [row(employee="A"), row(employee="B"), paid()]
        reviews = {"0001": {"fingerprint": fingerprint(frame(rows)),
                            "note": "Manager confirmed shared service",
                            "shared_owners": ["Alpha", "Beta"]}}
        return rows, reviews

    def test_equal_shares_and_no_automatic_tie_resolution(self):
        rows, reviews = self.fixture()
        self.assertFalse(score(rows).accounts.iloc[0].counted)
        result = score(rows, reviews=reviews)
        self.assertEqual(board(result).loc["Alpha", "eligible_tables"], .5)
        self.assertEqual(board(result).loc["Beta", "eligible_tables"], .5)
        self.assertEqual(result.accounts.iloc[0].issues, "")
        rolling = category_leaderboard(result, WEEKS[0], ROSTER, COMPETITORS).set_index("Display")
        self.assertEqual(rolling.loc["Alpha", "eligible_tables"], .5)

    def test_seller_credit_is_not_split_and_owner_seller_not_duplicated(self):
        rows, reviews = self.fixture()
        rows += [row(item="Scotch Egg", qty=3)]
        reviews["0001"]["fingerprint"] = fingerprint(frame(rows))
        b = board(score(rows, reviews=reviews))
        self.assertEqual(b.loc["Alpha", "target_units"], 3)
        self.assertEqual(b.loc["Beta", "target_units"], 0)
        self.assertEqual(b.loc["Alpha", "eligible_tables"], .5)

    def test_additional_seller_shares_one_total_opportunity(self):
        rows, reviews = self.fixture()
        rows += [row(employee="M", item="Scotch Egg")]
        reviews["0001"]["fingerprint"] = fingerprint(frame(rows))
        b = board(score(rows, reviews=reviews))
        for who in ["Alpha", "Beta", "Manager"]:
            self.assertAlmostEqual(b.loc[who, "eligible_tables"], 1/3)
        self.assertAlmostEqual(b.eligible_tables.sum(), 1)

    def test_outside_roster_share_not_given_to_competitor(self):
        rows = [row(), row(employee="Visitor"), paid()]
        reviews = {"0001": {"fingerprint": fingerprint(frame(rows)), "note": "Shared",
                            "shared_owners": ["Alpha", EXCLUDED_OWNER + ": Visitor"]}}
        self.assertEqual(board(score(rows, reviews=reviews)).loc["Alpha", "eligible_tables"], .5)

    def test_stale_review_still_holds_tie(self):
        rows, reviews = self.fixture()
        reviews["0001"]["fingerprint"] = "stale"
        result = score(rows, reviews=reviews)
        self.assertFalse(result.accounts.iloc[0].counted)
        self.assertIn("Review expired", result.accounts.iloc[0].issues)

    def test_invalid_shared_lists_rejected(self):
        rows, reviews = self.fixture()
        for invalid in ([], ["Alpha"], ["Alpha","Alpha"], "Alpha,Beta",
                        ["Alpha",None], ["Alpha","Invented"], ["Alpha",EXCLUDED_OWNER]):
            with self.subTest(invalid=invalid):
                reviews["0001"]["shared_owners"] = invalid
                with self.assertRaises(ValueError):
                    score(rows, reviews=reviews)

    def test_conflicting_single_owner_rejected(self):
        rows, reviews = self.fixture()
        reviews["0001"]["owner"] = "Alpha"
        with self.assertRaises(ValueError):
            score(rows, reviews=reviews)

    def test_shared_owners_do_not_waive_payment_or_create_mains(self):
        rows, reviews = self.fixture()
        rows = rows[:-1]
        reviews["0001"]["fingerprint"] = fingerprint(frame(rows))
        result = score(rows, reviews=reviews)
        self.assertFalse(result.accounts.iloc[0].counted)
        self.assertIn("No positive payment", result.accounts.iloc[0].issues)
        rows = [row(item="Scotch Egg"), paid()]
        reviews["0001"]["fingerprint"] = fingerprint(frame(rows))
        result = score(rows, reviews=reviews)
        self.assertFalse(result.accounts.iloc[0].counted)
        self.assertIn("main-course evidence", result.accounts.iloc[0].issues)
