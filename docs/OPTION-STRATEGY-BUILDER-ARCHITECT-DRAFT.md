# Architect review draft — option strategy authoring and paper → live rollout

**Status:** proposal, not approval or implementation commitment · **Date:** 2026-10-06

## Decision requested

Keep **one trading platform** and add a **dedicated options authoring workflow**, rather than a second application or a general-purpose Pine compiler. Prioritize a trustworthy options paper-to-live path. Build a small guided builder on the *existing* option strategy + playbook contracts. Treat Pine import as an optional, strictly scoped research aid, not an execution authority.

This is a request for architectural review, not a claim that live readiness or option backtesting has already been established.

## Why this fits the current code

- `templates/option_strategy_template.py` already expresses the separation: `generate_market_view(candles) -> MarketView | None` emits bullish/bearish conviction; the bridge owns chain, strike, structure and orders. Strategies must not import broker/data modules (`docs/STRATEGY-AUTHORING.md`).
- `src/backtest/forward/options_bridge.py` and `src/backtest/forward/execution_engine.py` already form a forward execution seam. `src/backtest/playbooks/models.py` captures structure, strike, quantity and exit configuration; `src/backtest/options/{paper_trading,live_trading}.py` own the two execution paths. Reuse these; do not invent a parallel order stack.
- **Portfolio is common to every strategy and instrument.** Equity and option fills must feed the existing portfolio/segment accounting and risk controls, preserving paper/live capital boundaries. Options may have instrument-specific position details and execution state, but not a second authoritative portfolio, independent capital pool, or separate risk total. The portfolio must expose consolidated cash, realized/unrealized P&L, exposure and emergency flatten across equity and options; reconcile each instrument's execution book into that shared view.
- `docs/OPTIONS-FORWARD-TESTING.md` records much of the forward paper/option plumbing as done, **but** `docs/OPTIONS-PAPER-LIVE.md` says broker-data paper runs have not been restarted after the 2026-09-30 wipe. Code completion is not operational proof.
- `docs/OPTIONS-BACKTEST-PRD.md` and `src/backtest/api/backtest.py` say historical options backtests are disabled: the DB lacks historical option chains. Do not advertise an option backtest or show synthetic-chain P&L as evidence of edge.
- The current Pine converter is intentionally limited: v5/v6 subset only; indicator scripts are not executable orders, and persistent `var` / `:=` logic is rejected. Its validate endpoint currently returns `SYNTAX_ONLY` without an engine. Don't couple launch to arbitrary Pine conversion.

## Proposed product contract (V1)

User chooses **NIFTY or BANKNIFTY**, a supported signal preset (initially EMA trend/cross; add more only after a signal-level test), timeframe, and one playbook: bullish → **long call**, bearish → **long put**. Explicitly distinguish this from `strategy.short` on the underlying. One open structure per runner; no simultaneous CE+PE, partial exits, user-supplied executable Python, multi-leg UI authoring, or arbitrary Pine in V1. The supported engine structures may be wider; the builder intentionally offers fewer.

Form sections:

1. **Signal:** preset, parameter ranges, entry condition, neutral/flip behavior; render an exact plain-language preview with indicator warm-up and bar timing.
2. **Instrument & expression:** underlying, expiry policy, ATM selector, lots, resolved lot size/contract at execution time (never hardcode exchange lot sizes). Reject missing chain/quote, stale quote, invalid or near-expiry contract; do not silently substitute synthetic quotes.
3. **Risk & exits:** playbook stop/target, DTE square-off, flip, per-trade risk cap, capital segment and maximum concurrent exposure. Show units (option premium % vs underlying points) explicitly. Emergency flatten/breakers always outrank tactical exits; no same-bar re-entry, default re-entry false (existing precedence and churn findings in `docs/ARCHITECTURE-UNIFIED-TRADING.md`).
4. **Review:** display the immutable versioned signal configuration and playbook snapshot, resulting expression on a sample real chain (preview only, never an order), assumptions and source/quote badge. Save as **draft**. No Sharpe threshold or performance gate in the builder.

Prefer a validated declarative definition, e.g. `{schema_version, signal_type, params, underlying, playbook_id, playbook_version, segment}`. Compile/instantiate a fixed audited signal implementation; don't `exec` user expressions or accept arbitrary generated code. Snapshot parameters at runner start; edits produce a new version and do not mutate a running strategy. Use existing playbook/version and segment conventions rather than a second source of truth. Exact storage model needs architect sign-off after checking the registry and persistence code.

## Delivery sequence and acceptance gates

| Phase | Deliverable | Exit criterion / proof |
|---|---|---|
| 0. Contract audit | Trace actual `MarketView → playbook → OptionsBridge → paper/live broker → portfolio` and source policy, identify missing broker/quote/order reconciliation behavior | Architect-reviewed seam diagram, failure matrix and reproducible current-state test; no assumed readiness from docs |
| 1. Paper reliability | Real broker-sourced chain/quotes in paper mode, contract/lot verification, fills/rejections, independent MTM and close/expiry accounting, restart recovery, segment risk | Deterministic unit/integration tests plus a recorded market-hours paper soak; reconcile intent, fills, open legs, cash and P&L; stale/missing data halts rather than simulates |
| 2. Guided builder MVP | Typed config, exact rule preview, supported preset → `MarketView`, linked versioned playbook, draft and paper launch | Golden-bar tests of signals; draft and paper produce identical rules; unsupported combinations rejected; no live button enabled by a syntax-only check |
| 3. Live controlled rollout | Reuse the same signal/playbook contract with broker-backed execution adapter; manual enable, low size, kill switch | Broker sandbox or tiny supervised live pilot only after paper evidence; ack/fill/partial fill/reject/timeout/duplicate/restart tests and independent broker-versus-internal position reconciliation |
| 4. Optional import | Pine as a *suggestion* for supported signal fields; explicit user review; no direct deployment | Pine/reference bar-level signal comparison; unsupported state/indicator semantics shown as blockers, not guessed |

These are dependency gates, not calendar promises. In particular **paper readiness precedes live**, and builder UI must not be mistaken for execution readiness.

## Critical invariants / test matrix

- **One source of truth:** strategy expresses directional intent; expression resolves broker-supplied option contracts; execution owns actual fill state. A PE buy is a long put, not a short underlying or naked short option. All fills and positions roll up into the **same portfolio across strategy types**, subject to the existing segment and paper/live separation; option-specific execution books are not separate portfolios.
- **No lookahead:** signals use trailing candles; action follows the engine's next-bar policy. Match historical and forward signal timing on identical bars; log bar timestamp, candle source and decision version.
- **Paper/live parity of decisions, not fabricated fill parity:** same signal, selector, risk and exit decisions under identical input snapshots; paper simulated fills and live broker fills may differ and must be visibly labelled. Never infer a fill merely from order submission.
- **Failure safety:** stale chain/LTP, absent contract, unavailable feed, session expiry, risk-cap breach, broker reject, partial fill, uncertain timeout, restart and emergency flatten must have specified halt/reconcile behavior. Idempotency keys per intent and broker order IDs; never retry an uncertain order blindly.
- **Test levels:** pure signal golden vectors; contract selection against recorded chains; exit-precedence/property tests; paper ledger/cash invariants; mock broker state-machine tests; supervised real-feed soak; limited live pilot with manual sign-off. Test both CE and PE, expiry rollover, gaps, and competing runners on one segment.
- **Observability:** intent ID, config/playbook versions, contract token, quote timestamp/source, requested and filled lots, broker order ID, risk decision, exit reason and reconciliation state, without leaking credentials.

## Non-goals and trade-offs

- **No second platform:** it duplicates feed, broker session, portfolio, risk and operational controls and creates divergent paper/live logic. A separate options UI/workspace is fine inside the same app.
- **No historical options performance claim:** chain history is missing. A synthetic chain may exercise mechanics in a testing profile only; it must never masquerade as real backtest evidence or paper fills.
- **No broad Pine transpilation in the critical path:** the supplied CE/PE v6 example is an `indicator` whose plots/SL lines are not orders; the consecutive-bars example relies on `:=` history. Their true trade lifecycle requires a user-defined execution contract. The form can represent a constrained equivalent signal after human review; it must not silently rewrite either script.
- **No profitability gate:** Sharpe belongs to research results. Correct conversion/execution is checked with signals, contracts, state transitions and accounting, not a minimum return.

## Decisions for architect to resolve before Phase 1

1. Which existing runner/execution seam is authoritative for option paper and live, including portfolio ledger and restart reconciliation? Confirm against code; avoid a second book.
2. Is V1 restricted to **buy CE / buy PE** with ATM and one expiry policy, or is a debit spread needed immediately? Define max loss semantics for each.
3. What real-time chain/quote freshness SLA, stale-data halt rule and broker capability constraints apply in market hours? What happens when no valid expiry/strike is available?
4. What exactly triggers a flip: close current structure then wait for next bar, or remain flat until a fresh signal? Specify stop/target units and fill-price reference.
5. What is the release gate for live enablement (paper soak duration, reconciliation tolerance, operator sign-off, kill-switch drill)? Define evidence rather than a Sharpe threshold.
6. Should builder definitions persist as first-class typed records or validated configs attached to existing strategy/playbook IDs? Decide after mapping registry/API ownership and migration cost.

**Proposed sign-off condition:** approve the layered contract and V1 constraints first; authorize implementation only after Phase 0 verifies current seams and the architect resolves the six decisions. This is intentionally smaller than a universal strategy builder and more directly supports the high-value option paper/live goal.
