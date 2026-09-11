# George & Dragon — Upsell Incentive Scoreboard

A live, Zonal-driven Streamlit dashboard powering a five-week floor-team upselling
incentive at [The George & Dragon, Marlow](https://www.heartwoodcollection.co.uk/pubs/george-and-dragon-marlow/) —
part of the Heartwood Collection.

The goal of the incentive: **increase spend per head without turning service into a hard sell**.
The dashboard's job: **decide the £10 weekly and £50 overall winners fairly, using only
the data Zonal already produces, and defensibly enough that nobody can dispute the result.**

> The repository is public. The dataset is not. Only the code, methodology, and
> synthetic-shaped documentation are shared here.

---

## The problem

Running a "who sold the most?" competition in a restaurant is trivially unfair. Servers work
different contracted hours, hold different sections, and cover different services. A raw
sales-volume leaderboard rewards whoever was rostered most, not whoever recommended best.

The pre-promo baseline dataset made this obvious. Across a five-week window, the largest-hours
server on the team recorded roughly **4× more sales** than the smallest-hours server — not
because of skill, but purely because of floor exposure. Any incentive the top-of-rota staff could
win off that head-start would immediately lose trust with the rest of the team.

## The solution

Three design principles, in priority order:

1. **Normalise by opportunity, not activity.** Rank by conversion rate at the *table* level,
   not raw units, not per-hour, not per-cover.
2. **Small samples are quarantined, not scored.** A server with 3 tables isn't allowed to
   win off a lucky order.
3. **Absence is `N/A`, not zero.** A holiday week is excluded from the average, not counted
   as bottom rank.

### Scoring maths

For each week and each server:

```
opportunities  = distinct (Order No, Employee) pairs in the week's date range
                 with Covers > 0 and Type = 'Sale'
hits           = distinct (Order No, Employee) pairs where at least one line
                 is in that week's target-item list
conversion (%) = 100 × hits / opportunities
```

A table with three qualifying items counts as **one** hit — the metric measures whether the
server *created the opportunity*, not how many items were ordered off it.

Weekly points are awarded to *qualified* servers only (≥ 20 eligible tables that week):

| Rank | Points |
|------|-------:|
| 1    | 80     |
| 2    | 70     |
| ...  | ...    |
| 8    | 10     |
| Qualified below 8th | 10 |
| Building sample     | 0  |
| No recorded shift   | N/A |

The overall £50 winner is decided on **mean points across qualified weeks**, requiring at least
**4 of 5** qualified weeks to be eligible. This prevents a single big week from carrying someone
who then disappears, while also excluding N/A weeks from the denominator so approved absence
never hurts anyone.

## Data model

Input is Zonal's **Detailed Transaction Report** CSV (~59k rows over 5 weeks in the baseline
sample). The processing pipeline is deliberately minimal:

1. **Parse** — enforce required columns, coerce types (dates as `dayfirst`, sales as float,
   quantities as int).
2. **Filter** — keep `Type == 'Sale'`, positive `Quantity`, and only employees in the
   eligible-roster mapping. Everything else (voids, waste, payments, merges, non-competitors)
   is stripped.
3. **Build table view** — one row per `(Order No, Employee)`, taking `max(Covers)` because
   Zonal repeats the cover count on every line of the order.
4. **Weekly aggregate** — for the active week's date window, compute opportunities, hits,
   conversion %, target revenue, and target units per server; assign status; rank the
   qualified subset; award points.
5. **Overall aggregate** — average points across qualified weeks, gate on
   `qualified_weeks >= MIN_QUALIFIED_WEEKS`.

All logic lives in `src/processing.py` (~250 lines, zero magic). All tunables live in
`src/config.py`.

## Design decisions

Choices I made — and why — that you can push back on:

| Decision | Chose | Rejected alternative | Why |
|---|---|---|---|
| Denominator | Eligible tables served | Covers, hours, shifts | Servers control tables, not who sits at them. Per-cover penalises servers assigned bigger groups; per-hour requires a manual timesheet. Tables are what Zonal actually attributes. |
| Hit counting | Distinct tables with ≥1 target item | Total target units sold | Multi-item tables shouldn't 4× a lucky order. Measures *did you make the sell happen*, not *how big was that guest's appetite*. |
| Small-sample rule | Hard 20-table minimum for weekly prize | Rolling z-score or Bayesian shrinkage | Legibility. The team needs to understand the rule at pre-shift. "20 tables" beats "posterior distribution". |
| Absence handling | Exclude the week from the mean | Impute zero, or a "typical" score | Zero punishes holiday. Imputation invents data. Excluding matches how any reasonable person would grade someone who wasn't there. |
| Prize cadence | £10 weekly + £50 overall | Single £50 at the end | Weekly urgency drives daily behaviour. Overall prize rewards habit-building across all 5 themes. |
| Overall metric | Mean points, min 4 qualified weeks | Sum of points | Sum favours whoever worked the most weeks. Mean + minimum-participation floor keeps it fair without letting anyone win off one big week. |
| Cheese Selection | Present in both Week 1 and Week 4 taxonomy | Restrict to one theme | Different selling moments (shared board vs. dessert alternative). Each active week's leaderboard is independent, so no sale is double-counted inside a single competition. |

## Repository layout

```
gd-scoreboard/
├── app.py                 # Streamlit UI (tabs, KPIs, styled leaderboard)
├── src/
│   ├── config.py          # Roster, weekly themes, thresholds, prize values
│   └── processing.py      # Parse → clean → table view → weekly → overall
├── tests/
│   └── smoke.py           # Sanity check against a real CSV (excluded from repo)
├── requirements.txt
├── .streamlit/config.toml # Dark theme
└── README.md
```

## Running locally

```bash
git clone https://github.com/Lloydster118/gd-scoreboard.git
cd gd-scoreboard
pip install -r requirements.txt
streamlit run app.py
```

Then upload a Zonal Detailed Transaction Report CSV via the sidebar. The app processes
the file in-memory and renders the leaderboard — nothing is persisted server-side, so
this is safe to run on Streamlit Community Cloud with public URL access.

## Deployment

Hosted on [Streamlit Community Cloud](https://streamlit.io/cloud), which reads directly
from this GitHub repo. To deploy:

1. Fork/clone this repo.
2. Sign into Streamlit Cloud with GitHub.
3. Point a new app at `app.py` on the `main` branch.
4. No secrets, no environment variables — all data enters via the uploader.

## What's *not* in this repo

- Any Zonal export, real staff sales, or menu-item revenue data.
- Real employee names beyond the author's own.
- The actual pub's operational config beyond what's needed to explain the design.

Anyone can clone the repo, spin up the app, and see exactly how the incentive is calculated —
they just need their own CSV to see any numbers.

## Author

Harry Lloyd — Front-of-House Supervisor at The George & Dragon and a BNU BSc (Hons)
Computer Science with AI graduate targeting data-science and AI-engineering roles.
This project is one of several portfolio pieces that turn a real operational problem
into a small, defensible data product.

- LinkedIn / portfolio: (add your links)
- Contact: via GitHub `@Lloydster118`
