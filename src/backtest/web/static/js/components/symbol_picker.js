/**
 * Symbol picker — the ONE instrument list for Backtest, Compare and Optimize
 * (PRD backTest-enhance §1.3).
 *
 *   const picker = SymbolPicker.mount({ select, search, tabs, summary, onChange });
 *
 * Reads GET /api/data/coverage and renders:
 *   - a search box
 *   - All / Equity / Index / F&O filter tabs
 *   - a <select> where a symbol with no cached bars is still LISTED, greyed
 *     out, with the server's hint as its title ("No data loaded. Go to Data
 *     tab → fetch data for this symbol.")
 *   - a one-line summary: how many symbols are known, how many can actually run
 *
 * Before this, each page kept its own hard-coded <option> list, so a symbol
 * silently vanished unless the page's author had remembered to add it. The
 * server decides what is knowable; this file renders what it is told and
 * never invents or hides an entry.
 */
(function (global) {
    "use strict";

    const TABS = [
        { id: "", label: "All" },
        { id: "equity", label: "Equity" },
        { id: "index", label: "Index" },
        { id: "fno", label: "F&O" },
    ];

    const PAGE_SIZE = 500;
    const NO_DATA_TITLE = "No data loaded. Go to Data tab → fetch data for this symbol.";

    function esc(v) {
        return String(v === null || v === undefined ? "" : v).replace(/[&<>"']/g, (c) => (
            { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
        ));
    }

    /** Coverage string for an option label: "1min/1day · 1,247 bars". */
    function detailOf(row) {
        if (!row || !row.data_available) return "";
        const bits = [];
        if (row.timeframes_available && row.timeframes_available.length) {
            bits.push(row.timeframes_available.join("/"));
        }
        if (row.bars_count) bits.push(`${row.bars_count.toLocaleString()} bars`);
        return bits.join(" · ");
    }

    function _el(ref) {
        if (!ref) return null;
        return typeof ref === "string" ? document.getElementById(ref) : ref;
    }

    async function fetchCoverage(params) {
        const qs = new URLSearchParams(params || {}).toString();
        const r = await fetch(`/api/data/coverage${qs ? `?${qs}` : ""}`);
        const data = await r.json();
        if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
        return data;
    }

    function makeOption(row) {
        const opt = document.createElement("option");
        opt.value = row.symbol;
        // Readable label: "RELIANCE — 1day · 1,247 bars" when a display name
        // is absent; "RELIANCE (name) — …" when the catalogue has one. Raw
        // broker tokens (ISINs, NFO contract ids like 011NSETEST36DECFUT)
        // used to flood the dropdown; the default query now asks the server
        // for symbols WITH DATA only, which is exactly the set the user can
        // actually run — and after a fetch those are the readable names.
        const display = (row.name && row.name !== row.symbol && !/^[0-9A-Z]*[0-9][0-9A-Z]*$/.test(row.name))
            ? `${row.symbol} (${row.name})`
            : row.symbol;
        opt.textContent = display;
        if (!row.data_available) {
            // Listed, but not selectable: the user can see it exists and is told
            // where to get it, instead of it silently not being there.
            opt.disabled = true;
            opt.className = "sym-no-data";
            opt.title = row.hint || NO_DATA_TITLE;
        } else {
            const detail = detailOf(row);
            if (detail) opt.textContent = `${display} — ${detail}`;
            opt.title = [row.name, detail].filter(Boolean).join(" · ");
        }
        return opt;
    }

    function mount(opts) {
        const select = _el(opts.select);
        if (!select) return null;
        const search = _el(opts.search);
        const tabsEl = _el(opts.tabs);
        const summary = _el(opts.summary);
        const placeholder = opts.placeholder || "Select a symbol…";

        const state = {
            tab: "", rows: [], total: 0, known: 0, available: 0,
            dbAvailable: true, hint: "", error: null,
        };

        function render() {
            select.innerHTML = "";
            const head = document.createElement("option");
            head.value = "";
            head.textContent = placeholder;
            head.disabled = true;
            head.selected = true;
            select.appendChild(head);
            state.rows.forEach((row) => select.appendChild(makeOption(row)));

            if (!summary) return;
            if (state.error) {
                summary.textContent = `Could not load symbols — ${state.error}`;
                return;
            }
            if (state.dbAvailable === false) {
                summary.textContent = `${state.known} known · no data source connected`;
                return;
            }
            const runnable = state.rows.filter((r) => r.data_available).length;
            summary.textContent = `${state.rows.length} shown · ${runnable} with data · ${state.known} known`;
        }

        async function load() {
            try {
                // PRD change of direction (2026-09-30): the dropdown lists
                // ONLY symbols that have data. 500 disabled no-data rows of
                // broker catalogue noise made the readable symbols
                // unfindable; "if data is not available we do not even let
                // the user choose the symbol" is the requirement, and
                // available=1 gives exactly that set. The Data tab's own
                // coverage view remains the place to see what could be
                // fetched.
                const params = { limit: PAGE_SIZE, available: 1 };
                if (search && search.value.trim()) params.q = search.value.trim();
                if (state.tab) params.types = state.tab;
                const data = await fetchCoverage(params);
                state.rows = data.instruments || [];
                state.total = data.total || 0;
                state.known = data.known_total || 0;
                state.available = data.available_total || 0;
                state.hint = data.hint || "";
                state.dbAvailable = data.db_available;
                state.error = null;
            } catch (err) {
                state.error = err.message || String(err);
                state.rows = [];
            }
            render();
        }

        if (search) {
            let timer = null;
            search.addEventListener("input", () => {
                clearTimeout(timer);
                timer = setTimeout(load, 250);
            });
        }
        if (tabsEl) {
            tabsEl.innerHTML = TABS.map((t, i) => (
                `<button type="button" class="sym-tab${i === 0 ? " active" : ""}" data-tab="${t.id}">${t.label}</button>`
            )).join("");
            tabsEl.addEventListener("click", (e) => {
                const btn = e.target.closest("[data-tab]");
                if (!btn) return;
                state.tab = btn.dataset.tab;
                tabsEl.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b === btn));
                load();
            });
        }
        if (opts.onChange) select.addEventListener("change", () => opts.onChange(select.value));

        const handle = {
            select, search, summary, state,
            load, refresh: load,
            /** Select a symbol, injecting it if it is not on the loaded page. */
            setValue(symbol) {
                if (!symbol) return;
                if (state.rows.some((r) => r.symbol === symbol)) { select.value = symbol; return; }
                const row = { symbol, name: symbol, data_available: true, bars_count: 0 };
                select.insertBefore(makeOption(row), select.firstChild);
                select.value = symbol;
            },
            /** Timeframes the server says this symbol really has. */
            timeframesFor(symbol) {
                const row = state.rows.find((r) => r.symbol === symbol);
                return row && row.timeframes_available ? row.timeframes_available.slice() : [];
            },
            /**
             * Symbols currently loaded, for the Test Generalization slot
             * pickers (PRD §4.2). Returns the loaded page only — callers that
             * need a different symbol should use `setValue` first so the server
             * has a chance to inject it.
             */
            symbols() {
                return state.rows.map((r) => r.symbol).filter(Boolean);
            },
        };

        render();
        load();
        return handle;
    }

    const SymbolPicker = { TABS, NO_DATA_TITLE, mount, esc, detailOf, fetchCoverage };
    global.SymbolPicker = SymbolPicker;
    if (typeof module !== "undefined" && module.exports) module.exports = SymbolPicker;
}(typeof window !== "undefined" ? window : globalThis));
