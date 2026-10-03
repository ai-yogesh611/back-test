# User Guide — how to use every page, tab and control

The **operator** manual: what to click, in which order, and why. Written from the
code on 2026-10-04 (branch `main`).

- Developer reference (templates, JS files, endpoints): [WEB-UI.md](WEB-UI.md)
- Why the platform behaves the way it does: [project-overview.md](project-overview.md)
- Plain-language architecture: [ARCHITECTURE.md](ARCHITECTURE.md)

**How to read this guide.** Each page section starts with *Use it when…*, then the
*header controls*, then one subsection per tab: what the tab is for and the steps
to use it. Anything shown in `code` is a literal button/field label from the UI.

---

## 0. Start the app

```powershell
# Windows (see "how to run.md" in the repo root for detail)
$env:PYTHONPATH = "…\src"
python -m backtest.web.app --host 0.0.0.0 --port 5000
```

Open <http://127.0.0.1:5000> and check `GET /health` returns
`{"status":"ok","source":"…"}`. Useful switches (all also env vars —
`.env.example`): `--source` (`synthetic` for a credential-free first run, `db`
for real cached bars), `--log-level DEBUG`, `--log-file logs/app.log`,
`--currency USD`, `--replay-speed 5`.

### A safe first 15 minutes

1. **`Market data`** — if the instrument you want is missing from the dropdowns,
   fetch it first (see §4.2). A symbol must have data before Research pages list it.
2. **`Backtest`** — pick a strategy + instrument, `Run backtest`, read the metric
   cards and the three chart tabs.
3. **`Compare strategies`** — put two strategies on the same window and see which
   one actually differs.
4. **`Portfolio` → PAPER bucket** — `Add instance` to paper-trade it on a live
   clock. Nothing here touches money.
5. Only after that: `Settings` (cost models, segments) and the LIVE bucket, which
   requires explicit arming (§4.3).

---

## 1. Global chrome — the parts that are on every page

### 1.1 Sidebar navigation

Three groups, eleven pages:

| Group | Page | URL |
|-------|------|-----|
| **Workspace** | Portfolio | `/portfolio` (also `/portfolio/paper`, `/portfolio/live`) |
| | Analytics | `/analytics` |
| | Risk management | `/risk` |
| | P&L reports | `/reporting` |
| **Research** | Backtest | `/backtest` (`/` redirects here) |
| | Compare strategies | `/compare` |
| | Optimization | `/optimize` |
| | Strategy builder | `/strategy-builder` |
| **Operations** | Engine playground | `/forward` |
| | Market data | `/data` |
| | Settings | `/settings` |

### 1.2 Top bar

| Control | What it does | How to use it |
|---------|--------------|----------------|
| **Jump to…** (`Ctrl K`) | Page search dialog — navigation only, it never runs or deploys anything | Type a page name, `↑ ↓` to move, `Enter` to open, `Esc` to close |
| **Environment chip** (e.g. `Historical data`) | The *price data source* this deployment is running on — **not** an execution-mode indicator | Read it before trusting a result: synthetic vs real bars change everything |
| **Market status chip** | `NSE OPEN` / `PRE-OPEN` / `NSE CLOSED · WEEKEND` / `NSE CLOSED · <holiday>`; tooltip carries next open and calendar warnings | Use it to interpret anything time-related (countdowns, "last update") |
| **Halt pill** | Portfolio risk state at a glance | On a Portfolio page it opens the *Risk & intelligence* tab; elsewhere it navigates to `/risk` |
| **Broker chip** | Opens the **Broker Connections** modal: every broker's session, its segment, `Login` / `Logout` / `Reconcile` | Log a broker in here before fetching data or arming live |
| **Data fetch indicator** | Appears only while a historical fetch runs (`n/m`) | Click it to stop the running fetch |
| **Theme toggle** | Light/dark; preference stored as `trading-workspace.theme` | Cosmetic — navigation and theme changes never place an order |

### 1.3 Risk strip (always visible, below the top bar)

The survival layer: `Feed` state · `Daily Loss (today)` · `DD` (drawdown) ·
`Deployed` · `Pos` (open positions), each with a mini bar.

- **`Details`** — expands a last-6-hours breakdown chart plus the top losers.
- **`Flatten all`** — closes every open position across all runners; requires the
  `Yes, flatten everything` confirmation.

On a non-trading day the daily-loss label reads *"Daily Loss (prev session)"* —
it is not silently showing today's zero.

### 1.4 Alert widget (bottom right)

Rendered when `PORTFOLIO_INTELLIGENCE_ENABLED` (the default). A minimized pill
with the alert count and severity mix; expand it for the list (`View Details`,
`Dismiss`), open a detail modal for *what it means / contributing strategies /
typical responses*. Polls `/api/alerts/active` every 3 s (15 s while the page is
hidden) and only re-renders when the alert version changes. No position-changing
buttons — see [ALERTS-GUIDE.md](ALERTS-GUIDE.md).

### 1.5 The Workspace guide (`?` key)

The `Workspace guide` button (sidebar footer, or `?`) opens the three-step
workflow dialog: **01 Build evidence** (backtest/compare/optimize) → **02 Practice
without exposure** (paper bucket) → **03 Prepare for live trading** (broker
connections, cost models, segment limits).

### 1.6 Errors and toasts

Everything surfaces as a toast. Every `/api` error includes a request id
(`… [req 979be616]`) — grep that id in `logs/app.log` to find the exact
traceback. See [LOGGING.md](LOGGING.md).

### 1.7 Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Ctrl K` | Jump to… page search |
| `?` | Workspace guide |
| `↑ ↓` / `Enter` / `Esc` | Move / open / close in either dialog |

---

## 2. Workspace group

### 2.1 Portfolio — `/portfolio`, `/portfolio/paper`, `/portfolio/live`

**Use it when…** you want to see what is held, steer it, or put a strategy to work.
This is the trading console, not just a monitor: automation keeps trading and you
can intervene at any moment.

**Three views of one system:**

| Page | Shows |
|------|-------|
| `/portfolio` | Both buckets side by side: PAPER and LIVE cards (equity, allocated, daily/realised P&L, instances, running, open positions, mini loss-limit/drawdown gauges), a collapsible **Global risk** header, the full command center embedded for LIVE, and a PAPER summary table |
| `/portfolio/paper` | Paper bucket only — simulated fills, live instances never render here |
| `/portfolio/live` | Live bucket only — real-money instances; paper never renders here |

Each bucket page carries the banner *"Scoping this command center to the PAPER/LIVE
bucket only — other buckets never appear here"*, and the metric cards, bulk actions
and positions list are all filtered to that bucket.

#### Command center header

- Metric cards: **Total Capital · Total Equity · Deployed · Daily P&L · Realized
  P&L · Open Positions · Daily Loss Limit** (progress bar).
- **`+ Add instance`** — opens the spawn modal (below).
- **`⏸ Pause all` / `▶ Resume all`** — bulk control, scoped to the current bucket.
- **`⬇ Export charts`** — writes a PnL-vs-spot PNG + CSV into `charts/` for every
  live option runner (also runs automatically at 15:30 IST).
- **`⏹ Emergency flatten`** — closes **both** books (paper and live) after
  confirmation; hidden on the paper-bucket page so a sandbox click cannot masquerade
  as a real-money action.
- **Circuit-breaker banner** (`CIRCUIT BREAKER HALTED` + `Reset & Resume`) appears
  when a global breaker has tripped; concentration warnings appear above the matrix.

#### Strategy instances (the matrix)

Search box, status filter (`Running / Paused / Stopped / Error`) and sort
(Daily P&L, Open P&L, Capital Allocation, Name, Status). Row actions:

| Button | Action |
|--------|--------|
| `⏸` / `▶` | Pause / resume this runner |
| `⏹` | Stop it |
| `🔍` | Deep dive into the instance |
| `🗑` | Remove the instance — **flattens its book first**, then deletes the row |
| `⟳` | Refresh marks (option rows) |

An empty bucket offers `Add your first instance`.

#### Add instance (spawn) modal

Routing only — trading logic lives in **Playbooks**, never here. Fields (some are
conditional on the strategy's instrument kind):

1. **Instance name** (optional; auto-generates `strategy·instrument·timeframe`)
2. **Strategy** — with a hint line describing it
3. **Playbook** (optional) — pick a declarative option config to spawn from
4. **Target type**, **Timeframe**, **Bucket mode** (`paper` / `live`), **Data source**
5. **Segment** and **Broker** — shown only when segments are configured
6. **Instrument** (SymbolPicker: `All / Equity / Index / F&O` tabs + search),
   **Lots per leg** (options), **Universe/Pool** + **Max pool positions** (pooled equity)
7. **Allocated capital**, then the strategy's own parameters

`Deploy Instance` creates it. The instrument is derived from the strategy's signal
kind — the form never asks you to invent one.

#### The seven tabs

##### Tab 1 — `Positions` (default)

*What is held right now, with the manual controls.* One flat row per open position;
an option structure shows as a single net row with its legs underneath.

**How to use:**
1. Read `Unrealized P&L`, then `Target` / `Stop` — `—` means no level is armed.
2. Narrow with `Filter symbol / instance…`, or tick `SL/Target only` to hide rows
   with nothing armed.
3. Act from the **Actions** column — each opens a modal that names the row it will
   affect and posts to `/api/portfolio/position/action`:
   - `🛑 Modify SL` / `🎯 Modify Target` — enter a **price** (share price, or net
     premium per unit for options). The *server* validates it against the live mark;
     a refusal is shown verbatim and the modal stays open.
   - `◐ Close 50%` — equity positions only.
   - `✕ Close All` — flatten that position.
4. Manual levels are checked **on every bar, every mark move and every stress
   markdown**, so a manual stop fires even if the strategy never trades again.
   Whichever exit fires first — manual or strategy — wins.
5. `↻ Refresh` re-renders from the latest SSE snapshot.

On a **live** runner a close returns `status: "placed"`: the UI tells you the order
was sent rather than pretending the position is already flat.

##### Tab 2 — `Equity`

On-demand equity snapshot of the session.

1. Press `⟳ Refresh snapshot` (it fetches `/api/portfolio/equity/snapshot`, scoped
   to the bucket on paper/live pages).
2. Read the session stats row and the equity chart.
3. Use the runner table — `Equity · Realized P&L · Today · Win rate ·
   Trades (today/all)` — to see which instance is carrying the book.

##### Tab 3 — `Orders`

The engine's own order ledger: every order's fate plus the slippage actually paid.
The tab badge counts working + rejected orders from the SSE snapshot; the panel
polls every 3 s while visible.

**How to use:**
1. Filter with the status dropdown (`All / Pending / Filled / Rejected / Cancelled`),
   the `Filter symbol / instance…` box, and the row limit (50–500).
2. **Pending** rows carry their **Age** and the app's only `Cancel` button.
3. **Filled** rows show `Ordered @` vs `Filled @` with adverse-positive **Slippage**
   per unit — that column is where cost surprises show up.
4. Tick `⏰ Aging only` to see only orders past the thresholds: **60 s → warn**,
   **5 min → alert**, one audit entry per band.
5. `✎ Amend` (on an order resting **at a venue**) edits quantity / limit price —
   the venue is asked first, and a refusal leaves local state untouched.
   Simulated orders fill or reject inside the same call, so for those use Cancel
   and let the strategy re-arm.

Option structures execute as a unit on the strategy's own option book; their
entries/exits are on the runner signals and the **Trade history** tab.

##### Tab 4 — `Risk & intelligence`

Live risk gauges plus the **Portfolio Intelligence** board — *information only:
the platform calculates and alerts, strategies decide, you override. Nothing here closes a position.*

**Header controls:** bucket filter (`All Buckets / Paper Only / Live Only`),
`↻ Refresh Risk`, `💥 Stress Test (-25%)`, `Reset Breaker`.

**Sections** (each collapsible; expanded state stored per section; deep links
`…?tab=risk#pi-<section>` jump straight to one):

| Section | What you get | Refresh |
|---------|--------------|---------|
| 📊 Portfolio Greeks | Delta/Gamma/Vega/Theta in ₹, per-runner breakdown, scenario revaluation table, warnings | every 1 s |
| 🎯 Concentration | By underlying and by strike cluster (positions, types, holders, exposure) | every 1 s |
| 🔗 Strategy Correlation | Heatmap, group by `runner` or `strategy`, `↻ Recalculate` | every 30 s |
| 🌡️ Market Regime | Current regime, VIX input (`Set India VIX` → `Apply`), strategy regime-fit table | every 30 s |
| 📈 Market Activity | OI / liquidity activity | every 30 s |

Fast data refreshes every 1 s, slow every 30 s — **only while the tab and the page
are visible**. Editing config, per-bucket limits and the audit timeline is done on
the **Risk page** (single authority); this tab links to it.

##### Tab 5 — `Trade history`

**Aggregated closed trades** across every runner — the post-trade view.

1. Choose the kind: `All Kinds / Equity Only / Option Only`, and a row limit.
2. `↻ Refresh` loads them; read `Entry → Exit`, `PnL` and **`Exit Reason / Time`**
   (`auto_square_off`, `expiry_settlement`, `strategy_signal`, …) — the reason is
   what turns a losing trade into a lesson.
3. Summaries sit above the table (`agg-trades-summary`).

##### Tab 6 — `Playbooks`

Declarative, reusable option-strategy configs: structure, strikes policy, exits,
sizing, risk envelope — **no code**. One concept, embedded here rather than on a
page of its own.

**How to use:**
1. `＋ New Playbook` — or open one of the three seeded defaults (NIFTY ATM Bull
   Spread Conservative, NIFTY ATM Long Call/Put Directional, BANKNIFTY Delta 35
   Spread; they cannot be deleted).
2. Each card shows name, underlying · structure · strike · qty, the exit bits, and
   the risk cap with an **`estimated`** badge (V1 moneyness model) plus its version.
3. **`Deploy`** spawns a runner from it (the same `Add instance` path), `Edit`
   bumps the version, `Delete` removes it.
4. The engine owns HOW (live/paper check, margin, lot size from the instrument
   master — never stored, NSE revises lot sizes); the strategy owns WHAT (signal).

Full spec: [ARCHITECTURE-UNIFIED-TRADING.md](ARCHITECTURE-UNIFIED-TRADING.md).

##### Tab 7 — `Activity log`

The audit trail: spawns, bulk actions (tagged with the bucket they applied to),
breaker events, order-aging entries, alert lifecycle. Newest first — use it to
answer *"who/what changed this, and when"* after any surprise.

### 2.2 Analytics — `/analytics`

**Use it when…** you want to know whether an edge still exists, over the
forward/portfolio books (not a backtest). Header: `Compare backtests` link and
`⟳ Refresh`.

**Shared filters:** `Period` (7d / 30d / 90d / 1y / All time) and `Mode`
(All Modes / Paper Only / Live Only).

##### Tab 1 — `Overview`

1. Read **Portfolio Overview**: total return, portfolio Sharpe (with a grade), win
   rate (`5 trades 2W/3L` style detail), max drawdown, and the active-strategy count.
2. **Cross-Broker Portfolio** breaks the same numbers down per broker — useful the
   moment two venues are in play.
3. **Running Strategies** — one row per instance; click a row to open its
   **Strategy Detail** tab.
4. **🚨 Performance Alerts & Edge Degradation** — the suite's own verdict on when
   performance has drifted from what the backtest promised.

##### Tab 2 — `Strategy Detail`

Appears once a strategy is selected (also reachable as `/analytics?strategy=<id>`).
Sections, in reading order: **Strategy Description** → **Key Performance Ratios** →
**Equity Curve & Drawdown** → **Rolling Performance Trend (edge stability)** →
**Monthly Performance Breakdown** → **Trade P&L Distribution & Insights** →
**Recent Executed Trades**. The rolling trend and the monthly table are where a
strategy that is quietly dying shows it first.

See [STRATEGY-PERFORMANCE-ANALYTICS.md](STRATEGY-PERFORMANCE-ANALYTICS.md).

### 2.3 Risk management — `/risk`

**Use it when…** you need the authoritative risk surface: exposure, limit usage,
configuration and the event history.

**Header controls:** bucket selector (`All Buckets / Paper Only / Live Only`),
`Refresh`, `⬇ Export CSV`.

**Global Risk Dashboard** (always at the top): six gauges — Daily Loss Used, Drawdown
("peak protected"), Capital Deployed, Gross Exposure, Open Positions, System Status —
plus circuit-breaker and warning banners, and per-broker / per-segment cards when
more than one broker is configured.

##### Tab 1 — `⚙️ Risk Configuration`

1. Optionally load a preset: `Conservative` / `Balanced` / `Aggressive`.
2. **Global Limits (Supervisor)** — daily loss limit, max drawdown, leverage and
   friends; `💾 Save Global Config`.
3. **Per-Bucket Overrides** — `paper` and `live` are independent: a breach in paper
   does **not** halt live. `💾 Save Bucket Overrides`.
4. **Correlation Groups** — declare which symbols move together; the exposure view
   and concentration warnings use them.
5. `🔴 Live Mode` toggles the live posture — it is deliberate, not a switch you can
   hit by accident.

Halts are session-scoped (latches are written but deliberately not re-armed on
boot) and peak/day anchors re-baseline to the restored book. Updates are validated
with typo detection: a misspelled key is rejected rather than silently ignored.

##### Tab 2 — `📜 Audit Timeline`

Every risk event with scope and reason.

1. Filter by **scope** (`All / paper / live`) and **event type**.
2. Search action/reason text, then `↻ Refresh`.
3. `Export CSV` for a record you can hand to someone else.

##### Tab 3 — `📊 Exposure Analysis`

- **Exposure by Symbol** (pie) — where the notional sits.
- **Exposure by Correlation Group** (bar) — whether one thesis is really behind
  several "diversified" positions.
- **Drawdown & Daily Loss — last 6 h** (line).
- **Exposure Details** — symbol table (notional, runners) and group table
  (notional, count).

##### Tab 4 — `📈 Trade History & Analytics`

Closed-trade risk statistics **grouped by day**. Filter by instrument kind
(`All / Equity / Option`), set rows per page (50–500), `↻ Refresh`, `Export CSV`.

### 2.4 P&L reports — `/reporting`

**Use it when…** you need one statement across every broker and both books: gross →
fees → net → estimated tax → net after tax.

**How to use:**
1. Set **From** / **To** (defaults to the financial year to date), optionally type
   broker names into **Brokers** (`All brokers, or comma-separated names`), and
   decide whether to **Include paper trades** (paper is never taxable — it never
   enters a return).
2. `⟳ Generate report` — then read the blocks in order: summary ladder → by-broker →
   tax categorisation → reconciliation → caveats/data notes → trade ledger.
3. **Exports:** `PDF statement`, `ITR annexures (xlsx)`, `Trade ledger (csv)`,
   `Email (dry run)` (writes a `.eml` to `var/reporting/outbox`; sending needs SMTP).
4. **Contract-note reconciliation:** per broker, paste the note's net P&L (and
   optional fees) and press `Reconcile` → `PASS / WARNING / FAIL` against the
   tolerance bands in `config/reporting.yaml`.
5. The `demo book` toggle includes a sample book labelled `SIMULATED` — for
   previews only; it is excluded from tax.

Every export carries the *"estimate — verify with a chartered accountant"*
disclaimer. Tax heads (FY 2026-27): F&O = non-speculative business income,
intraday equity = speculative, delivery ≤12 m = STCG 20 %, >12 m = LTCG 12.5 %
above the ₹1,25,000 exemption; unknown instruments fail closed as `UNCLASSIFIED`.
Full reference: [WEB-UI.md](WEB-UI.md) §Reporting.

---

## 3. Research group

### 3.1 Backtest — `/backtest` (and `/`)

**Use it when…** you want historical evidence for one strategy before risking
anything.

**How to use:**
1. **Strategy** — dropdown is fed by `/api/strategies` (built-ins + plugins from
   `plugins/strategies/`). The hint under it shows which segment the strategy runs in.
2. **Instrument** — SymbolPicker tabs `All / Equity / Index / F&O` + search. A symbol
   with no bars is visible but not selectable and says so ("No data loaded. Go to
   Data tab → fetch data for this symbol"). Synthetic-only deployments get
   `Use generated demo instrument`.
3. **Timeframe**, **From / To**, **Initial capital**, then the strategy's own
   parameters (auto-generated from its schema).
4. Tick **Fast preview** only when you want the approximate vectorised engine —
   faster, but *not* what a paper or live run reproduces.
5. `▶ Run backtest`.

**Reading the results:**

1. **Provenance strip** — data source and run identity, so a number is never
   divorced from where it came from; `persistBadge` marks a run saved to history.
2. **Metric cards** — P&L, Win Rate, Max Drawdown, Sharpe, Trades. Win rate counts
   **closed** trades only: a run still holding shows `—` rather than a misleading
   `0.00%`, and the Trades card notes open positions.
3. **Chart tabs:** `Equity curve` · `Drawdown` · `Price & signals`.
4. **Trade ledger** with pagination; open rows read `⏳ Open` instead of ✅/❌.
   Actions: `Save to compare`, `Export CSV`, `Open in playground` (sends the run to
   the Engine playground for a live-clock replay).
5. **Metric sections** — four collapsible blocks of richer metrics below the cards,
   preceded by a non-dismissable sample-size banner when the trade count is too
   small to trust.
6. **Run checks** (collapsed by default) — benchmark, cost shock and Monte Carlo
   panels that qualify the headline numbers. A check that could not run says why
   rather than silently disappearing.
7. **Certification** — an advisory eight-check traffic light (✅/⚠️/❌/⬜ unknown).
   "Unknown" is a real state and never counts as a pass; nothing here disables a
   button.
8. **Tune This** — hands the run to **Optimize** with the common fields prefilled.
   It never starts a search by itself; you still press `Start optimization`.
9. **Recent runs** — tiles from the server-side run ledger (`GET /api/backtest/runs`);
   opening a tile reads the stored payload back. A run whose ledger write failed
   says so instead of pretending it was saved.

### 3.2 Compare strategies — `/compare`

**Use it when…** you need a difference you can trust: two to four combinations under
*identical* conditions.

**Step 1 — pick the comparison mode:**

| Mode | What is held constant | What varies |
|------|-----------------------|-------------|
| **Compare Strategies** (default) | symbol, date range, capital, timeframe | strategy and/or parameters, up to 4 slots |
| **Test Generalization** | one strategy + one parameter set | up to 4 symbols — does the edge survive other instruments? |

**Step 2 — shared config:** Symbol, From, To, Capital, and a **shared Timeframe**
(a 5-minute and a daily run are not comparable, so it is a condition rather than a
per-slot choice). `Fast Preview` applies to every slot.

**Step 3 — slots:** `+ Add slot` / remove, then `▶ Run comparison`.

**Reading the results** — four tabs:

1. `Metrics Table` — side-by-side numbers.
2. `Equity Curves` — every curve **indexed to 100 at the first shared date**, so the
   chart compares performance rather than starting balances.
3. `Drawdown` — overlaid drawdown.
4. `Comparison` — the ranking view (Sharpe / return / drawdown).

**Comparison history** below the results stores past comparisons from the same run
ledger; a comparison that is not in the ledger says so rather than appearing saved.

### 3.3 Optimization — `/optimize`

**Use it when…** you want a systematically better parameter set — validated
out-of-sample — instead of hand-tuning.

**The setup form, section by section:**

1. **1 · Strategy & data** — `Load preset…` (start from a saved preset's values as
   the *current* column), Strategy, Symbol, Timeframe (only granularities actually
   stored are offered), From / To, Initial capital; an options **Strike selector**
   appears for option strategies.
2. **2 · Parameters** — table of `Opt ☐ · Parameter · Current · Min · Max · Step · #`;
   the counter updates live ("2 selected · 55 combinations"). Up to 8 parameters.
3. **3 · Objective & search** — `Maximize` (e.g. Sharpe) and Method:
   `Grid / Random / Bayesian / Genetic`, with the method's own knobs (samples,
   evaluations, population, generations).
4. **4 · Constraints** — `+ Add` risk constraints (max drawdown, minimum trades,
   win rate, …) that a combination must satisfy to be a candidate.
5. **5 · Walk-forward validation** — train window, test window, step, max evals per
   split. On by default in spirit: this is what catches a curve-fit winner.

Then check **Run estimate** and press `▶ Start optimization` (or `Save as draft`).
**Recent runs** lists previous searches.

**The run dashboard** — `/optimize/runs/<id>` (live progress while running, results
after) with six tabs:

| Tab | What it gives you |
|-----|-------------------|
| `Overview` | Best run vs baseline, validation checks, robustness score and plain-English overfitting warnings |
| `Heatmaps` | Any two parameters as a surface — look for a **plateau**, not a spike |
| `Walk-forward` | Train/test folds: does the winner hold out of sample? |
| `Sensitivity` | One-at-a-time sweeps around the winner |
| `All results` | Every candidate, sortable, with CSV export |
| `Audit` | Every apply, with rollback and the originating run |

**Applying the winner** opens a 3-step wizard: review the checks (walk-forward,
robustness, deflated Sharpe, Monte Carlo, data source, trade count — each
✅/⚠️/❌), choose the target (new paper runner, replace existing, A/B, or live —
live needs the ≥30-day paper-history gate), then type `CONFIRM`. Applying to live
is additionally refused for overfitted runs. Storage: PostgreSQL (migrations
009–015) or the dev SQLite profile. See [OPTIMIZATION-ENGINE.md](OPTIMIZATION-ENGINE.md).

### 3.4 Strategy builder — `/strategy-builder`

**Use it when…** you have a TradingView **Pine Script v5** idea and want it as a
Python strategy plugin.

1. **Strategy name** (optional — defaults to the script's title).
2. **Segment** (required) — every strategy runs in one capital partition so that
   backtest → paper → live share it.
3. Paste the **Pine Script v5 source** into the textarea.
4. `▶ Convert strategy` — the right pane shows the generated Python, and a
   **readable translation** appears underneath: entry criteria, entry strike
   (options), take-profit, stop-loss.
5. Anything the script left out must be typed into that summary — **`💾 Save as
   plugin` only appears once every required criterion has a value.**
6. `✅ Validate (backtest)` runs it against historical data before you trust it.
7. `📋 Copy LLM prompt` for scripts too complex for the converter — paste it into
   ChatGPT/Claude and bring the result back.

Saved plugins land in `plugins/strategies/` and appear in the Backtest/Optimize
dropdowns automatically. Authoring rules: [STRATEGY-AUTHORING.md](STRATEGY-AUTHORING.md),
[STRATEGY-TEMPLATE-GUIDE.md](STRATEGY-TEMPLATE-GUIDE.md).

---

## 4. Operations group

### 4.1 Engine playground — `/forward`

**Use it when…** you want to watch one strategy replay on a live clock for engine
diagnostics. It is a bench, **not a trading book** — no portfolio bucket-risk
controls, and the page says so.

**How to use:**
1. Strategy, Instrument, Timeframe (`1min` / `Day`), **Run mode**, **Data source**,
   Capital, **Replay speed** (`0.25` slow → `500` instant-ish bars/s; server default
   comes from `--replay-speed`), From / To.
2. `▶ Start` — the clock runs **on the server**, so bars keep being revealed with the
   tab closed; `■ Stop` ends the session.
3. Read: status badge (Idle / Running / Stopped), the `revealed / total · %`
   progress line, live metric cards, the live equity curve with its buy & hold
   benchmark, positions (entry vs current, move %, unrealised P&L, bars held) and
   the trade feed (`✅ / ❌ / ⏳ Open`).
4. **Banners:** `Pre-filled from a backtest / compare result` when you arrived via
   `Open in playground`, and a **resume** banner offering `▶ Resume` / `✕ Start
   fresh` when a session from a previous page load is still alive. The page
   re-attaches to its own `state_id` after a refresh.

Details: [FORWARD-TESTING.md](FORWARD-TESTING.md),
[OPTIONS-FORWARD-TESTING.md](OPTIONS-FORWARD-TESTING.md).

### 4.2 Market data — `/data`

**Use it when…** a symbol is missing from the Research dropdowns, or the stored
cache is stale. A symbol must have data before Backtest/Optimize/Compare list it.

**Fetch historical data:**
1. **Timeframe** (`1min / 5min / 15min / 1hour / 1day`) and **From / To**.
2. **Instruments** — `All / Equity / Index / F&O` tabs, search, then multi-select
   (`Clear` resets). Only the instruments on the *active tab* are fetched when you
   leave boxes unticked.
3. **Token status** — real data needs an authenticated broker: use the broker chip
   in the header to `Login` first (mStock needs ID/password/PIN/TOTP; Dhan needs
   Client ID/PIN/TOTP).
4. `⬇ Start fetch` → watch **Fetch progress** (bar, status, `n/m symbols`, bar
   count, elapsed time, current symbol, per-symbol errors). `⏹ Stop fetch` cancels.

Re-runs are **coverage-aware**: only the missing days are fetched, chunks are paced
adaptively, outages trip a circuit breaker instead of grinding for days, and any
lost-chunk count is reported rather than hidden.

**Data inventory:** `⟳ Refresh` → totals (symbols / bars / timeframes) and a
per-symbol table. This is how you answer *"do I even have RELIANCE 15 min since
March?"*

The app runs on **real data by default**: if the configured source is disabled it
falls back through DB bars → broker feeds → CSV, never silently to synthetic.
See [DATA-SOURCES.md](DATA-SOURCES.md) and [DATABASE.md](DATABASE.md).

### 4.3 Settings — `/settings`

**Use it when…** you are preparing to trade for real, or you need to change what a
run costs. Saved edits apply to **new** runs — never to strategies already running.

**Header:** `Active broker` selector and `⟳ Refresh`.

**Sections:**

1. **Global live kill-switch** — `OFF` (the safe default) refuses every live run
   regardless of segment mode. `ON` lets segments with `mode=live`, a positive
   daily-loss limit **and** a contract-note-validated broker arm live. This is the
   master switch; read the state text before arming anything.
2. **Broker cost profiles**, grouped by adoption:
   - **In use** — expanded: the active broker plus every broker a segment or the
     data routing points at. These are the brokers that price your runs.
   - **In `config/brokers.yaml` / Built-in presets** — collapsed behind
     *Show N other broker(s)*: the rate catalogue (10+ presets, so adding a broker
     is a rates change, not a code change). Nothing there is charged until a
     segment points at it.
   - Each card shows provenance (`config/brokers.yaml`, built-in preset, or *this
     row wins* when a panel edit differs from the file), a validation stamp, and
     `Edit / validate`.
   - **Resolution order is DB row → `config/brokers.yaml` → built-in preset.** So a
     broker only in the yaml becomes editable on first save (audited); after that
     the DB row wins until you change it back in the panel.
3. **Segments** — capital + broker + mode + risk limits per mandate. `+ New segment`
   opens the editor; `💾 Save segment (audit-trailed)` records every change, and the
   **arming checklist** appears inline for live segments.
4. **Profile editor** — the `Edit / validate` form for a single broker's rates;
   `…/<id>/audit` shows its history and a contract-note validation stamps the
   profile (which is also what the live arming gate checks).

Endpoints: `GET/PUT /api/settings/brokers[/<id>]`, `…/<id>/audit`,
`POST …/<id>/validate`, `GET/PUT /api/settings/active-broker`,
`GET/PUT/DELETE /api/settings/segments[/<id>]` plus the kill-switch endpoints.

---

## 5. Four end-to-end workflows

### 5.1 Take a strategy from idea to paper

`Market data` (fetch the symbol) → `Backtest` (evidence, check the certification
panel) → `Compare` (against a baseline) → `Optimize` (only if the shape of the
heatmap is a plateau) → `Portfolio` PAPER → `Add instance` → watch **Positions**
and **Activity log**.

### 5.2 Investigate a bad day

Risk strip `Details` (6 h breakdown + top losers) → `/risk` → `📜 Audit Timeline`
(what tripped, when) → Portfolio → **Trade history** (`Exit Reason`) → **Orders**
(slippage, aging) → `Analytics` for whether it is one bad day or a dying edge.

### 5.3 Get a brand-new instrument working

Header broker chip → `Login` → `Market data` → pick tab + instruments → `Start
fetch` → `Refresh` on inventory to confirm the bars landed → the symbol now appears
in Backtest/Compare/Optimize dropdowns.

### 5.4 Prepare to go live (deliberately)

`Settings` → verify broker profile + contract-note validation → set segment limits
→ read the arming checklist → global kill-switch `ON` → LIVE bucket → `Add
instance` in the live bucket → keep **Risk & intelligence** open, and remember
`Emergency flatten` and the risk strip's `Flatten all` are one click away.

---

## 6. Troubleshooting

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| Symbol missing from a dropdown | No bars stored | `Market data` → fetch it (§4.2) |
| Toast ends with `[req …]` | Server-side error | Grep that id in `logs/app.log` (§1.6) |
| `0 trades` / empty results | Window too short, or params never fire | Check From/To and the strategy's own parameters; `--log-level DEBUG` |
| Win rate shows `—` | Nothing has closed yet | Correct behaviour — the Trades card says how many are open |
| Forward start refused (403) | Risk/gate state | See LOGGING.md's symptom table and the risk strip |
| Fetch keeps failing | Broker outage / no session | Re-login via the broker chip; the fetch circuit breaker stops it grinding |
| A live close says `placed` | Venue acknowledgment is asynchronous | Expected — the UI refuses to claim the position is closed |
| Alerts not appearing | Intelligence disabled at boot | Check `PORTFOLIO_INTELLIGENCE_ENABLED` |

**Glossary.** *Bucket* = the paper or live capital partition. *Runner/instance* = one
deployed strategy. *Segment* = capital + broker + mode + risk limits mandate.
*Playbook* = declarative option-strategy config. *Ledger* = server-side history of
runs/orders (survives restarts). *Fast preview* = approximate vectorised engine.

---

## 7. Where the rest of the documentation lives

| I want to… | Read |
|------------|------|
| Know what every page *is* (developer view) | [WEB-UI.md](WEB-UI.md) |
| Understand the engine, costs and no-lookahead rule | [project-overview.md](project-overview.md), [BACKTEST-ENGINE.md](BACKTEST-ENGINE.md) |
| Write a strategy or plugin | [STRATEGY-AUTHORING.md](STRATEGY-AUTHORING.md), [STRATEGY-GUIDELINES.md](STRATEGY-GUIDELINES.md) |
| Operate the command center / endpoints | [PORTFOLIO-CENTER.md](PORTFOLIO-CENTER.md) |
| Trade options (paper, live, backtest) | [OPTIONS-PAPER-LIVE.md](OPTIONS-PAPER-LIVE.md), [OPTIONS-BACKTEST-PRD.md](OPTIONS-BACKTEST-PRD.md) |
| Add a data source or a table | [DATA-SOURCES.md](DATA-SOURCES.md), [DATABASE.md](DATABASE.md) |
| Turn on/off intelligence and alerts | [PORTFOLIO-INTELLIGENCE.md](PORTFOLIO-INTELLIGENCE.md), [ALERTS-GUIDE.md](ALERTS-GUIDE.md) |
| Debug a request | [LOGGING.md](LOGGING.md) |
| See what is done vs still open | [OPEN-ITEMS-TRACKER.md](OPEN-ITEMS-TRACKER.md) |
| Run the app / deploy it | `how to run.md` (root), README *Quick Start* |

