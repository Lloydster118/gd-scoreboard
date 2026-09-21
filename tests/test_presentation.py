"""HTML output must be safe and reflect the scorer, not introduce new scoring."""
import unittest
import pandas as pd
from src.presentation import weekly_cards, overall_cards, stylesheet, fmt


class PresentationTests(unittest.TestCase):
    def test_weekly_escapes_names_and_retains_fractional_opportunities(self):
        board = pd.DataFrame([dict(Display='<script>alert("x")</script>', rank=1,
                                   portions_per_100_tables=64.516, target_units=10,
                                   eligible_tables=15.5, status="Qualified")])
        output = weekly_cards(board)
        self.assertNotIn("<script>", output)
        self.assertIn("&lt;script&gt;", output)
        self.assertIn("64.5", output)
        self.assertIn("15.5", output)

    def test_unavailable_rate_not_rendered_as_zero(self):
        self.assertNotEqual(fmt(float("nan")), "0")
        self.assertEqual(fmt(0), "0")
        self.assertEqual(fmt(10), "10")

    def test_equal_overall_scores_show_equal_ranks(self):
        board = pd.DataFrame([dict(Display=n, overall_score=20, categories_qualified=1,
                                   **{f"category_{i}": 20 if i == 1 else 0 for i in range(1, 6)})
                              for n in ("Example A", "Example B")])
        output = overall_cards(board)
        self.assertEqual(output.count('class="gd-rank">1<'), 2)

    def test_all_appearance_modes_have_explicit_tokens(self):
        for mode in ("System", "Light", "Dark"):
            output = stylesheet(mode)
            self.assertNotIn("THEME_TOKENS", output)
            self.assertIn("--bg:", output)
            self.assertIn("prefers-reduced-motion", output)
