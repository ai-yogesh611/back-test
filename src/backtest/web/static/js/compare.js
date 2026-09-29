/**
 * Compare page controller (PRD Tasks 3.3, 3.4, 3.8).
 * Slot management (add/remove, independent strategy + params), Run All →
 * /api/backtest/run-many, render 3 views, per-slot Open-in-Backtest / Promote.
 */
const PALETTE = ["#3b82f6", "#d4b26a", "#7fc8a0", "#e0938f"]; // blue, orange, green, red
// Slot timeframes are filled per-slot from the shared symbol's real coverage
// (§1.4) — see applyTimeframesToSlots(). No static list of phantom options.

let strategies = [];          // [{name,...}]
let slots = [];               // [{id, color, card, strategy, timeframe, runConfig, result, label}]
let nextId = 1;
let lastResults = null;       // successful slots from last run (for tab charts)
let lastProvenance = null;    // provenance of the SHARED conditions (engine + data)
let symbolPicker = null;      // components/symbol_picker.js handle

const $ = (id) => document.getElementById(id);
/** Server errors carry a request_id that also appears in the app log — quoting it
 *  here means a screenshot of a toast is enough to find the traceback. */
function _apiError(data, status) {
    const id = data && data.request_id ? ` [req ${data.request_id}]` : "";
    return `${(data && data.error) || `HTTP ${status}`}${id}`;
}

async function fetchJSON(url, opts) {
    const r = await fetch(url, opts);
    const d = await r.json();
    if (!r.ok) throw new Error(_apiError(d, r.status));
    return d;
}

// ---------------------------------------------------------------------------
// Param rendering is provided by components/params_form.js (shared).
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Slot management (Task 3.3)
// ---------------------------------------------------------------------------

function strategiesOptions(selected) {
    return strategies.map((s) => {
        const kindBadge = s.signal_kind === "option" ? " [Options]" : "";
        return `<option value="${s.name}" ${s.name === selected ? "selected" : ""}>${s.name}${kindBadge}</option>`;
    }).join("");
}

function addSlot(prefill) {
    if (slots.length >= 4) { showToast("Maximum of 4 slots", "warning"); return null; }
    const id = nextId++;
    const color = PALETTE[slots.length % PALETTE.length];
    const card = document.createElement("div");
    card.className = "slot-card";
    card.style.borderTopColor = color;
    card.dataset.id = id;
    card.innerHTML = `
        <div class="slot-head">
            <span class="slot-label"><span class="slot-dot" style="background:${color}"></span> <span class="slot-num">Slot ${slots.length + 1}</span></span>
            <button class="btn-icon remove-slot" title="Remove">✕</button>
        </div>
        <div class="form-row"><label>Strategy</label><select class="slot-strategy input"></select></div>
        <div class="form-row"><label>Timeframe</label><select class="slot-tf input"></select></div>
        <div class="slot-params"></div>
        <div class="slot-status muted small"></div>`;
    $("slotsRow").appendChild(card);

    const stratSel = card.querySelector(".slot-strategy");
    const tfSel = card.querySelector(".slot-tf");
    // §1.4: a new slot offers what the shared symbol actually has, not a
    // fixed list of granularities that may not exist.
    const offered = Timeframes.applyTo(tfSel, symbolPicker ? symbolPicker.timeframesFor($("symbol").value) : null);
    const slot = { id, color, card, strategy: "", timeframe: tfSel.value || (offered.length ? offered[offered.length - 1] : "1day"),
                   runConfig: null, result: null, label: "" };
    stratSel.addEventListener("change", () => onStrategyChange(slot, stratSel.value));
    tfSel.addEventListener("change", () => { slot.timeframe = tfSel.value; updateLabel(slot); });
    card.querySelector(".remove-slot").addEventListener("click", () => removeSlot(slot));

    slots.push(slot);

    const cfg = prefill && prefill.config;
    if (cfg && cfg.strategy) {
        stratSel.innerHTML = strategiesOptions(cfg.strategy);
        slot.strategy = cfg.strategy;
        if (cfg.timeframe) {
            const want = Timeframes.toCanonical(cfg.timeframe);
            if (want && [...tfSel.options].some((o) => o.value === want)) {
                tfSel.value = want;
                slot.timeframe = want;
            }
        }
        onStrategyChange(slot, cfg.strategy, cfg.params);
    } else if (strategies.length) {
        stratSel.innerHTML = strategiesOptions(strategies[0].name);
        slot.strategy = strategies[0].name;
        onStrategyChange(slot, strategies[0].name);
    } else {
        stratSel.innerHTML = '<option value="">loading…</option>';
    }
    relabelSlots();
    refreshControls();
    return slot;
}

function removeSlot(slot) {
    if (slots.length <= 1) { showToast("Keep at least one slot", "warning"); return; }
    slot.card.remove();
    slots = slots.filter((s) => s !== slot);
    relabelSlots();
    refreshControls();
}

async function onStrategyChange(slot, name, overrides) {
    slot.strategy = name;
    try {
        const params = await fetchJSON(`/api/strategies/${encodeURIComponent(name)}/params`);
        renderParamsInto(slot.card.querySelector(".slot-params"), params, overrides);
    } catch (err) { showToast(err.message, "error"); }
    updateLabel(slot);
}

function updateLabel(slot) {
    slot.label = `${slot.strategy} ${slot.timeframe}`;
}

function relabelSlots() {
    slots.forEach((s, i) => {
        s.card.querySelector(".slot-num").textContent = `Slot ${i + 1}`;
        s.card.style.borderTopColor = PALETTE[i % PALETTE.length];
        s.color = PALETTE[i % PALETTE.length];
        s.card.querySelector(".slot-dot").style.background = s.color;
    });
}

function refreshControls() {
    $("addSlotBtn").style.display = slots.length >= 4 ? "none" : "";
}

// ---------------------------------------------------------------------------
// Run All (Task 3.4)
// ---------------------------------------------------------------------------

function sharedConfig() {
    return {
        symbol: $("symbol").value, from_date: $("fromDate").value,
        to_date: $("toDate").value, capital: Number($("capital").value) || 0,
        // Engine is a SHARED condition, not a per-slot one: comparing a
        // fill-exact slot against a quick-screen slot produces two numbers
        // that cannot be read side by side. Applied to every slot below.
        mode: engineMode(),
    };
}

/** Requested engine mode: "" (full engine) or "quick_screen" (fast preview). */
function engineMode() {
    const box = $("fastPreview");
    return box && box.checked ? "quick_screen" : "";
}

function slotConfig(slot) {
    return {
        strategy: slot.strategy, symbol: $("symbol").value,
        timeframe: slot.timeframe, from_date: $("fromDate").value,
        to_date: $("toDate").value, capital: Number($("capital").value) || 0,
        params: collectParamsFrom(slot.card.querySelector(".slot-params")),
    };
}

async function runAll() {
    if (!$("fromDate").value || !$("toDate").value) { showToast("Pick a date range", "warning"); return; }
    const empty = slots.filter((s) => !s.strategy);
    if (empty.length) { showToast("Every slot needs a strategy", "warning"); return; }

    const payload = {
        shared: sharedConfig(),
        slots: slots.map((s) => ({ id: s.id, strategy: s.strategy, timeframe: s.timeframe,
                                    mode: engineMode(),
                                    params: collectParamsFrom(s.card.querySelector(".slot-params")) })),
    };

    slots.forEach((s) => { s.result = null; s.card.querySelector(".slot-status").innerHTML = '<span class="muted">Running…</span>'; });
    $("emptyState").hidden = true;
    $("results").hidden = false;
    showLoader("compareTable", "Running all slots…");

    try {
        const data = await fetchJSON("/api/backtest/run-many", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        slots.forEach((s) => {
            const r = data.results[String(s.id)];
            s.result = r || null;
            s.runConfig = slotConfig(s);
            const st = s.card.querySelector(".slot-status");
            if (!r) st.innerHTML = '<span class="neg">no result</span>';
            else if (r.error) st.innerHTML = `<span class="neg">⚠ ${r.error}</span>`;
            else st.innerHTML = `<span class="pos">✓ ${(r.metrics.total_return_pct >= 0 ? "+" : "") + r.metrics.total_return_pct.toFixed(2)}%</span>`;
        });

        const ok = slots.filter((s) => s.result && !s.result.error);
        document.getElementById("compareTable").innerHTML = "";   // clear loader
        if (!ok.length) { showToast("All slots failed", "error"); $("results").hidden = true; $("emptyState").hidden = false; return; }
        lastResults = ok;
        lastProvenance = data.provenance || null;
        renderResults(ok);
        showToast(`Compared ${ok.length} slot${ok.length > 1 ? "s" : ""}`, "success");
    } catch (err) {
        showToast(err.message || "Run failed", "error");
        document.getElementById("compareTable").innerHTML = "";
        $("results").hidden = true;
        $("emptyState").hidden = false;
    }
}

function renderResults(okSlots) {
    // Engine + data provenance for the SHARED conditions every slot ran under
    // (PRD backTest-enhance §1.1/§1.2). Slots that disagreed on the engine are
    // stamped "mixed" and the server's warning is shown alongside.
    if (typeof Provenance !== "undefined") Provenance.renderInto("compareProvenance", lastProvenance);
    renderCompareTable("compareTable", okSlots, onSlotAction);
    renderChartForPane(document.querySelector(".tab.active")?.dataset.tab || "metrics");
}

function renderChartForPane(pane) {
    if (!lastResults) return;
    if (pane === "equity") renderCompareEquity("equityCompareChart", lastResults);
    else if (pane === "drawdown") renderCompareDrawdown("drawdownCompareChart", lastResults);
}

// ---------------------------------------------------------------------------
// Per-slot actions (Task 3.8)
// ---------------------------------------------------------------------------

function onSlotAction(slot, kind) {
    const config = slot.runConfig || slotConfig(slot);
    if (kind === "backtest") {
        SessionState.backtestPrefill = { config };
        showToast("Opening in Backtest…", "success");
        setTimeout(() => { window.location.href = "/backtest"; }, 400);
    } else {
        SessionState.forwardPrefill = { config };
        showToast("Promoting to Forward…", "success");
        setTimeout(() => { window.location.href = "/forward"; }, 400);
    }
}

// ---------------------------------------------------------------------------
// Tabs + init
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

/**
 * Give every slot the timeframes the shared symbol REALLY has (§1.4).
 * Slots are compared against each other, so they may legitimately differ in
 * timeframe — but no slot may offer a granularity with no bars behind it.
 * `applyTo` keeps a still-valid selection and otherwise falls back to the
 * coarsest one the symbol has, so a slot is never left pointing at nothing.
 */
function applyTimeframesToSlots() {
    const available = symbolPicker ? symbolPicker.timeframesFor($("symbol").value) : null;
    slots.forEach((s) => {
        const sel = s.card.querySelector(".slot-tf");
        const offered = Timeframes.applyTo(sel, available);
        s.timeframe = sel.value || (offered.length ? offered[offered.length - 1] : "1day");
    });
}

async function init() {
    wireTabs();
    $("addSlotBtn").addEventListener("click", () => addSlot());
    $("runAllBtn").addEventListener("click", runAll);

    // §1.3/§1.4 — one picker for the page; the slot timeframes follow it.
    symbolPicker = SymbolPicker.mount({
        select: "symbol", search: "symbol-search", tabs: "symbol-tabs", summary: "symbol-status",
        onChange: applyTimeframesToSlots,
    });

    try {
        strategies = await fetchJSON("/api/strategies");
    } catch (err) {
        showToast("Could not load strategies", "error");
        strategies = [];
    }

    const saved = SessionState.compareSlots;
    if (saved && saved.length) {
        saved.slice(0, 4).forEach((s) => addSlot(s));
        showToast(`Loaded ${Math.min(saved.length, 4)} saved slot(s)`, "success");
    } else {
        addSlot(); addSlot();   // start with 2 slots
    }
}

document.addEventListener("DOMContentLoaded", init);
