"""Cache correctness: freshness, isolation, credentials and invalidation."""
import copy
import unittest
from unittest.mock import patch
import pandas as pd

from src.runtime import saved_snapshot, scored_snapshot
from src.processing import score_accounts, fingerprint, load_transactions
from src.menus import DEFAULT_MENUS
from src.storage import StorageError
from test_scoring import row, paid, ROSTER


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        saved_snapshot.clear()
        scored_snapshot.clear()

    def tearDown(self):
        saved_snapshot.clear()
        scored_snapshot.clear()

    def csv(self):
        return pd.DataFrame([row(), row(item="Scotch Egg"), paid()]).to_csv(index=False)

    def test_repeated_inputs_score_once_and_return_isolated_copies(self):
        with patch("src.runtime.score_accounts", wraps=score_accounts) as calculate:
            raw, result = scored_snapshot(self.csv(), DEFAULT_MENUS, {}, ROSTER)
            result.accounts.loc[0, "issues"] = "mutated"
            raw.loc[0, "Employee"] = "mutated"
            fresh, clean = scored_snapshot(self.csv(), DEFAULT_MENUS, {}, ROSTER)
            self.assertEqual(calculate.call_count, 1)
            self.assertNotIn("mutated", fresh.Employee.tolist())
            self.assertNotIn("mutated", clean.accounts.issues.tolist())

    def test_every_scoring_input_invalidates(self):
        csv = self.csv()
        with patch("src.runtime.score_accounts", wraps=score_accounts) as calculate:
            scored_snapshot(csv, DEFAULT_MENUS, {}, ROSTER)
            changed_csv = csv.replace("Scotch Egg", "Olives Rustica")
            scored_snapshot(changed_csv, DEFAULT_MENUS, {}, ROSTER)
            menus = copy.deepcopy(DEFAULT_MENUS)
            menus[0]["targets"]["1"] = ["Scotch Egg"]
            scored_snapshot(csv, menus, {}, ROSTER)
            raw = load_transactions(csv.encode())
            reviews = {"0001": {"fingerprint": fingerprint(raw), "note": "Exclude test", "exclude": True}}
            _, excluded = scored_snapshot(csv, DEFAULT_MENUS, reviews, ROSTER)
            self.assertFalse(excluded.accounts.counted.any())
            scored_snapshot(csv, DEFAULT_MENUS, {}, {**ROSTER, "A": "Changed"})
            self.assertEqual(calculate.call_count, 5)

    def test_snapshot_cache_is_scoped_to_repo_and_credentials(self):
        with patch("src.runtime.GitHubStore") as store:
            store.return_value.load.return_value = ({"reviews": {}}, "v1")
            a, _ = saved_snapshot("example/one", "synthetic-token")
            a["reviews"]["mutated"] = True
            b, _ = saved_snapshot("example/one", "synthetic-token")
            self.assertEqual(b["reviews"], {})
            self.assertEqual(store.return_value.load.call_count, 1)
            saved_snapshot("example/one", "rotated-synthetic-token")
            saved_snapshot("example/two", "synthetic-token")
            self.assertEqual(store.return_value.load.call_count, 3)

    def test_explicit_refresh_fetches_new_version(self):
        with patch("src.runtime.GitHubStore") as store:
            store.return_value.load.side_effect = [({"csv": "old"}, "v1"), ({"csv": "new"}, "v2")]
            self.assertEqual(saved_snapshot("example/repo", "synthetic")[1], "v1")
            saved_snapshot.clear()
            self.assertEqual(saved_snapshot("example/repo", "synthetic")[1], "v2")

    def test_refresh_failure_is_not_silently_served_as_old_data(self):
        with patch("src.runtime.GitHubStore") as store:
            store.return_value.load.return_value = ({"csv": "old"}, "v1")
            saved_snapshot("example/repo", "synthetic")
            saved_snapshot.clear()
            store.return_value.load.side_effect = StorageError("offline")
            with self.assertRaises(StorageError):
                saved_snapshot("example/repo", "synthetic")

    def test_failed_scoring_does_not_reuse_previous_result(self):
        scored_snapshot(self.csv(), DEFAULT_MENUS, {}, ROSTER)
        with self.assertRaises(ValueError):
            scored_snapshot("invalid,csv\n1,2", DEFAULT_MENUS, {}, ROSTER)
