import unittest
from src.roster import with_additions
from src.config import WEEKS
from src.processing import score_accounts, weekly_leaderboard, overall_leaderboard
from src.storage import empty_state, encode, decode
from test_scoring import ROSTER, COMPETITORS, frame, row, paid


class PrivateRosterTests(unittest.TestCase):
    def test_addition_scores_existing_export_and_preserves_managers(self):
        roster, competitors = with_additions(ROSTER, COMPETITORS, [
            {"display": "New Server", "aliases": ["New Till"], "competitor": True}])
        r = score_accounts(frame([row(employee="New Till"), row(employee="New Till", item="Scotch Egg"), paid()]),
                           roster=roster)
        b = weekly_leaderboard(r, WEEKS[0], roster, competitors).set_index("Display")
        self.assertEqual(b.loc["New Server", "eligible_tables"], 1)
        self.assertEqual(b.loc["New Server", "target_units"], 1)
        self.assertEqual(b.loc["New Server", "status"], "Building sample")
        self.assertEqual(b.loc["Manager", "status"], "Not competing")
        self.assertIn("New Server", overall_leaderboard(r, roster, competitors).Display.tolist())
        self.assertNotIn("New Till", ROSTER)

    def test_conflicting_alias_or_eligibility_fails_closed(self):
        for entry in [
            {"display": "Another", "aliases": ["A"], "competitor": True},
            {"display": "Manager", "aliases": ["M2"], "competitor": True},
            {"display": "", "aliases": ["New"], "competitor": True},
            {"display": "Another", "aliases": ["New"], "competitor": "false"},
        ]:
            with self.assertRaises(ValueError):
                with_additions(ROSTER, COMPETITORS, [entry])

    def test_addition_survives_private_snapshot(self):
        state = empty_state()
        state["roster_additions"] = [{"display": "New Server", "aliases": ["New Till"], "competitor": True}]
        self.assertEqual(decode(encode(state))["roster_additions"], state["roster_additions"])

    def test_existing_identical_addition_is_idempotent(self):
        addition = [{"display": "Alpha", "aliases": ["A", "A alias"], "competitor": True}]
        self.assertEqual(with_additions(ROSTER, COMPETITORS, addition), (ROSTER, COMPETITORS))
