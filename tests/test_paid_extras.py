"""Synthetic paid-extra regressions; no real employee or transaction data."""
import copy
import unittest
from src.config import WEEKS
from src.menus import DEFAULT_MENUS
from src.processing import category_leaderboard
from test_scoring import row, paid, score, board, ROSTER, COMPETITORS

class PaidExtrasTests(unittest.TestCase):
    def menus(self):
        menus=copy.deepcopy(DEFAULT_MENUS)
        menus[0]["end"]="2026-10-18"
        menus[0]["targets"]["3"]=["TripleCook Chips","with Emmental","Garlic Butter"]
        return menus

    def test_only_positive_extra_sales_and_correct_shares(self):
        day="28/09/2026"
        result=score([row(day=day),paid(day=day),
            row(day=day,employee="B",item="TripleCook Chips",qty=2,amount=9.9),
            row(day=day,employee="M",item="with Emmental",qty=1,amount=0),
            row(day=day,employee="M",item="Garlic Butter",qty=99,amount=0)],
            menus=self.menus())
        b=board(result,WEEKS[2])
        self.assertEqual(b.loc["Beta","target_units"],2)
        self.assertEqual(b.loc["Alpha","eligible_tables"],.5)
        self.assertEqual(b.loc["Beta","eligible_tables"],.5)
        self.assertEqual(b.loc["Manager","eligible_tables"],0)
        self.assertEqual(b.loc["Manager","target_units"],0)

    def test_free_courses_still_count_in_continuing_categories(self):
        day="28/09/2026"
        result=score([row(day=day),paid(day=day),
            row(day=day,item="Scotch Egg",amount=0),
            row(day=day,item="Cheese Souffle",amount=0),
            row(day=day,item="TripleCook Chips",amount=0)],menus=self.menus())
        self.assertEqual(board(result,WEEKS[2]).loc["Alpha","target_units"],0)
        for n in (0,1):
            b=category_leaderboard(result,WEEKS[n],ROSTER,COMPETITORS).set_index("Display")
            self.assertEqual(b.loc["Alpha","target_units"],1)

    def test_no_retroactive_week_three_points(self):
        result=score([row(day="27/09/2026"),paid(day="27/09/2026"),
            row(day="27/09/2026",item="TripleCook Chips",amount=4.95)],menus=self.menus())
        self.assertEqual(board(result,WEEKS[2]).target_units.sum(),0)

    def test_paid_extras_continue_after_launch_week(self):
        result=score([row(day="05/10/2026"),paid(day="05/10/2026"),
            row(day="05/10/2026",item="TripleCook Chips",amount=4.95)],menus=self.menus())
        b=category_leaderboard(result,WEEKS[2],ROSTER,COMPETITORS).set_index("Display")
        self.assertEqual(b.loc["Alpha","target_units"],1)
        self.assertEqual(board(result,WEEKS[2]).target_units.sum(),0)
