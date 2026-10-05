"""The points progress view must agree with the official overall leaderboard."""
import datetime as dt
import unittest

import pandas as pd

from src import progress
from src.config import WEEKS
from src.processing import ScoringResult, overall_leaderboard, weekly_leaderboard


def board_result():
    accounts = pd.DataFrame([
        dict(account_id="a1", Date=dt.date(2026, 9, 15), table="1", covers=2, owner="Ann", counted=True,
             credit_allowed=True, issues="", fingerprint="", data_notes="", owner_identity="Ann", owner_participants=("Ann",)),
        dict(account_id="a2", Date=dt.date(2026, 9, 22), table="2", covers=2, owner="Bob", counted=True,
             credit_allowed=True, issues="", fingerprint="", data_notes="", owner_identity="Bob", owner_participants=("Bob",)),
    ])
    credits = pd.DataFrame([
        dict(account_id="a1", Date=dt.date(2026, 9, 15), week=1, Display="Ann", target_units=3, target_revenue=15.0),
        dict(account_id="a2", Date=dt.date(2026, 9, 22), week=1, Display="Bob", target_units=1, target_revenue=5.0),
    ])
    empty = pd.DataFrame(columns=["Date", "Description", "reason"])
    result = ScoringResult(accounts, credits, empty)
    result.categories[1] = ScoringResult(accounts, credits, empty)
    return result


class ProgressTests(unittest.TestCase):
    roster = {"Ann": "Ann", "Bob": "Bob"}
    competitors = {"Ann", "Bob"}

    def test_latest_snapshot_matches_official_overall(self):
        result = board_result()
        snaps = progress.build_snapshots(result, self.roster, self.competitors, dt.date(2026, 9, 24))
        table = progress.points_table(snaps[-1])
        official = overall_leaderboard(result, self.roster, self.competitors).set_index("Display")
        for person in table.index:
            self.assertAlmostEqual(table.at[person, "total"], official.at[person, "overall_score"])

    def test_snapshots_use_only_sales_up_to_cutoff(self):
        result = board_result()
        snaps = progress.build_snapshots(result, self.roster, self.competitors, dt.date(2026, 9, 24))
        self.assertEqual([s.label for s in snaps], ["End of week 1", "Latest"])
        first = snaps[0].boards[1].set_index("Display")
        self.assertEqual(first.at["Bob", "eligible_tables"], 0)
        self.assertEqual(snaps[1].boards[1].set_index("Display").at["Bob", "eligible_tables"], 1)

    def test_grid_shows_movement(self):
        result = board_result()
        snaps = progress.build_snapshots(result, self.roster, self.competitors, dt.date(2026, 9, 24))
        self.assertIn("Overall / 100", progress.grid(snaps).columns)
        self.assertEqual(progress.ordinal(11), "11th")
        self.assertEqual(progress.ordinal(22), "22nd")

    def test_tables_render_without_error(self):
        result = board_result()
        snaps = progress.build_snapshots(result, self.roster, self.competitors, dt.date(2026, 9, 24))
        progress.history(snaps)
        progress.category_path(snaps, 1)
        progress.person_view(snaps, result, "Ann")
        self.assertEqual(progress.best_tables(result, "Ann").iloc[0]["Portions"], 3)


if __name__ == "__main__":
    unittest.main()


class ProgressAppTests(unittest.TestCase):
    def test_five_week_view_shows_points_grid_without_errors(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from streamlit.testing.v1 import AppTest
        from src.storage import LocalStore, empty_state, replacement_state
        from test_scoring import row, paid
        root = Path(__file__).resolve().parents[1]
        rows = [row(employee="Server One"), row(employee="Server One", item="Scotch Egg"), paid(),
                row("2", employee="Server Two", day="16/09/2026"), paid("2", day="16/09/2026")]
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory) / "state.gz")
            state, _ = replacement_state(empty_state(), pd.DataFrame(rows).to_csv(index=False).encode(),
                                         today=dt.date(2026, 9, 20))
            store.save(state, None)
            with patch("src.storage.LocalStore", return_value=store):
                app = AppTest.from_file(str(root / "app.py"), default_timeout=30)
                app.secrets = {"admin_pin": "synthetic-secret", "storage": {"development_local": True}}
                app.run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(any("how your points move" in s.value for s in app.subheader))
                self.assertTrue(any("Overall / 100" in df.value.columns for df in app.dataframe))
