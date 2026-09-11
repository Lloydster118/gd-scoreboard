"""Quick smoke test — runs processing over the real pre-promo CSV."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.processing import (
    load_transactions, clean_sales, weekly_leaderboard,
    overall_leaderboard, diagnostics,
)
from src.config import WEEKS, ELIGIBLE_ROSTER

CSV = Path("/home/user/workspace/branched_contexts/a7f3e32e-e844-480e-8908-726ddd7ec370/"
           "attachments/Detailed-Transaction-Report-5-week-pre-promo_"
           "Detailed-Transaction-Report_Detailed-Transaction.csv")

print(f"Loading {CSV.name} ...")
df = load_transactions(CSV)
print(f"Loaded {len(df):,} rows")

diag = diagnostics(df)
print("\nDiagnostics:")
for k, v in diag.items():
    if k != "unmapped_employees":
        print(f"  {k}: {v}")
print(f"  unmapped_employees: {len(diag['unmapped_employees'])} names (sample: {diag['unmapped_employees'][:5]})")

sales = clean_sales(df)
print(f"\nEligible-roster sales rows: {len(sales):,}")
print(f"Distinct employees in sales: {sorted(sales['Display'].unique())}")

# Simulate: pretend the CSV IS week 1 of the campaign so we get non-empty output.
# We'll temporarily re-scope WEEKS[0] to the CSV's actual date range.
from src import config
first_week = config.WEEKS[0]
new_first = type(first_week)(
    number=first_week.number, name=first_week.name,
    start=df["Date"].min(), end=df["Date"].max(),
    items=first_week.items,
)
config.WEEKS = (new_first,) + config.WEEKS[1:]

print(f"\n--- Simulated Week 1 leaderboard ({new_first.start} → {new_first.end}) ---")
board = weekly_leaderboard(sales, new_first)
print(board.to_string(index=False))

print("\n--- Overall leaderboard (simulated) ---")
ov = overall_leaderboard(sales)
print(ov.to_string(index=False))
print("\n✅ smoke test complete")
