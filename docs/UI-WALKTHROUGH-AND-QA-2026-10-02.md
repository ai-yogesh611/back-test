# UI Walkthrough & QA Report — 2026-10-02

Purpose: a per-tab functional walkthrough of the web app, written for developers
who are about to update or extend a feature. It records what each page is *for*,
the fields it exposes, and the non-functional / cosmetic issues found during a
manual browser sweep on a non-trading day (cosmetic + field-presence + dead-control
+ console/network checks; no live fills exercised).

Environment tested: server on `:5000` from `main` @ `f0563c5`, mStock logged in,
3 paper runners (RELIANCE 1min, all PAUSED), 0 live runners. Browser viewport
~731–1024px wide.

Severity legend:
- 🔴 **Functional** — a page/feature is broken or misrepresents data.
- 🟠 **Notable** — wrong/confusing in a way a user will hit; fix before next feature work.
- 🟡 **Cosmetic** — polish; low risk.

---

## Findings summary (actionable)

| # | Sev | Where | Issue |
|---|-----|-------|-------|
| 1 | 🔴 | `/reporting` | Consolidated P&L returns **HTTP 500** on load: `can't compare offset-naive and offset-aware datetimes`. Only crashes when paper trades are included. |
| 2 | 🔴 | `/portfolio/live`, `/portfolio` | "Open Positions — the live book" panel shows a **PAPER** position on the LIVE page. Bucket isolation leaks in the positions panel. |
| 3 | 🟠 | `/settings` | "Active broker" dropdown exposes **test/foreign broker profiles** (Test Broker, Expensive, Panel Broker, Zero, Generic Discount, Robinhood, TD Ameritrade, Ibkr). Default selected is `panel_broker`. |
| 4 | 🟠 | all pages | Global chrome polls `/api/portfolio/summary` + `/api/data/status` + `/api/alerts/active` every ~5s even on research pages that show none of it; portfolio overview fetches summary 2–3×/cycle. |
| 5 | 🟡 | all pages (narrow) | Top risk strip clips "Deployed: …" below ~800px (`overflow:auto` with no wrap). |
| 6 | 🟡 | portfolio pages | Bar clock prints raw seconds: "last 101789s ago" (~28h). Should humanize. |
| 7 | 🟡 | Broker Board | "Expires: 02:04 AM" has no date and no timezone label. |
| 8 | 🟡 | `/data` | Inventory flashes "0 symbols · 0 bars" before the async fetch resolves. Show a loading state. |
| 9 | 🟡 | `/forward` | Symbol is a free-text box while backtest/compare/optimize use the shared `SymbolPicker`. Consistency gap. |
| 10 | 🟡 | footer | Source tag differs by page ("Historical data" vs "mStock data"); likely intentional per routing, but reads as inconsistency. |
| 11 | 🟠 | command center, risk banner | Misleading timers on market-closed days (fake "next bar in 28s" countdown, browser-clock "Last update", sparkline mislabeled "last 6 hours"). **Signed-off fix spec in §14** — implement copy verbatim. |

Positive: **no console errors** on any page except the reporting 500; **all API calls 200** except reporting; broker board, risk board, spawn modal, and the research forms all render with sensible, non-redundant fields.

---

## 1. Global chrome (every page)

**What it is:** persistent header + risk strip + alerts. Header carries the
workspace breadcrumb, a global search ("Jump to… Ctrl K"), a **Standby/halt pill**,
the broker connection chip (Dhan / mStock), and a theme toggle. Below it, the risk
strip shows Feed state, Daily Loss, DD, Deployed, Positions, plus `Details` and
`Flatten all`.

**Behavior verified:**
- Standby/halt pill (`#halt-pill`) → on portfolio pages activates the "Risk &
  intelligence" tab and scrolls up; elsewhere navigates to `/risk`. Works.
- Broker chip → opens **Broker Connections** modal (see §7).
- Kill-switch defaults to **OFF (live blocked)** on `/settings` — correct safe default.

**Issues:** #4 (polling), #5 (clip), #10 (footer tag).

---

## 2. `/` and `/backtest` — Backtest

**Purpose:** turn a strategy idea into historical evidence.

**Fields (all relevant, none redundant):** Strategy (select), Instrument
(`SymbolPicker`: All/Equity/Index/F&O tabs + search), Timeframe, From, To, Initial
capital (₹), per-strategy params (e.g. Band Width std dev, Band Period), Fast
preview (checkbox). Header links to Compare and Optimize. Data-source line:
"Real Data (PostgreSQL) · certification-grade".

**Status:** clean. No console/network errors.

---

## 3. `/compare` — Compare strategies

**Purpose:** run up to 3 strategy slots on one instrument/window.

**Fields:** Symbol, From, To, Capital, shared Timeframe, Fast Preview (all slots),
then 3 slots each = Strategy + its params. "Slots" section present.

**Status:** clean.

---

## 4. `/optimize` — Parameter optimization

**Purpose:** grid/random/Bayesian/genetic search over a parameter space.

**Fields:** "1 · Strategy & data" (preset loader, strategy + description, symbol,
timeframe with "only granularities actually stored" hint, From/To, capital);
"2 · Parameters" table = Opt checkbox · Parameter · Current · Min · Max · Step ·
# (combination count, live "2 selected · 55 combinations"); "3 · Objective &
search" = Maximize (Sharpe) + Method (Grid/Random/Bayesian/Genetic).

**Status:** clean and well-designed. Note: `/optimize/runs/<id>` (results
dashboard) not exercised — no completed run in this environment.

---

## 5. `/data` — Market data manager

**Purpose:** fetch historical OHLCV into Postgres and inspect coverage.

**Fields:** "Fetch historical data" = Timeframe, From, To, Instruments picker
(tabs/search/multi-select), Start/Stop; "Data inventory" = Refresh + totals
(symbols/bars/timeframes) + per-symbol table.

**Verified:** `/api/data/inventory` returns 205 symbols / 3,328,345 bars and the
table renders correctly once loaded.

**Issues:** #8 (flash of "0 symbols" before fetch resolves).

---

## 6. `/portfolio`, `/portfolio/paper`, `/portfolio/live` — Capital & execution

**Purpose:** the trading command center. Overview = both buckets side by side;
paper/live = bucket-scoped (`data-mode` on `#portfolio-page`).

**Shared center contains:** command-center header (Add instance, Pause/Resume all,
Export charts, Emergency flatten [live only]); metric cards (Total Capital, Equity,
Deployed, Daily/Realized P&L, Open Positions, Daily Loss Limit); Strategy instances
table (search + status filter + sort); tabs Positions / Equity / Orders / Risk &
intelligence / Trade history / Playbooks / Activity log.

**Spawn (Add instance) modal — routing-only, 15 fields, conditional display:**
Instance name (optional), Strategy, Playbook (optional), Target type, Timeframe,
Bucket mode, Data source, Segment (hidden unless configured), Broker, Instrument
(+ hidden option-symbol select), Lots per leg (options), Universe/Pool, Max pool
positions, Allocated capital. Field set is appropriate — no stray trading-logic
fields. Broker dropdown now populates from `/api/broker/list` (the `symbol_picker.js`
fix from `f0563c5` is confirmed working: mStock + Dhan present).

**🔴 Issue #2 — bucket isolation leak (positions panel):**
On `/portfolio/live`, "Open Positions — the live book" rendered a position tagged
`PAUSED · paper · options_index` (`buy_and_hold·RELIANCE·1min`), while the instance
table above correctly showed "No instances deployed in this bucket". The page's own
footer states "other buckets never appear here".

Root cause: `render(p)` in `static/js/portfolio.js` filters `p.runners` by
`PAGE_MODE` (line ~723) but then `renderAggregatePositions(p)` takes the
`p.positions` fast-path (line ~535) → `renderPositionRows(p.positions)` which is
**not** filtered by bucket. The SSE `/api/portfolio/stream` frame carries a combined
`positions` array where each row has a `mode` field (verified: `mode:"paper"`).

Fix direction: filter `p.positions` by `(x.mode||"paper") === PAGE_MODE` alongside
`p.runners`, mirroring the existing runner filter. Same leak applies to the overview
page's embedded LIVE center.

---

## 7. Broker Connections modal (header chip)

**Purpose:** show every broker's session + segments from one call.

**Content verified:** Dhan (Not connected, segment "Equity Intraday (Paper, ₹8L)",
Login); mStock (Authenticated, "remembered today", segment "Index Options (Paper,
₹12L)", Logout + Reconcile, "UI" active badge). Refresh + close present.

**Issues:** #7 (expiry lacks date + tz).

---

## 8. Risk Board / `/risk` — Risk management

**Purpose:** exposure, limit usage, kill-switch state.

**Content verified:** Global Risk Dashboard cards = Daily Loss Used (1.9%),
Drawdown (0.0%, "Peak protected"), Capital Deployed (9.0%), Gross Exposure (31.6%),
Open Positions (1), System Status (LIVE). "All Buckets" filter, Refresh, Export CSV.
Collapsible sections: Portfolio Greeks, Concentration, Strategy Correlation, Market
Regime, Market Activity (OI & liquidity), Stress Test (−25%), Reset Breaker.

**Status:** clean and comprehensive.

---

## 9. `/analytics` — Strategy analytics

**Purpose:** performance/execution/risk view across paper and live.

**Content verified:** Overview tab; Period (Last 30 Days) + Mode (All Modes)
filters; Portfolio Overview = Total Return, Portfolio Sharpe (−1.55 "Poor"),
Win Rate (40%, "5 trades 2W/3L"), Max Drawdown; "0 / 3 Active Strategies".
Compare backtests + Refresh actions.

**Status:** clean.

---

## 10. `/reporting` — Consolidated P&L   🔴 BROKEN

**Purpose:** every broker/both books → one statement (gross → costs → net → tax).

**Fields:** From, To, Brokers (free text "All brokers, or comma-separated names"),
Include paper trades (checkbox), Generate report; outputs = PDF statement, ITR
annexures (xlsx), Trade ledger (csv), Email (dry run), demo book toggle.

**🔴 Issue #1 — 500 on load (page auto-generates and crashes):**
`GET /api/reporting/pnl/consolidated?…&include_paper=true` → 500, UI shows
"report generation failed: can't compare offset-naive and offset-aware datetimes".

Traceback: `src/backtest/reporting/consolidator.py:317`
```python
report.trades = sorted(selected, key=lambda t: (t.exit_time or datetime.min, t.symbol))
```
The sort compares a tz-aware `exit_time` against the naive `datetime.min` sentinel
and/or against naive timestamps from another source.

Diagnostic (reproduced via curl): `include_paper=false` → **works**;
`include_paper=true` → **crashes**. So paper trade timestamps and broker/live trade
timestamps disagree on tz-awareness, and mixing them breaks the sort.

Fix direction: normalize every `exit_time` to a single awareness (e.g. coerce to
UTC-aware) before sorting, and use an aware sentinel (or sort with `None` handled
separately). This is the top item to fix — the page is unusable with paper trades on.

---

## 11. `/forward` — Engine playground

**Purpose:** single-strategy paper replay for engine diagnostics (not a trading book).

**Fields:** Strategy, Symbol (free text), Timeframe, Run mode, Data source, Capital,
Replay speed, From, To. Clear "Diagnostics only · paper replay without portfolio
bucket-risk controls" banner.

**Issues:** #9 (free-text symbol vs shared SymbolPicker). Minor: the default
strategy shown was an options strategy (`atm_instant_buy`) while the symbol
placeholder suggests equities ("e.g. MAZDOCK, INFY") — consider tying the symbol
hint to the selected strategy's instrument kind.

---

## 12. `/settings` — Workspace settings   🟠

**Purpose:** broker cost models, execution segments, safeguards.

**Content verified:** Global live kill-switch (OFF = live blocked) + Arm live
execution; "In use (3)" broker cost cards (Dhan, Mstock shown, with
config/brokers.yaml source, segment, contract-note validation status, STT/GST
rates); collapsed "12 more available".

**🟠 Issue #3 — test/foreign brokers in the "Active broker" selector:**
`#settings-active-broker` options include `test_broker`, `expensive`,
`panel_broker`, `zero`, `generic_discount`, `robinhood`, `td_ameritrade`, `ibkr`.
These are fee-model presets (built-in + `config/brokers.yaml`), not execution
venues, and several are US brokers or obvious test fixtures. The **currently
selected value is `panel_broker`** (a test preset) even though `config/brokers.yaml`
sets `active_broker: zerodha`. Display names for the auto-derived entries are
naive `.title()` ("Ibkr", "Td Ameritrade").

Fix direction: gate the selector to real/connected brokers (or at least hide the
test fixtures and non-IN venues), and make the default reflect the config's
`active_broker` rather than a test preset.

---

## 13. `/strategy-builder` — Pine Script converter

**Purpose:** TradingView Pine v5 → Python strategy plugin.

**Fields:** Strategy name (optional), Segment (required, "Choose a segment…"),
Pine Script v5 source (textarea with `//@version=5` placeholder), Converted Python
strategy (output pane).

**Status:** clean. The "Segment required" gate (from `238f993`) is present.

---

## 14. Enhancement spec — Market-status awareness (no misleading timers)

**Approved by the operator 2026-10-02.** Implements the holiday-aware feed gate,
the `market_holidays` table, and the exact UI copy below. Implement the message
strings **verbatim** — they were agreed in discussion and are the contract.

### 14.1 Problem

Today (02-Oct-2026) is an NSE trading holiday (Mahatma Gandhi Jayanti), yet the UI
behaves as if trading were live:

- Command center shows `⏱ next bar in 28s (bars every 60s, last 103592s ago)` —
  a client-side modulo countdown toward an event that cannot happen
  (`portfolio.js`, bar-clock block ~lines 774–797).
- Risk banner shows `Last update: 14:43:39` — when the SSE snapshot carries no
  timestamp, `risk_strip.js:190` **falls back to the browser's wall clock**, so
  "last update" refreshes even with no server data at all.
- Details panel is labeled `Daily loss breakdown · last 6 hours` (`base.html:131`)
  but the sparkline actually plots ≤60 client-memory points (~5 min since page
  load), reset on refresh (`risk_strip.js:12,155–160,206`).
- `api/forward.py:484` hardcodes `"market_open": True`.
- The backend calendar, `live/time_manager.py`, ships a *partial 2024* NSE holiday
  list and is not consulted by the web layer or the feed gate.

Principle: **a timer may only count toward an event that can actually happen, and
every timing string must derive from server truth (bar arrival, broker session,
market calendar), never from the browser clock.**

### 14.2 Source of truth — NSE holiday data (verified 2026-10-02)

- `GET https://www.nseindia.com/api/holiday-master?type=trading` — JSON keyed by
  segment (`CBM` = equity; plus derivative/currency/commodity sections); entries
  `{tradingDate, weekDay, description, morning_session, evening_session, Sr_no}`.
  `holiday-year-list` currently returns `{"year":["2026"]}`; NSE publishes the
  next year around mid-December.
- `GET https://www.nseindia.com/api/marketStatus` — live per-segment status:
  `{"market":"Capital Market","marketStatus":"Close","tradeDate":"01-Oct-2026
  15:30","marketStatusMessage":"Market is Closed", …}`.
- **Catch:** NSE sits behind Akamai; plain `requests`/`curl` gets empty/403
  responses. Therefore **the app must NOT call NSE at runtime.** The list is
  seeded into the DB once per year (manual fetch via browser, or a one-off
  script with a cookie handshake), and the table below becomes the source of truth.

### 14.3 `market_holidays` table schema

One row per exchange-declared holiday. Weekend Saturdays/Sundays are NOT stored
(weekday rule handles them); store only dates that would otherwise look tradable.

```sql
CREATE TABLE market_holidays (
    holiday_date  DATE PRIMARY KEY,            -- IST calendar date, market closed all day
    segment       TEXT NOT NULL DEFAULT 'equity', -- 'equity' | 'derivative' | 'currency' | 'commodity'
    description   TEXT NOT NULL,               -- e.g. 'Mahatma Gandhi Jayanti'
    is_trading_holiday BOOLEAN NOT NULL DEFAULT TRUE, -- FALSE for clearing-only holidays
    source        TEXT NOT NULL DEFAULT 'nse', -- provenance: 'nse' | 'manual'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_market_holidays_year ON market_holidays (EXTRACT(year FROM holiday_date));
```

Wire it as a SQLAlchemy model in the same DB as the portfolio tables (Postgres via
`FORWARD_TEST_DB_URL`; remember `create_all`+stamp drift — ship an Alembic
migration, do not rely on `create_all`).

Seed for 2026 (equity/derivative trading holidays from the NSE table; weekday rows
only — weekend rows add nothing):

```
2026-01-15  Municipal Corporation Election - Maharashtra
2026-01-26  Republic Day
2026-03-03  Holi
2026-03-26  Shri Ram Navami
2026-03-31  Shri Mahavir Jayanti
2026-04-03  Good Friday
2026-04-14  Dr. Baba Saheb Ambedkar Jayanti
2026-05-01  Maharashtra Day
2026-05-28  Bakri Id
2026-06-26  Muharram
2026-09-14  Ganesh Chaturthi
2026-10-02  Mahatma Gandhi Jayanti
2026-10-20  Dussehra
2026-11-08  Diwali Laxmi Pujan — trading holiday but Muhurat session only;
            store it: day_state=HOLIDAY gives CLOSED for normal bars
2026-11-24  Prakash Gurpurb Sri Guru Nanak Dev
2026-12-25  Christmas
```

### 14.4 Day-status contract (start / midnight check)

A single server-side resolver, `get_market_day_state(now_ist)`, computed at app
startup and **re-evaluated lazily whenever the IST date changes** (a server up
since yesterday must carry today's verdict, not the boot-time one — key the cache
on IST date, no scheduler needed):

```
state ∈ { HOLIDAY, WEEKEND, TRADING_DAY }
if ist.weekday() >= 5:                      WEEKEND
elif date in market_holidays (segment):     HOLIDAY  (+ description)
else:                                       TRADING_DAY
```

**Calendar-gap rule:** if `market_holidays` has no rows covering the current IST
year, fall back to the weekday rule only and surface
`⚠ Holiday calendar missing for <year> — weekday rule only` (chip state 6). Never
silently guess "holiday" from an empty table.

**Fail-open rule (the trap-guard):** inside 09:15–15:30 on a weekday, the system
may only claim "closed" if the DB says HOLIDAY. If the DB read fails, the verdict
is "not closed" → show STALE / BROKER_DOWN warnings instead of calm. Any calendar
exception degrades to today's behavior minus the false calm, never to a new
wrongness.

Expose in the SSE `portfolio` snapshot and `/api/portfolio/summary`:

```json
"market": { "day_state": "HOLIDAY", "session_state": "CLOSED",
            "holiday_name": "Mahatma Gandhi Jayanti", "next_open_ts": "2026-10-05T09:15+05:30" }
```

`session_state ∈ { PRE_OPEN, OPEN, CLOSED_TODAY, CLOSED_WEEKEND, CLOSED_HOLIDAY }`
(time-of-day within the day-state). Replace the hardcoded `"market_open": True` at
`api/forward.py:484` with this truth.

### 14.5 Feed gate — rest on holidays

- `feed_registry.py` `_poll_once()` (lines 471–495): today it checks only
  time-of-day (`mstock_live_feed._market_open`) and skips symbols already in
  `_last_ts` — on a holiday it still wakes every 60s, still does a first fetch per
  never-seen symbol, and dedups stale bars into silence. Change: when
  `day_state == HOLIDAY` (or WEEKEND), **skip polling fully** — no broker HTTP at
  all, including first-fetches — log `feed resting — <reason>` once per day, and
  re-arm automatically on the next TRADING_DAY at session start.
- `mstock_live_feed.py` `stream()` (lines 587–600): extend the existing
  `market_gate` sleep with the same day-state check so per-symbol generators rest
  identically.
- Keep both gates fail-open: an unreadable `market_holidays` table must never stop
  the feed (assume trading day, keep polling).
- **Do NOT kill the feed threads to save memory** — thread stacks are a few MB; the
  runners' warmup state must stay loaded regardless. The win is honesty + zero API
  calls, not RAM.
- Options path note: `ChainBus`/`option_quote_provider` poll underlying quotes on
  the same cadence; apply the identical holiday gate before any chain fetch.

### 14.6 UI state machine — five states

`HEALTHY` (open, bars flowing) · `STALE` (open, no bars > 2 periods — incident) ·
`BROKER_DOWN` (open, broker session lost) · `CLOSED` (weekend/holiday/pre-open/
post-close) · `UNKNOWN` (calendar unreadable — fail loud, never guess). Every
timing string below is chosen by these states, which come from the server
snapshot, not client inference.

### 14.7 Message tables (implement verbatim)

**1 · App-top header chip (new, global, `base.html`)**

| State | Chip text | Color |
|---|---|---|
| Session live | `● MARKET OPEN · till 15:30 IST` | green |
| Pre-open (09:00–09:15, optional) | `◐ PRE-OPEN · opens 09:15` | amber |
| After close, trading day | `○ CLOSED · next open tomorrow 09:15` | grey |
| Weekend | `○ WEEKEND · opens Mon 05-Oct 09:15` | grey |
| Holiday (from DB) | `○ HOLIDAY · Mahatma Gandhi Jayanti · opens Mon 05-Oct 09:15` | grey |
| Calendar gap | `⚠ No holiday data for <year> — weekday rule only` | amber |

**2 · Command center bar-clock line (`portfolio.js`)** — countdown exists only in
HEALTHY; everything else is a static statement (no ticking on closed days):

| Condition | Message |
|---|---|
| Open + bars flowing | `⏱ next bar in 28s · bars every 60s` |
| Open + no bars >2 periods | `⚠ market OPEN — no bar for 2m 14s (expected every 60s)` — red |
| Open + broker not logged in | `🔴 broker session not logged in — live feed paused · log in at /settings` — red |
| Closed (any reason) | `○ Market closed (Gandhi Jayanti) — no bars today · last bar 15:29 IST, 01-Oct` — grey, static |

**3 · Risk banner "Last update" (`risk_strip.js`)** — freshness, never browser
clock. Remove the `new Date().toLocaleTimeString()` fallback at line 190:

| Condition | Message |
|---|---|
| Snapshot <15s old | `Updated 14:43:39 · live` |
| Snapshot stale, market open | `Updated 14:43:39 · ⚠ 3m stale` |
| Closed day | `Figures final as of 01-Oct 15:30 IST (market closed)` |
| No timestamp from server | `Updated —` (never fabricate) |

**4 · Daily-loss sparkline (`base.html:131` label + `risk_strip.js`)** — v1 relabel
honest, v2 server-sourced series:

| Version | Label / message |
|---|---|
| v1 (UI-only fix, do now) | `Daily loss · since page load (~5 min live sample)` |
| v2 (server series, follow-up) | during session: `Daily loss · today 09:15 → 14:43 · peak 2.1%, now 0.8%` · closed day: `Daily loss · 01-Oct session complete — ended at 0.3% of limit` · holiday: `No session today (Gandhi Jayanti) — showing 01-Oct, last trading day` |

v2 backend: sample `daily_loss_pct` every 1–5 min during the session into the
existing portfolio state (server timestamps), serve as `GET /api/risk/history`,
plot the full session. The client `riskHistory` array then goes away.

**5 · Risk strip / halt pill on closed days** — `HALTED` vs `NO DATA` must stay
visually distinct when figures are old: on CLOSED days render the strip figures in
"frozen" styling (dimmed + the "figures final" line from table 3) so nobody reads
yesterday's close as today's live risk. The halt pill keeps its own state
(`halted`/`danger`/`warn`/`safe`) and does NOT fire stale-state colors purely
because a holiday froze the numbers.

### 14.8 Acceptance criteria / tests

- Unit: `get_market_day_state` for 02-Oct-2026 (HOLIDAY), a Saturday (WEEKEND), a
  normal Thursday (TRADING_DAY), and an empty-table 2027-01-04 (weekday fallback +
  gap warning, never HOLIDAY).
- Unit: inside 09:15–15:30 weekday with the DB read raising → session_state OPEN
  (fail-open), STALE path reachable.
- Feed: with day_state HOLIDAY, mock broker client records **zero** HTTP calls
  across ≥3 poll cycles, including a newly subscribed symbol; re-arms next TRADING_DAY.
- UI (jsdom or manual): no element matching the countdown pattern renders on a
  CLOSED snapshot; "Last update" never uses `Date.now()` when the server omits
  `timestamp`.
- Copy check: assert the five tables' strings appear verbatim (guards against
  silent paraphrase).

---

## Notes for whoever picks these up

- **#14 is a signed-off enhancement spec** (holiday feed gate + market_holidays +
  header chip + verbatim UI copy), agreed with the operator 2026-10-02 — it is
  intentionally separate from the bug findings below.
- #1 and #2 are the two real functional bugs; both have exact file:line and a
  reproduction. Fix #1 first (reporting is fully broken with paper on).
- #2's fix is one line in `portfolio.js` `render()`; add a test that a live-scoped
  render drops paper positions (there's already a `test_options_spawn_ui.py` pattern
  for "component loaded on every page" — a bucket-isolation assertion would fit).
- #3 and #4 are the kind of thing to fold into the next feature touching Settings /
  the base layout, not standalone.
- Everything else is cosmetic polish.
