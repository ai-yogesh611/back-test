/**
 * Backtest page controller (PRD Tasks 2.3, 2.9, 2.10).
 * Orchestrates: strategy dropdown + dynamic params, run, render results,
 * chart tabs, Save to Compare, Export CSV, Promote to Forward, pre-fill.
 */
let lastRun = null;          // {config, result}
let currentParams = {};      // schema for the selected strategy
let symbolPicker = null;     // components/symbol_picker.js handle

// --- Recent-run history (localStorage) --------------------------------------
// Each completed run is cached so it can be re-opened without re-running.
// Full trades/equity are kept (trimmed to the last 12 runs) — a completed
// backtest is deterministic for the same config + data range, so replaying
// the cached result is identical to re-running against the same source.
const RECENT_RUNS_KEY = "backtest_recent_runs";
const MAX_RECENT_RUNS = 12;

function getRecentRuns() {
    try { return JSON.parse(localStorage.getItem(RECENT_RUNS_KEY) || "[]"); }
    catch { return []; }
}

function saveRecentRun(run) {
    const runs = getRecentRuns();
    // same strategy+symbol+range+params → replace (it is the same test)
    const keyOf = (r) => JSON.stringify([r.config.strategy, r.config.symbol, r.config.timeframe, r.config.from_date, r.config.to_date, r.config.params]);
    const existing = runs.findIndex((r) => keyOf(r) === keyOf(run));
    if (existing >= 0) runs.splice(existing, 1);
    runs.unshift(run);
    while (runs.length > MAX_RECENT_RUNS) runs.pop();
    try { localStorage.setItem(RECENT_RUNS_KEY, JSON.stringify(runs)); }
    catch { /* storage full → drop trades, keep metrics */ }
    renderRecentRuns();
}

const $ = (id) => document.getElementById(id);

/** Server errors carry a request_id that also appears in the app log — quoting it
 *  here means a screenshot of a toast is enough to find the traceback. */
function _apiError(data, status) {
    const id = data && data.request_id ? ` [req ${data.request_id}]` : "";
    return `${(data && data.error) || `HTTP ${status}`}${id}`;
}

async function fetchJSON(url, opts) {
    const r = await fetch(url, opts);
    const data = await r.json();
    if (!r.ok) throw new Error(_apiError(data, r.status));
    return data;
}

// ---------------------------------------------------------------------------
// Dynamic params (Task 2.3)
// ---------------------------------------------------------------------------

function renderParams(params) {
    currentParams = params || {};
    renderParamsInto($("params-container"), currentParams);
}

function collectParams() {
    return collectParamsFrom($("params-container"));
}

function applyParamOverrides(overrides) {
    applyOverridesInto($("params-container"), overrides);
}

function collectConfig() {
    return {
        strategy: $("strategy").value,
        symbol: $("symbol").value,
        timeframe: timeframeValue(),
        from_date: $("fromDate").value,
        to_date: $("toDate").value,
        capital: Number($("capital").value) || 0,
        params: collectParams(),
        // Empty = canonical fill-exact engine. "quick_screen" is only ever an
        // explicit opt-in (the Fast Preview toggle) — never the default, so a
        // result can never silently come from the approximate path.
        mode: engineMode(),
    };
}

/**
 * The timeframe to send. §1.4: the dropdown only ever offers what the
 * selected symbol actually has cached, and never claims an intraday
 * granularity on a source that stores daily bars only.
 */
function timeframeValue() {
    const sel = $("timeframe");
    if (!sel || !sel.value) return "1day";
    const canonical = Timeframes.toCanonical(sel.value);
    const available = symbolPicker ? symbolPicker.timeframesFor($("symbol").value) : [];
    if (available.length && !available.includes(canonical)) {
        showToast(`${sel.value} has no data for ${$("symbol").value} — using ${available[0]}`, "warning");
        return available[0];
    }
    return canonical;
}

/** Requested engine mode: "" (full engine) or "quick_screen" (fast preview). */
function engineMode() {
    const box = $("fastPreview");
    return box && box.checked ? "quick_screen" : "";
}

// ---------------------------------------------------------------------------
// Run + render
// ---------------------------------------------------------------------------

async function runBacktest() {
    if (!$("strategy").value) { showToast("Select a strategy first", "warning"); return; }
    if (!$("fromDate").value || !$("toDate").value) { showToast("Pick a date range", "warning"); return; }

    const config = collectConfig();
    $("emptyState").hidden = true;
    $("results").hidden = false;
    showLoader("metricsCards", "Running backtest…");
    $("tradeTable-wrap").querySelector("tbody").innerHTML = "";
    $("pagination").innerHTML = "";

    try {
        const result = await fetchJSON("/api/backtest/run", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(config),
        });
        // §6: a handle for this rendered result, so the Optimize hand-off and
        // its audit chain have something to name. Backtests are stateless —
        // nothing about this run is persisted server-side.
        lastRun = { config, result, resultId: TuneThis.mintResultId() };
        renderResults(result);
        saveRecentRun(lastRun);
        showToast("Backtest complete", "success");
    } catch (err) {
        showToast(err.message || "Backtest failed", "error");
        $("results").hidden = true;
        $("emptyState").hidden = false;
    }
}

function renderResults(result) {
    // Engine + data provenance first: every number below it is read through
    // these two badges (PRD backTest-enhance §1.1/§1.2).
    if (typeof Provenance !== "undefined") Provenance.renderInto("resultProvenance", result.provenance);
    renderMetricsCards("metricsCards", result.metrics);
    // PRD §2.2: risk/tail, drawdown detail, trade quality and statistical
    // confidence, plus the insufficient-sample banner when it applies.
    if (typeof MetricSections !== "undefined") MetricSections.renderInto("metricSections", result.metrics);
    // PRD §3.1/§3.2/§3.3: buy-and-hold reference, slippage stress and trade
    // sequence resampling. Read from the same payload as the cards above, so a
    // check can never qualify a different result than the one being shown.
    if (typeof RunChecks !== "undefined") RunChecks.renderInto("runChecks", result);
    // PRD §5: the readiness summary, from the same payload as everything above.
    if (typeof Certification !== "undefined") Certification.renderInto("certification", result.readiness);
    // PRD §6: carry this result into Optimize. Never hidden — see the note in
    // tune_this.js about why a not-yet-certifiable result is precisely when
    // someone wants to tune.
    if (typeof TuneThis !== "undefined") TuneThis.renderInto("tuneThis", lastRun);
    TradeTable.render("tradeTable-wrap", result.trades);
    // default tab = equity; render lazily on tab switch
    renderChartForPane("equity");
}

function renderChartForPane(pane) {
    if (!lastRun) return;
    if (pane === "equity") renderEquityChart("equityChart", lastRun.result.equity);
    else if (pane === "drawdown") renderDrawdownChart("drawdownChart", lastRun.result.drawdown);
    else if (pane === "signals") renderSignalsChart("signalsChart", lastRun.result.signals);
}

// ---------------------------------------------------------------------------
// Actions (Tasks 2.9, 2.10, Promote)
// ---------------------------------------------------------------------------

function saveToCompare() {
    if (!lastRun) { showToast("Run a backtest first", "warning"); return; }
    const res = SessionState.addCompareSlot(lastRun);
    if (!res.ok) { showToast("Compare is full (4/4)", "warning"); return; }
    showToast(`Saved to Compare — slot ${res.index}/${SessionState.maxCompareSlots}`, "success");
}

function exportCsv() {
    if (!lastRun) { showToast("Run a backtest first", "warning"); return; }
    const trades = lastRun.result.trades;
    const header = ["id", "date", "exit_date", "side", "entry", "exit", "pnl", "result"];
    const lines = [header.join(",")];
    trades.forEach((t) => {
        lines.push([t.id, t.date, t.exit_date || "", t.side, t.entry, t.exit, t.pnl, t.result]
            .map(csvCell).join(","));
    });
    downloadFile("backtest_trades.csv", lines.join("\n"), "text/csv");
    showToast(`Exported ${trades.length} trades`, "success");
}

function csvCell(v) {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function downloadFile(name, content, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name; a.click();
    URL.revokeObjectURL(url);
}

function promoteToForward() {
    if (!lastRun) { showToast("Run a backtest first", "warning"); return; }
    SessionState.forwardPrefill = { config: lastRun.config };
    showToast("Pre-filled Forward Test — redirecting…", "success");
    setTimeout(() => { window.location.href = "/forward"; }, 500);
}

// ---------------------------------------------------------------------------
// Tabs + pre-fill
// ---------------------------------------------------------------------------

function wireTabs() {
    document.querySelectorAll(".tab").forEach((tab) => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
            document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
            tab.classList.add("active");
            const pane = tab.dataset.tab;
            document.querySelector(`.tab-pane[data-pane="${pane}"]`).classList.add("active");
            renderChartForPane(pane);
        });
    });
}

function showBanner(text) {
    const main = document.querySelector(".container");
    const b = document.createElement("div");
    b.className = "banner";
    b.textContent = text;
    main.prepend(b);
}

// ---------------------------------------------------------------------------
// Recent runs — tile row of already-tested configs with cached results.
// Clicking a tile restores the exact result (no re-run) + pre-fills the form.
// ---------------------------------------------------------------------------

function fmtDateRange(from, to) {
    const f = (d) => (d || "").split("-").reverse().join("/");
    return `${f(from)} – ${f(to)}`;
}

function renderRecentRuns() {
    const wrap = document.getElementById("recentRuns");
    if (!wrap) return;
    const runs = getRecentRuns();
    if (!runs.length) { wrap.hidden = true; wrap.innerHTML = ""; return; }
    wrap.hidden = false;
    wrap.innerHTML =
        `<div class="recent-head"><h2 class="card-title">Recent Runs</h2>` +
        `<button class="btn btn-ghost btn-small" id="clearRecentBtn" type="button">Clear</button></div>` +
        `<div class="recent-grid">` +
        runs.map((r, i) => {
            const m = r.result.metrics || {};
            const pnlCls = (m.total_pnl ?? 0) >= 0 ? "pos" : "neg";
            const wr = (typeof m.closed_trades === "number" && m.closed_trades === 0) ? "—" : `${(m.win_rate_pct ?? 0).toFixed(1)}%`;
            return `
            <div class="card recent-tile g-card-hover" data-idx="${i}" role="button" tabindex="0"
                 title="Open cached result — no re-run">
                <div class="recent-tile-top">
                    <strong>${r.config.strategy}</strong>
                    <span class="badge ${pnlCls === "pos" ? "bg-light-success" : "bg-light-danger"}">${fmtMoney(m.total_pnl)}</span>
                </div>
                <div class="recent-tile-sub muted">${r.config.symbol} · ${r.config.timeframe} · ${fmtDateRange(r.config.from_date, r.config.to_date)}</div>
                <div class="recent-tile-stats">
                    <span>WR ${wr}</span>
                    <span>DD ${(m.max_drawdown_pct ?? 0).toFixed(1)}%</span>
                    <span>${m.total_trades ?? 0} trades</span>
                    <span>Sharpe ${(m.sharpe ?? 0).toFixed(2)}</span>
                </div>
            </div>`;
        }).join("") +
        `</div>`;

    wrap.querySelector("#clearRecentBtn").addEventListener("click", () => {
        localStorage.removeItem(RECENT_RUNS_KEY);
        renderRecentRuns();
        showToast("Recent runs cleared", "info");
    });
    wrap.querySelectorAll(".recent-tile").forEach((tile) => {
        tile.addEventListener("click", () => openRecentRun(Number(tile.dataset.idx)));
    });
}

function openRecentRun(idx) {
    const run = getRecentRuns()[idx];
    if (!run) return;
    lastRun = run;
    const cfg = run.config;
    // pre-fill the form so "Run" would re-produce the same test
    try {
        $("strategy").value = cfg.strategy || "";
        if (cfg.symbol) $("symbol").value = cfg.symbol;
        if (cfg.timeframe) $("timeframe").value = cfg.timeframe;
        if (cfg.from_date) $("fromDate").value = cfg.from_date;
        if (cfg.to_date) $("toDate").value = cfg.to_date;
        if (cfg.capital) $("capital").value = cfg.capital;
    } catch { /* form nodes always present */ }
    fetchJSON(`/api/strategies/${encodeURIComponent(cfg.strategy)}/params`)
        .then((p) => { renderParams(p); applyParamOverrides(cfg.params); })
        .catch(() => { /* cached result still shown */ });
    $("emptyState").hidden = true;
    $("results").hidden = false;
    renderResults(run.result);
    renderChartForPane("equity");
    showToast(`Opened cached result — ${cfg.strategy} (${cfg.symbol}). No re-run needed.`, "info");
}

async function init() {
    wireTabs();

    // Which prices this run is measured on. A disabled source blocks the Run
    // button and says why, rather than letting it 409 on click.
    if (window.DataSourceGate) {
        DataSourceGate.mount("dataSourceGate", {
            status: $("dataSourceGate").dataset.status,
            blockIds: ["runBtn"],
        });
    }

    $("runBtn").addEventListener("click", runBacktest);
    $("saveCompareBtn").addEventListener("click", saveToCompare);
    $("exportCsvBtn").addEventListener("click", exportCsv);
    $("promoteBtn").addEventListener("click", promoteToForward);
    renderRecentRuns();

    // §1.3/§1.4: the picker and the timeframe dropdown are driven by what the
    // server says the chosen symbol actually has, so neither can offer a
    // phantom option.
    symbolPicker = SymbolPicker.mount({
        select: "symbol", search: "symbol-search", tabs: "symbol-tabs", summary: "symbol-status",
        onChange: () => Timeframes.applyTo($("timeframe"), symbolPicker.timeframesFor($("symbol").value)),
    });
    Timeframes.applyTo($("timeframe"), null);

    // load strategies
    let strategies = [];
    try {
        // venue=backtest: the server omits option strategies, because a
        // backtest runs on DB candles and the DB holds no historical chains.
        strategies = await fetchJSON("/api/strategies?venue=backtest");
        $("strategy").innerHTML = strategies
            .map((s) => `<option value="${s.name}">${s.name}</option>`).join("");
    } catch (err) {
        $("strategy").innerHTML = `<option value="">failed to load</option>`;
        showToast("Could not load strategies", "error");
    }

    // strategy change → dynamic params
    $("strategy").addEventListener("change", async () => {
        try {
            renderParams(await fetchJSON(`/api/strategies/${encodeURIComponent($("strategy").value)}/params`));
        } catch (err) { showToast(err.message, "error"); }
    });

    // pre-fill from Compare's "Open in Backtest" (Task 3.8 hand-off)
    const pre = SessionState.backtestPrefill;
    if (pre && pre.config && pre.config.strategy) {
        const cfg = pre.config;
        $("strategy").value = cfg.strategy;
        if (cfg.symbol) {
            symbolPicker.setValue(cfg.symbol);
            Timeframes.applyTo($("timeframe"), symbolPicker.timeframesFor(cfg.symbol));
        }
        if (cfg.timeframe) {
            const want = Timeframes.toCanonical(cfg.timeframe);
            const sel = $("timeframe");
            if (want && [...sel.options].some((o) => o.value === want)) sel.value = want;
        }
        if (cfg.from_date) $("fromDate").value = cfg.from_date;
        if (cfg.to_date) $("toDate").value = cfg.to_date;
        if (cfg.capital) $("capital").value = cfg.capital;
        try {
            renderParams(await fetchJSON(`/api/strategies/${encodeURIComponent(cfg.strategy)}/params`));
            applyParamOverrides(cfg.params);
        } catch { /* ignore param load errors */ }
        SessionState.clear(SessionState.keys.backtestPrefill);
        showBanner("Pre-filled from a saved comparison slot");
    } else if ($("strategy").value) {
        // default: render params for the first strategy
        try {
            renderParams(await fetchJSON(`/api/strategies/${encodeURIComponent($("strategy").value)}/params`));
        } catch { /* ignore */ }
    }
}

document.addEventListener("DOMContentLoaded", init);
