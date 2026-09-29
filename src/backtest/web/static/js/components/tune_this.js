/**
 * "Tune This" — the Backtest → Optimize hand-off (PRD backTest-enhance §6).
 *   TuneThis.mount("tuneThis", () => lastRun)
 *   TuneThis.buildPrefill(run) -> optimize prefill object
 *
 * The button navigates to the Optimize setup form with every common field
 * already filled in. It does NOT start an optimization: the user reviews the
 * form and presses Start themselves, because a search that begins the moment
 * you look at it is a search you never chose.
 *
 * The result ID
 * -------------
 * Backtests are stateless — nothing about a completed run is persisted, so
 * there is no server-side id to quote. `buildPrefill` mints a short session
 * handle instead, and the UI labels it as such. It identifies *this rendered
 * result in this session* and nothing more; it is a handle for the audit
 * chain in §6's reverse flow, not a stored backtest record. Claiming
 * otherwise would put a convincing-looking id in an audit log that resolves to
 * nothing.
 *
 * Why the button is never hidden
 * ------------------------------
 * §5 lists the button under "if all green", which reads like a gate. It is not
 * used as one. The most ordinary reason to open Optimize is that the backtest
 * above is mediocre — a weak result is exactly what you tune — so hiding the
 * button unless the result is already certifiable would block the workflow the
 * feature exists for. The button is always available; when readiness is not
 * all-green the hint says plainly that Optimize finds better parameters for
 * this strategy and does not repair the problems the readiness panel listed.
 */
(function (global) {
    "use strict";

    const WF_TRAIN_FRACTION = 2 / 3;
    //: The Optimize form's own minimums. A split that cannot clear them is
    //: worse than no split: the server would reject the run at submit time,
    //: after the user has already spent time filling in the form.
    const WF_MIN_TRAIN = 5;
    const WF_MIN_TEST = 2;

    const ENGINE_LABELS = {
        driver: "Fill-Exact (Canonical)",
        quick_screen: "Quick-Screen (Approximate)",
        options: "Options engine",
    };

    function esc(v) {
        return String(v === null || v === undefined ? "" : v)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    /** A short, collision-resistant-enough handle for one rendered result. */
    function mintResultId() {
        const rand = Math.random().toString(36).slice(2, 8);
        return `bt_${Date.now().toString(36)}${rand}`;
    }

    function daysBetween(from, to) {
        const a = Date.parse(`${from}T00:00:00Z`);
        const b = Date.parse(`${to}T00:00:00Z`);
        if (!isFinite(a) || !isFinite(b) || b <= a) return 0;
        return Math.round((b - a) / 86400000);
    }

    /**
     * Walk-forward defaults: train on two thirds of the window, test on the
     * rest, and step by the test length (the standard convention — a longer
     * step skips bars, a shorter one re-tests the same ones).
     *
     * A window too short to clear the form's minimums gets walk-forward
     * switched OFF with a reason, rather than a split the server rejects.
     */
    function walkForwardFor(from, to) {
        const total = daysBetween(from, to);
        if (!total) {
            return { enabled: false, reason: "The backtest has no measurable date range." };
        }
        let train = Math.round(total * WF_TRAIN_FRACTION);
        let test = total - train;
        if (train < WF_MIN_TRAIN) train = WF_MIN_TRAIN;
        if (test < WF_MIN_TEST) test = WF_MIN_TEST;
        if (train + test > total) {
            return {
                enabled: false,
                reason: `A ${total}-day window cannot be split into a `
                    + `${WF_MIN_TRAIN}-day training and ${WF_MIN_TEST}-day test period.`,
            };
        }
        return { enabled: true, trainPeriodDays: train, testPeriodDays: test, stepDays: test };
    }

    /**
     * The whole §6 prefill, built from a completed backtest result.
     * Returns null when the result is too thin to hand over.
     */
    function buildPrefill(run) {
        if (!run || !run.result) return null;
        const cfg = run.result.config || run.config || {};
        const strategy = cfg.strategy;
        const symbol = cfg.symbol;
        const from = cfg.from_date;
        const to = cfg.to_date;
        if (!strategy || !symbol || !from || !to) return null;

        // §6 "engine from current result (whichever was used)". The Optimize
        // vocabulary is ("driver", "quick_screen", "options"); the backtest
        // spells the canonical engine as "".
        const rawEngine = String(cfg.engine || "").trim().toLowerCase();
        const engine = rawEngine || "driver";

        const wf = walkForwardFor(from, to);
        const prov = run.result.provenance || {};
        return {
            resultId: run.resultId || mintResultId(),
            strategyId: strategy,
            symbol: String(symbol).toUpperCase(),
            startDate: from,
            endDate: to,
            timeframe: cfg.timeframe || "1day",
            initialCapital: Number(cfg.capital) || 100000,
            engine,
            engineLabel: ENGINE_LABELS[engine] || engine,
            // §6 "data_source locked to same source".
            source: prov.data_source || run.source || "",
            params: (cfg.params && typeof cfg.params === "object") ? cfg.params : {},
            objectiveFunction: "sharpe",
            method: "bayesian",
            walkForward: wf,
            // Purely informational: the panel's own words, carried so the
            // Optimize page can say why the result is not certifiable.
            readiness: run.result.readiness || null,
        };
    }

    function render(container, run) {
        const el = document.getElementById(container);
        if (!el) return;
        const prefill = buildPrefill(run);
        if (!prefill) { el.innerHTML = ""; return; }

        const rd = prefill.readiness;
        const ready = rd && rd.all_green;
        const hint = ready
            ? `<span class="pos">All eight readiness checks passed.</span>`
            : `<span class="neg">This result is <strong>not</strong> certifiable yet. `
              + `Optimize looks for better parameters <em>for this strategy</em> — it does not `
              + `fix ${esc(((rd && rd.red_flags) || []).concat((rd && rd.unproven) || [])
                  .slice(0, 3).join(", ") || "the items listed above")}.</span>`;

        el.innerHTML = `<div class="tune-this">
            <div class="tune-this-head">
                <span class="tune-this-title">⚙ Tune This</span>
                <span class="tune-this-sub muted small">Carries this result into Optimize, ready to review. Nothing is run until you press Start.</span>
            </div>
            <div class="tune-this-body">
                <ul class="tune-this-fields">
                    <li><span>Strategy</span><strong>${esc(prefill.strategyId)}</strong></li>
                    <li><span>Symbol</span><strong>${esc(prefill.symbol)}</strong></li>
                    <li><span>Period</span><strong>${esc(prefill.startDate)} → ${esc(prefill.endDate)}</strong></li>
                    <li><span>Timeframe</span><strong>${esc(prefill.timeframe)}</strong></li>
                    <li><span>Capital</span><strong>${esc(prefill.initialCapital)}</strong></li>
                    <li><span>Engine</span><strong>${esc(prefill.engineLabel)}</strong></li>
                    <li><span>Objective</span><strong>${esc(prefill.objectiveFunction)}</strong></li>
                    <li><span>Method</span><strong>${esc(prefill.method)}</strong></li>
                    <li><span>Walk-forward</span><strong>${
                        prefill.walkForward.enabled
                            ? `${prefill.walkForward.trainPeriodDays}d train / ${prefill.walkForward.testPeriodDays}d test`
                            : "off — window too short"
                    }</strong></li>
                </ul>
                <p class="tune-this-hint">${hint}</p>
                <p class="tune-this-id muted small">Result ID (this session): <code>${esc(prefill.resultId)}</code></p>
                <button class="btn btn-primary" id="tuneThisBtn">⚙ Open in Optimize →</button>
            </div>
        </div>`;

        const btn = document.getElementById("tuneThisBtn");
        if (btn) btn.addEventListener("click", () => global.TuneThis.go(prefill));
    }

    const TuneThis = {
        render,
        renderInto: render,
        buildPrefill,
        walkForwardFor,
        mintResultId,
        go(prefill) {
            if (!prefill) return;
            SessionState.optimizePrefill = prefill;
            showToast("Opening Optimize with your backtest carried over…", "success");
            setTimeout(() => { window.location.href = "/optimize"; }, 400);
        },
        mount(container, getRun) {
            render(container, getRun());
        },
    };

    global.TuneThis = TuneThis;
    if (typeof module !== "undefined" && module.exports) module.exports = TuneThis;
}(typeof window !== "undefined" ? window : globalThis));
