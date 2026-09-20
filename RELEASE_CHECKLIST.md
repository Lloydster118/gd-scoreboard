# Account-based scoring release

## Automated coverage

Rolling-habits release validation: 73 tests passed locally, including three Streamlit
AppTest cases. New coverage includes equal-weight rolling categories through Week 5,
no retroactive credit, fractional opportunities and category-specific sharing,
outside-roster shares, immutable weekly snapshots, safe legacy-state migration,
and the actual admin freeze button with durable read-back.
Read-only production audit of the saved 14–19 September export confirms all previous
account decisions, roster portion totals and reviewed exceptions are preserved.
Total opportunity shares equal the number of accepted accounts.

Earlier release validation: 59 tests passed locally, including two Streamlit AppTest cases.
The denominator follow-up covers zero/missing covers, missing table identifiers,
unrelated transfers, week-specific corrections, and Week 1/2 wings eligibility.
The 14–18 September exports were also parsed structurally: all 5,218 rows retained,
213 distinct candidate dining accounts and no duplicate account ownership records.
That structural test used anonymised all-staff mappings, not the production roster.
It is not a claim that the real account-review decisions are resolved.

- Separate sittings, multiple order numbers and payments per account.
- Repeated identical rows, quantity credit, cross-table selling and alias merging.
- Manager-owned accounts, zero-target accounts and zero-denominator rates.
- Majority main ownership, sharing weights, PF zero-price mains and SF exclusion.
- Ties, unknown mains, large walk-ins, cover anomalies and explicit preorders.
- Missing payment, deposit review, relevant corrections and transfer holds.
- Reviewed final sales and invalidation when account evidence changes.
- Menu switchover, historical preservation and unconfirmed future weeks.
- Minimum sample, observer exclusion, weekly and overall points.
- Cumulative replacement, reupload idempotency and malformed export rejection.
- Private-only storage, expected-version writes, restart recovery and data minimisation.
- App empty state, populated state, incorrect PIN, unlock and lock.

## Interface QA

Check the leaderboard and all tabs at desktop and mobile widths.
Use synthetic data only in screenshots and the preview.

Exercise cumulative upload and an identical reupload; invalid-file rejection;
account review and save; invalid/overlapping menu rejection; valid dated-menu save;
backup download; admin logout. Check that no private account details appear outside Admin.

Browser checks completed on synthetic data: desktop/mobile layout, all public tabs,
admin login/logout, invalid upload rejection, identical reupload, backup download,
review save, overlapping-menu rejection and valid menu save.
Private GitHub initialization also passed a save/read-back and missing-SHA conflict check.
Real transaction data is stored only in the private data repository, never in the
public source repository or synthetic QA preview.

## Production rollout gate

1. Keep the release branch separate from live `main` until storage access is ready.
2. Keep a fresh cumulative Zonal export from 14 September available for re-upload.
3. Configure the private-data repository token directly in Streamlit Secrets.
4. Preserve the existing real private roster and strong top-level admin PIN.
5. Merge only after the storage setup is ready; CI must be green.
6. Upload the cumulative export and check coverage and roster source.
7. Restart the live app and verify snapshot persistence.
8. Resolve review accounts before paying prizes.
9. Add confirmed seasonal mappings effective 24 September. Later weeks remain unconfigured.

## Limits

Synthetic tests and the historical exports cannot establish final ownership,
settlement or correction semantics for ambiguous real accounts.
The engine exposes those decisions for documented review instead of inventing them.
The 15-opportunity threshold now uses fractional shares, not rounded account counts.
Menu transitions are date-level; part-day transitions need additional rules.
