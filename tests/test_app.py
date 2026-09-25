"""Exercise actual Streamlit rendering and the admin gate with synthetic data."""
import tempfile
from pathlib import Path
import unittest
from dataclasses import replace
from datetime import date
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.storage import LocalStore, empty_state, replacement_state
from src.config import WEEKS
from test_scoring import row, paid
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


class AppTests(unittest.TestCase):
    def test_edit_shared_review_preserves_owner_allocation(self):
        from src.processing import fingerprint, load_transactions
        rows = [row(employee="Server One"), row(employee="Server Two"), paid()]
        csv = pd.DataFrame(rows).to_csv(index=False).encode()
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory) / "state.gz")
            state, _ = replacement_state(empty_state(), csv)
            state["reviews"]["0001"] = {
                "fingerprint": fingerprint(load_transactions(csv)),
                "note": "Approved shared service",
                "shared_owners": ["Server 1", "Server 2"],
            }
            store.save(state, None)
            with patch("src.storage.LocalStore", return_value=store):
                app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
                app.secrets = {"admin_pin": "synthetic-secret", "storage": {"development_local": True}}
                app.run()
                app.text_input[0].set_value("synthetic-secret")
                next(b for b in app.button if b.label == "Unlock admin").click().run()
                next(b for b in app.button if b.label == "Save reviewed account").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(store.load()[0]["reviews"]["0001"]["shared_owners"],
                                 ["Server 1", "Server 2"])

    def test_edit_transfer_review_preserves_provenance(self):
        from test_reviewed_transfers import ReviewedTransferTests
        rows, reviews = ReviewedTransferTests().fixture()
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory) / "state.gz")
            state, _ = replacement_state(empty_state(), pd.DataFrame(rows).to_csv(index=False).encode())
            state["reviews"] = reviews
            store.save(state, None)
            with patch("src.storage.LocalStore", return_value=store):
                app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
                app.secrets = {"admin_pin": "synthetic-secret", "storage": {"development_local": True}}
                app.run()
                app.text_input[0].set_value("synthetic-secret")
                next(b for b in app.button if b.label == "Unlock admin").click().run()
                next(s for s in app.selectbox if s.label == "Account to inspect or correct").select("dest").run()
                next(b for b in app.button if b.label == "Save reviewed account").click().run()
                self.assertEqual(len(app.exception), 0)
                saved = store.load()[0]["reviews"]["dest"]
                self.assertTrue(saved["transfer_only"])
                self.assertEqual(saved["linked_fingerprints"], reviews["dest"]["linked_fingerprints"])

    def test_admin_can_freeze_completed_week_and_public_result_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory) / "state.gz")
            rows = [row(employee="Server One"), row(employee="Server One", item="Scotch Egg"), paid(),
                    row("2", employee="Server One", day="19/09/2026"), paid("2", day="19/09/2026")]
            state, _ = replacement_state(empty_state(), pd.DataFrame(rows).to_csv(index=False).encode(),
                                         today=date(2026, 9, 20))
            store.save(state, None)
            # A synthetic already-closed window exercises the real admin button.
            weeks = (replace(WEEKS[0], end=date(2026, 9, 19)),) + WEEKS[1:]
            with patch("src.storage.LocalStore", return_value=store), patch("src.config.WEEKS", weeks):
                app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
                app.secrets = {"admin_pin": "synthetic-secret", "storage": {"development_local": True}}
                app.run()
                app.text_input[0].set_value("synthetic-secret")
                next(b for b in app.button if b.label == "Unlock admin").click().run()
                next(c for c in app.checkbox if "entire week's export" in c.label).check().run()
                next(b for b in app.button if b.label == "Freeze weekly prize result").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertIn("1", store.load()[0]["weekly_results"])
                self.assertTrue(any("weekly result frozen" in x.value for x in app.success))
                next(b for b in app.button if b.label == "Lock admin").click().run()
                self.assertFalse(any(b.label == "Freeze weekly prize result" for b in app.button))

    def test_empty_app_and_missing_pin_fail_closed(self):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
        app.secrets = {"admin_pin": "", "storage": {"development_local": False}}
        app.run()
        self.assertEqual(len(app.exception), 0)
        self.assertTrue(any("disabled" in e.value for e in app.error))
        self.assertEqual(len(app.file_uploader) if hasattr(app, "file_uploader") else 0, 0)

    def test_authentication_and_render_populated_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory) / "state.gz")
            rows = [row(employee="Server One"), row(employee="Server One", item="Scotch Egg"), paid()]
            state, _ = replacement_state(empty_state(), pd.DataFrame(rows).to_csv(index=False).encode())
            state["roster_additions"] = [{"display": "New Server", "aliases": ["New Till"], "competitor": True}]
            version = store.save(state, None)
            with patch("src.storage.LocalStore.load", return_value=(state, version)):
                app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
                app.secrets = {"admin_pin": "synthetic-secret", "storage": {"development_local": True}}
                app.run()
                self.assertEqual(len(app.exception), 0)
                app.text_input[0].set_value("wrong")
                next(b for b in app.button if b.label == "Unlock admin").click().run()
                self.assertTrue(any("Incorrect" in e.value for e in app.error))
                app.text_input[0].set_value("synthetic-secret")
                next(b for b in app.button if b.label == "Unlock admin").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(any(b.label == "Lock admin" for b in app.button))
                self.assertTrue(any("Server 1" in str(d.value) for d in app.dataframe))
                self.assertTrue(any("New Server" in str(d.value) for d in app.dataframe))
                next(b for b in app.button if b.label == "Lock admin").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertFalse(any(b.label == "Save reviewed account" for b in app.button))


if __name__ == "__main__":
    unittest.main()
