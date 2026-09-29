# Open Items Tracker

**How to use:** tick `[x]` when done. "Who: You" = needs your action (credentials/money/account). "Who: Agent" = I can do it on request. Everything stays on this page — nothing held in your head.

**Last updated:** 2026-09-29 · full suite 3,418 passed / 0 failed / 28 skipped / 4 xfailed / 3 xpassed · multi-broker Phases A–D shipped (`docs/MULTI-BROKER-PRD.md`) · date-rot test repairs landed (pricing-clock pins + replay-anchored expiry) · doc sweep: item 7 marked done, item 12 half-done note · **Consolidated P&L & Tax Reporting (PRD-002) merged and pushed to remote**

> **Expiry-day pricing fix (2026-09-24):** `SyntheticChainGenerator.MIN_PRICING_YEARS`
> (half a trading day) in `price_contract` — near/post-expiry synthetic options keep a
> real premium smile instead of collapsing to ₹0, which used to trip the fail-closed
> ltp=0 guard and block all entries on monthly expiry days. 31+12 date-brittle test
> failures fixed, 0 introduced.

---

## 🔥 Do today (market is open)

- [ ] **1. Real-session smoke test (T9.5)** — *~30 min — Who: You + Agent*
  Start app → mStock login + TOTP → spawn ONE option runner `source: mstock, mode: paper` → confirm quote label is NOT `synthetic:bs`.
  Command: `cd src && python -m backtest.web.app --port 5000`

- [ ] **2. Start chain snapshot capture** — *~5 min to start, runs all day — Who: You*
  Command: `PYTHONPATH=src python scripts/snapshot_option_chains.py --interval-seconds 300`
  Win: today's option chain data starts accruing for future backtests.

- [ ] **3. Intraday strategy paper run** — *~1 hr of market time — Who: Agent (on request)*
  Spawn runner on the new strategy, watch it trade real 1-hour bars until close.

- [ ] **4. Record findings** — *~10 min after close — Who: Agent*
  Log what worked/broke in `docs/OPTIONS-FORWARD-TEST-EXPERIMENT.md`.

---

## 🧠 HFT strategy — what's real (opinion)

**True HFT is not possible here.** No tick feed, no sub-second latency, no colocation.
**What IS possible: high-turnover intraday on 1-hour bars** (shortest real feed).
Plan: new strategy in `plugins/strategies/` from the option template + tighter
exit knobs (`reenter: true`, `max_reentries_per_day: 4`). Est: half a day of
agent work. Blocked only by item 1 passing.

---

## 🟠 This week

- [x] **5. HFT-style strategy built + conformance-tested** — ✅ DONE (owner confirmed 2026-09-28): the high-turnover intraday family shipped in `plugins/strategies/` — `momentum_burst` (draft #1 adapted 2026-09-18), `atm_instant_buy`, `immediate_entry`, `immediate_strangle`, `time_alternator`, `ema_reversion_pob` (reenter knobs) — all pass the conformance battery (`tests/test_strategy_conformance.py`) and register at boot.
- [ ] **6. CI workflow push** (`.github/workflows/ci.yml` needs your account — I can't push workflows) — *15 min — Who: You*
- [x] **7. Portfolio-page option trade rows (UI polish)** — ✅ DONE (verified in code 2026-09-28): `portfolio.js` renders per-structure rows via `OptionView.openStructures` (matrix + deep-dive); API side covered by `tests/engine/test_portfolio_options_rows.py`, JS by `tests/js/test_option_view.mjs` + `render_option_views.mjs`.
- [ ] **8. Consultant answers on 6 open questions** — `CONSULTANT_RESPONSE.md` §0.4 — *Who: You*

### Consolidated P&L / tax report (PRD-002) — shipped ✅

**Merged 2026-09-29:** Complete reporting system with correct Indian tax classification, exports, reconciliation, and monthly email reports. All 7 follow-up items from the original PRD are now live:

- ✅ Tax engine with correct FY 2026-27 rates (STCG 20%, LTCG 12.5% above ₹1,25,000, business slab default 30%)
- ✅ Consolidated P&L across all brokers/books with itemised fees
- ✅ Exports: PDF statement, ITR annexures (xlsx/csv/json), trade ledger CSV
- ✅ Contract-note reconciliation per broker with PASS/WARNING/FAIL tolerance bands
- ✅ Monthly email reports (dry-run to outbox by default)
- ✅ Settings panel with broker catalogue grouped by adoption
- ✅ Comprehensive test suite (93% coverage, every module ≥88%)

**Remaining follow-ups (user action required):**
- [ ] **Tax config sign-off** — *Who: You (+ your CA)*. Verify `config/reporting.yaml` rates match your actual marginal rate before treating "estimated tax" as your number.
- [ ] **Validate dhan broker profile** — *Who: You (one contract note)*. `/settings` → dhan card → Edit/validate with real contract note amounts.
- [ ] **Turn monthly email on** — *Who: You*. Set `monthly_email.enabled: true` and SMTP host in `config/reporting.yaml`; export `REPORTING_SMTP_PASSWORD`.
- [ ] **Reconciliation against real contract notes** — *Who: You + Agent*. `/reporting` → paste each broker's note net P&L → verify PASS/WARNING/FAIL.

## 🟢 Later (no date)

- [x] 9. Options-tab hard delete — ✅ DONE early (2026-09-22): GAP-3 resolved as REMOVE — /options page, options.js, Manual Options Book tab/banner deleted; JSON endpoints + book singleton kept (portfolio merge + emergency flatten). Review date no longer needed.
- [ ] 10. Risk envelope V2 (BS+IV+SPAN) — after consultant answers
- [ ] 11. Multi-leg structures (straddles/condors) — Phase B, don't bundle
- [ ] 12. Sizing presets + richer metrics (Sortino, profit factor) — vectorized path
      — *metrics half is DONE (2026-09-21: `engine/metrics.py` has Sortino, profit
      factor, expectancy, VaR/ES); sizing presets on the vectorized path remain.*

---

**Wins today so far:** ✅ Gap validation done — P1.1 live chain wiring, F-12 equity fills, P2.4 persistence, fail-closed trader all verified in code + tests. One date-brittle test fixed.
