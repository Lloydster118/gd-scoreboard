"""Exercise actual Streamlit rendering and the admin gate with synthetic data."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from src.storage import LocalStore, empty_state, replacement_state
from test_scoring import row, paid
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


class AppTests(unittest.TestCase):
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
                next(b for b in app.button if b.label == "Lock admin").click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertFalse(any(b.label == "Save reviewed account" for b in app.button))


if __name__ == "__main__":
    unittest.main()
