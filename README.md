# George & Dragon Upsell Scoreboard

A Streamlit scoreboard for a five-week hospitality incentive. Public source code;
private transaction snapshots and staff configuration.

Additional staff can be configured privately in the saved snapshot's optional
`roster_additions` list using `display`, exact till `aliases`, and a boolean
`competitor` flag. These supplement the Streamlit Secrets roster without placing
employee names in public source. Alias conflicts and changes to existing prize
eligibility fail closed. Additions rescore existing exports for open weekly and
ongoing category standings; previously frozen weekly results remain unchanged.

## Scoring

The weekly metric is:

```text
valid qualifying portions / shared eligible table opportunities × 100
```

This is **portions per 100 tables**, not conversion percentage. Three qualifying
portions on one account earn three credits. Scores above 100 are legitimate.

- Account ID separates sittings at the same physical table.
- Main-course portions identify the ordinary account owner. Aliases resolve to one
  employee. Chateaubriand has ownership weight two.
- Target portions go to their sale-entry employee, regardless of account ownership.
- Each accepted account contributes exactly one opportunity per category, split
  equally between its main-course owner and distinct qualifying sellers. Two people
  get 0.5 each; three get one third. Owner/seller aliases are not counted twice.
- Managers and outside-roster sellers keep their share; it is not redistributed.
  Only roster members appear on the public board, and only competitors can win.
- Zero-target accounts still enter the assigned owner's denominator.
- Zero assigned accounts means an unavailable rate, not infinity or an invented zero.
- The minimum is 15 shared opportunities in the scoring window. Qualified ranks earn
  80/70/60/50/40/30/20/10 points; qualified lower ranks earn 10. Other weeks earn zero.
- Observers never earn prize points. Overall standings use five rolling categories,
  not the frozen weekly results.
- Target revenue breaks rate ties. Exact ties share a rank and need prize review.

The rate measures sales contribution relative to shared workload. Cross-table
selling shares the account opportunity; large groups still count as one account.
It is deliberately not a covers-adjusted or own-table conversion measure.

### Weekly prizes and sustained habits

- The £10 weekly competition uses only the launch week's sales, Monday to Sunday
  in UK calendar dates. Future sales cannot enter that weekly window.
- After the week ends, Admin can freeze the weekly board once the complete week's
  export is uploaded and its account reviews are resolved. Until then it is marked
  closed, pending finalisation, not a confirmed winner.
- A frozen board is stored in the private snapshot, with timestamp and source hash.
  Uploads, menu changes and account reviews cannot overwrite it. No automatic prize
  payment occurs; exact ties require manual prize review.
- Each category also continues from its launch through 18 October for the £50
  overall competition. Nibbles run from Week 1 to Week 5; starters from Week 2 to
  Week 5, with no retrospective Week 1 starter credit.
- Cumulative category portions / cumulative shared opportunities determines its
  rank, using the same 15-opportunity minimum and revenue tie-break.
- Each category's ranking points / 80 × 20 contributes up to 20 overall points:
  five equal 20% weights, 100 maximum. Not-yet-launched, missing or below-threshold
  categories contribute zero; weights are never redistributed.
- Weekly and rolling category reviews are evaluated independently. A later nibble
  correction can hold rolling nibble credit without blocking valid weekly starters.
- Snack-only/unresolved-owner accounts still need review: item credit may remain
  visible, but the app does not invent a main-course opportunity.

## Review, not guesswork

Payments from any employee are matched by Account ID. Payment activity is evidence,
not proof of final bill settlement. Deposit-only accounts require confirmation.

SF-tagged staff food is excluded. Main-course ties, unknown ownership, multiple table
numbers, explicit preorders and relevant corrections are visible in the private admin
review queue. Missing/under-entered/inconsistent covers are informational notes, not
ownership vetoes. A known main-course account is audited even with zero covers or no
table number: its Account ID identifies the visit. Large party size alone does not
exclude an ordinary account.

Relevant means a recognised main or target for the category being evaluated.
Unrelated drink/dessert transfers and another week's starter corrections do not
discard valid current-week food scores. Refund/reversal evidence remains a blocker.
Relevant voids/corrections and transfers are **held**, not blindly subtracted or
double-counted. The supervisor supplies verified final sales for the affected
account, preserving the original sale-entry employee. Other valid accounts continue
to score. Unresolved ownership keeps item credits but holds that denominator, so
rankings remain explicitly provisional until reviewed.

The public column says "Table opportunities", showing fractional shares, not total
tables served. Chicken Wings and Houmous &Falafel are excluded from nibbles and
retained in starters. Cheese Selection is not a nibble; later dessert mappings
must be confirmed before their launch.

Reviews require an evidence note and are bound to the full account fingerprint.
A changed account expires its previous review. Private snapshot history records
review changes. Final prize decisions must wait for relevant review resolution.

## Dated menus

`src/menus.py` contains the confirmed outgoing main and target mappings through
23 September 2026. The 13 confirmed starter names replace the old inaccurate list.

No menu is assumed from 24 September onward; later-week targets are unconfirmed.
Every later menu period must include ALL launched category mappings, not only the
new weekly theme. Missing mappings fail closed for the affected rolling category.
Admin can add exact product mappings and non-overlapping effective dates without
editing code. Scoring and ownership use the sale date, preserving historical rules.
Unknown products are catalogued privately for review; drinks/modifiers will also
appear, so absence from the mapping does not automatically mean an error.

Menu changes currently have **whole-day resolution**. A mid-day transition or
concurrent menus needs a further explicit rule rather than guessing.

## Upload safety and storage

Upload one **complete cumulative** Zonal export beginning 14 September, replacing
the prior snapshot. Daily incremental uploads are not supported.

- Genuine identical rows are preserved.
- Identical cumulative reuploads are idempotent.
- Missing columns, bad dates/numbers, missing Account IDs and future dates are rejected.
- An earlier end date, missing previous accounts or reduced daily row counts require
  explicit confirmation of a corrected replacement.
- The uploader must attest the file is complete. Counts cannot prove completeness.
- Validation occurs before saving. Failed validation or failed storage leaves the
  last good snapshot intact.
- Stored CSV contains only scoring fields. Customer names, free-form details and
  unused payment/customer fields are discarded.

Production storage is a **separate private GitHub repository**, holding one compressed
snapshot (`state.json.gz`) with CSV, dated menus, reviews, frozen weekly boards
and upload metadata. Existing snapshots migrate without changing their source data.
Git history supplies recovery versions. SHA-based writes reject concurrent updates.
The backend refuses a public data repository. GitHub authentication errors and
corrupt snapshots are shown as unavailable data, not an empty successful scoreboard.

This is low-volume operational storage, not a high-write transactional database.
Anyone with private-repository access can read its snapshot history. Restrict access
and set an appropriate retention/deletion policy before keeping data indefinitely.

## Deployment

Keep the existing Streamlit app connected to `main` / `app.py`.

1. Create a separate **private** repository for data.
2. Create a fine-grained GitHub token restricted to that repository, with
   **Contents: read and write** and the automatically required Metadata access.
   Set a suitable expiry and rotate before expiry.
3. Add the following directly in **Streamlit Cloud Secrets**. Never paste a token
   into chat, public source, a commit, a screenshot or a log.

```toml
admin_pin = "REPLACE_WITH_A_STRONG_PRIVATE_VALUE"

[storage]
repo = "Lloydster118/gd-scoreboard-data"
token = "REPLACE_DIRECTLY_IN_STREAMLIT_SECRETS"

# Keep the existing private roster configuration as well:
[[roster]]
display = "Server 1"
aliases = ["Server One"]
competitor = true
```

Keep `admin_pin` as a top-level key before TOML table declarations.
Do not replace the real roster with the example. Existing flat `[roster]` mappings
are also supported, but array-of-tables supports manager observers and aliases.

4. Confirm Admin reports the private roster, and upload the current cumulative file.
5. Review flagged accounts; add the new seasonal menu when confirmed.
6. Restart the app and confirm upload timestamp, coverage and scores persist.

Admin fails closed when no PIN exists; there is no hard-coded default. The PIN
gate is not multi-user SSO. Session-level attempt throttling is basic protection,
not a substitute for a strong secret or network-wide authentication controls.

Without storage configuration, the app can preview a surviving legacy local CSV,
but new uploads are disabled. Legacy server files may be lost on a redeploy.
**Back up/re-export the current cumulative data before promoting this release.**

## Development and tests

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
streamlit run app.py
```

For local development only:

```toml
admin_pin = "LOCAL_TEST_ONLY"
[storage]
development_local = true
```

Local snapshots are atomic but not durable on Streamlit Cloud. Production should
use the private backend. `.streamlit/secrets.toml`, transaction data and real roster
files are git-ignored. Tests and CI use synthetic records only.

## Layout

- `src/processing.py`: strict CSV parsing, account evidence, scoring and ranks.
- `src/menus.py`: effective-dated exact product taxonomy.
- `src/storage.py`: versioned private snapshots and replacement validation.
- `src/config.py`: private roster loading, weekly schedule and prize settings.
- `app.py`: public standings and PIN-gated review/configuration/upload controls.
- `tests/`: portable synthetic regression and Streamlit integration tests.

## Author

Harry Lloyd, Front-of-House Supervisor and BNU Computer Science with AI graduate.
This project turns an operational problem into a documented, testable data product.
