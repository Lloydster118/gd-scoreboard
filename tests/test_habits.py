"""Synthetic regressions for weekly snapshots and cumulative equal-weight habits."""
import copy
from datetime import date
import unittest

import pandas as pd

from src.config import WEEKS
from src.menus import DEFAULT_MENUS
from src.processing import score_accounts, weekly_leaderboard, category_leaderboard, overall_leaderboard
from src.storage import empty_state, replacement_state, finalise_week, weekly_result, encode, decode
from test_scoring import row, paid, frame, score, board, ROSTER, COMPETITORS


class HabitTests(unittest.TestCase):
    def test_later_nibbles_only_change_overall_not_weekly(self):
        before = [row("1"), row("1", item="Scotch Egg"), paid("1")]
        after = before + [row("2", day="22/09/2026"), row("2", day="22/09/2026", item="Scotch Egg", qty=3),
                          row("2", day="22/09/2026", item="Cheese Souffle", qty=2), paid("2", day="22/09/2026")]
        a, b = score(before), score(after)
        pd.testing.assert_frame_equal(board(a), board(b))
        rolling = category_leaderboard(b, WEEKS[0], ROSTER, COMPETITORS).set_index("Display")
        self.assertEqual(rolling.loc["Alpha", "target_units"], 4)
        self.assertEqual(rolling.loc["Alpha", "eligible_tables"], 2)
        self.assertEqual(board(b, WEEKS[1]).loc["Alpha", "target_units"], 2)

    def test_no_retroactive_starters_and_course_exclusions(self):
        r = score([row(), row(item="Houmous &Falafel"), row(item="Chicken Wings"),
                   row(item="Cheese Selection"), paid()])
        self.assertTrue(r.credits.empty)
        r = score([row(day="22/09/2026"), row(day="22/09/2026", item="Houmous &Falafel"),
                   paid(day="22/09/2026")])
        self.assertEqual(board(r, WEEKS[1]).loc["Alpha", "target_units"], 1)

    def test_three_people_one_opportunity_not_one_per_portion(self):
        r = score([row(), row(employee="B", item="Scotch Egg", qty=7),
                   row(employee="M", item="Olives Rustica", qty=2), paid()])
        b = board(r)
        self.assertAlmostEqual(b["eligible_tables"].sum(), 1)
        for person in ("Alpha", "Beta", "Manager"):
            self.assertAlmostEqual(b.loc[person, "eligible_tables"], 1/3)
        self.assertEqual(b.loc["Beta", "target_units"], 7)
        self.assertEqual(b.loc["Alpha", "target_units"], 0)

    def test_owner_selling_own_items_not_double_counted(self):
        r = score([row(), row(item="Scotch Egg"), row(employee="A alias", item="Olives Rustica"), paid()])
        self.assertEqual(board(r).loc["Alpha", "eligible_tables"], 1)

    def test_outside_roster_share_not_redistributed(self):
        r = score([row(), row(employee="Unknown", item="Scotch Egg"), paid()])
        self.assertEqual(board(r).loc["Alpha", "eligible_tables"], .5)
        r = score([row(employee="Unknown"), row(employee="Unknown", item="Scotch Egg"),
                   row(item="Olives Rustica"), paid()])
        self.assertEqual(board(r).loc["Alpha", "eligible_tables"], .5)

    def test_sharing_is_category_specific(self):
        r = score([row(day="22/09/2026"), row(day="22/09/2026", employee="B", item="Scotch Egg"),
                   row(day="22/09/2026", employee="M", item="Cheese Souffle"), paid(day="22/09/2026")])
        nib = category_leaderboard(r, WEEKS[0], ROSTER, COMPETITORS).set_index("Display")
        starter = board(r, WEEKS[1])
        self.assertEqual(nib.loc["Beta", "eligible_tables"], .5)
        self.assertEqual(nib.loc["Manager", "eligible_tables"], 0)
        self.assertEqual(starter.loc["Manager", "eligible_tables"], .5)
        self.assertEqual(starter.loc["Beta", "eligible_tables"], 0)

    def test_week_five_nibbles_continue_and_equal_weight_categories(self):
        menus = copy.deepcopy(DEFAULT_MENUS)
        menus[0]["end"] = "2026-10-18"
        for n in range(3, 6):
            menus[0]["targets"][str(n)] = [f"Category {n}"]
        rows = []
        for i in range(15):
            a = str(i)
            rows.extend([row(a, day="18/10/2026"), paid(a, day="18/10/2026")])
            for item in ("Scotch Egg", "Cheese Souffle", "Category 3", "Category 4", "Category 5"):
                rows.append(row(a, day="18/10/2026", item=item))
        r = score(rows, menus=menus)
        total = overall_leaderboard(r, ROSTER, COMPETITORS).set_index("Display")
        self.assertEqual(total.loc["Alpha", "overall_score"], 100)
        self.assertEqual(total.loc["Manager", "overall_score"], 0)
        for n in range(1, 6):
            self.assertEqual(total.loc["Alpha", f"category_{n}"], 20)
        self.assertEqual(board(r).loc["Alpha", "target_units"], 0)

    def test_fractional_minimum_not_rounded_up(self):
        rows = []
        for i in range(29):
            a = str(i)
            rows += [row(a), row(a, employee="B", item="Scotch Egg"), paid(a)]
        self.assertEqual(board(score(rows)).loc["Beta", "status"], "Building sample")
        rows += [row("30"), row("30", employee="B", item="Scotch Egg"), paid("30")]
        self.assertEqual(board(score(rows)).loc["Beta", "status"], "Qualified")

    def test_old_category_correction_does_not_block_new_weekly_category(self):
        r = score([row(day="22/09/2026"), row(day="22/09/2026", item="Scotch Egg"),
                   row(day="22/09/2026", item="Scotch Egg", kind="Clear", qty=-1),
                   row(day="22/09/2026", item="Cheese Souffle"), paid(day="22/09/2026")])
        self.assertEqual(board(r, WEEKS[1]).loc["Alpha", "target_units"], 1)
        rolling = category_leaderboard(r, WEEKS[0], ROSTER, COMPETITORS)
        self.assertTrue(rolling["provisional"].all())
        self.assertEqual(rolling["target_units"].sum(), 0)

    def test_unconfirmed_continuing_mapping_fails_closed(self):
        menus = copy.deepcopy(DEFAULT_MENUS)
        menus.append({"name": "new", "start": "2026-09-24", "end": "2026-09-27",
                      "mains": {"Burger & Fries": 1}, "targets": {"2": ["Cheese Souffle"]}})
        r = score([row(day="24/09/2026"), row(day="24/09/2026", item="Scotch Egg"),
                   row(day="24/09/2026", item="Cheese Souffle"), paid(day="24/09/2026")], menus=menus)
        self.assertEqual(board(r, WEEKS[1]).loc["Alpha", "target_units"], 1)
        self.assertEqual(category_leaderboard(r, WEEKS[0], ROSTER, COMPETITORS)["target_units"].sum(), 0)

    def completed_state(self):
        rows = [row("1"), row("1", item="Scotch Egg"), paid("1"),
                row("2", day="20/09/2026"), paid("2", day="20/09/2026")]
        return replacement_state(empty_state(), pd.DataFrame(rows).to_csv(index=False).encode(),
                                 today=date(2026, 9, 21))[0]

    def test_frozen_week_survives_upload_menu_and_review_changes(self):
        state = self.completed_state()
        frozen = finalise_week(state, WEEKS[0], date(2026, 9, 21), True, ROSTER, COMPETITORS)
        original = copy.deepcopy(frozen["weekly_results"])
        frozen["menus"][0]["targets"]["1"] = []
        r = score_accounts(frame([row(), paid()]), frozen["menus"], roster=ROSTER)
        self.assertEqual(weekly_result(frozen, r, WEEKS[0], ROSTER, COMPETITORS)
                         .set_index("Display").loc["Alpha", "target_units"], 1)
        restored = decode(encode(frozen))
        self.assertEqual(restored["weekly_results"], original)
        replacement, _ = replacement_state(restored, pd.DataFrame([row(), paid()]).to_csv(index=False).encode(),
                                           True, date(2026, 9, 22))
        self.assertEqual(replacement["weekly_results"], original)
        with self.assertRaises(ValueError):
            finalise_week(frozen, WEEKS[0], date(2026, 9, 22), True, ROSTER, COMPETITORS)

    def test_freeze_requires_end_complete_data_and_review(self):
        state = self.completed_state()
        for today, confirmed in [(date(2026, 9, 20), True), (date(2026, 9, 21), False)]:
            with self.assertRaises(ValueError):
                finalise_week(state, WEEKS[0], today, confirmed, ROSTER, COMPETITORS)
        state["upload"]["end"] = "2026-09-19"
        with self.assertRaises(ValueError):
            finalise_week(state, WEEKS[0], date(2026, 9, 21), True, ROSTER, COMPETITORS)
        state = self.completed_state()
        state["csv"] = pd.DataFrame([row(), row("2", day="20/09/2026")]).to_csv(index=False)
        with self.assertRaises(ValueError):
            finalise_week(state, WEEKS[0], date(2026, 9, 21), True, ROSTER, COMPETITORS)

    def test_legacy_snapshot_migrates_without_data_loss(self):
        state = self.completed_state()
        del state["weekly_results"]
        loaded = decode(encode(state))
        self.assertEqual(loaded["csv"], state["csv"])
        self.assertEqual(loaded["weekly_results"], {})


if __name__ == "__main__":
    unittest.main()
