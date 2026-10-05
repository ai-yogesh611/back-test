/**
 * Symbol picker + timeframe vocabulary — behaviour tests
 * (PRD backTest-enhance §1.3 + §1.4).
 *
 * What is pinned here:
 *  - a symbol with no cached bars is LISTED, disabled, and carries the
 *    server's fetch hint as its title (the §1.3 bug: it silently vanished)
 *  - the filter tabs exist and re-query with the right `types`
 *  - the timeframe dropdown only ever offers granularities the server says
 *    exist, and never empties
 *  - periodsPerYear is the number the engine uses, so an intraday run is not
 *    annualised with a daily factor
 *
 * Usage: node tests/js/test_symbol_picker.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../", import.meta.url));
const read = (p) => readFileSync(path.join(root, p), "utf8");
const pickerCode = read("src/backtest/web/static/js/components/symbol_picker.js");
const tfCode = read("src/backtest/web/static/js/components/timeframes.js");

// ------------------------------------------------------------------ tiny DOM
function makeOption(text, value) {
    return { textContent: "", value: "", disabled: false, className: "", title: "", text };
}
function makeEl(id) {
    const el = {
        id, value: "", textContent: "", options: [],
        _children: [], className: "", dataset: {},
        appendChild(child) { this._children.push(child); if (child.value !== "") this.options.push(child); },
        insertBefore(child) { this._children.unshift(child); this.options.unshift(child); },
        addEventListener() {},
        querySelectorAll() { return []; },
        get firstChild() { return this._children[0] || null; },
    };
    // innerHTML assignment is how the modules render, so the stub mirrors the
    // DOM's one observable side effect: the <option> list it implies.
    let html = "";
    Object.defineProperty(el, "innerHTML", {
        get() { return html; },
        set(v) {
            html = String(v);
            this.options = [...html.matchAll(/<option value="([^"]*)"/g)]
                .map((m) => ({ value: m[1], textContent: m[1] }));
        },
    });
    Object.defineProperty(el, "children", { get: () => el._children });
    return el;
}

const elements = {};
const el = (id) => (elements[id] = elements[id] || makeEl(id));

/** Server response for the seeded coverage fixture.
 *
 * The two timeframe fields deliberately DISAGREE, because that is the real
 * shape and the distinction the label bug was made of: RELIANCE holds only
 * 1-minute bars but can serve eight coarser granularities on top of that.
 */
const COVERAGE = {
    instruments: [
        { symbol: "RELIANCE", name: "Reliance Industries Ltd.", instrument_type: "equity",
          data_available: true, bars_count: 1247, from_date: "2020-01-01", to_date: "2024-12-31",
          timeframes_stored: ["1min"],
          timeframes_available: ["1min", "5min", "10min", "15min", "30min", "1hour", "4hour", "1day"] },
        { symbol: "NIFTY", name: "NIFTY 50", instrument_type: "index",
          data_available: true, bars_count: 1247, from_date: "2020-01-01", to_date: "2024-12-31",
          timeframes_stored: ["1day"], timeframes_available: ["1day", "1week"] },
        { symbol: "TCS", name: "Tata Consultancy Services", instrument_type: "equity",
          data_available: false, bars_count: 0, from_date: null, to_date: null,
          timeframes_stored: [], timeframes_available: [],
          hint: "No data loaded. Go to Data tab → fetch data for this symbol." },
    ],
    total: 3, returned: 3, known_total: 206, available_total: 2,
    hidden_total: 1,
    db_available: true, catalogue_source: "market_data_cache",
    instrument_types: ["equity", "index", "futures", "options"],
    hint: "No data loaded. Go to Data tab → fetch data for this symbol.",
    generated_at: "2026-09-29",
};

const calls = [];
const sandbox = {
    console,
    document: {
        getElementById: el,
        createElement: () => makeOption(),
        addEventListener() {},
    },
    fetch: (url) => {
        calls.push(url);
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(COVERAGE) });
    },
    URLSearchParams, Object, Array, JSON, Number, String, Promise, setTimeout, clearTimeout,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(tfCode, sandbox, { filename: "timeframes.js" });
vm.runInContext(pickerCode, sandbox, { filename: "symbol_picker.js" });

const { Timeframes, SymbolPicker } = sandbox;

let passed = 0;
/** Cross-realm arrays (created inside the VM) fail deepStrictEqual; compare by value. */
const eqList = (actual, expected) =>
    assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected);
const tick = () => new Promise((r) => setImmediate(r));
async function test(name, fn) {
    await fn();
    passed += 1;
    console.log(`  ✓ ${name}`);
}
function assertNoThrow(fn) { assert.doesNotThrow(fn); }

// ------------------------------------------------------------- timeframes
await test("canonical names round-trip through UI labels", () => {
    assert.equal(Timeframes.toCanonical("1D"), "1day");
    assert.equal(Timeframes.toCanonical("1d"), "1day");
    assert.equal(Timeframes.toCanonical("day"), "1day");
    assert.equal(Timeframes.toCanonical("1H"), "1hour");
    assert.equal(Timeframes.toCanonical("60min"), "1hour");
    assert.equal(Timeframes.toCanonical("1W"), "1week");
    assert.equal(Timeframes.labelFor("15min"), "15m");
    assert.equal(Timeframes.labelFor("1hour"), "1H");
});

await test("an unrecognised timeframe is reported, not guessed at", () => {
    assert.equal(Timeframes.toCanonical("bogus"), null);
    assert.equal(Timeframes.toCanonical(""), null);
    assert.equal(Timeframes.toCanonical(null), null);
});

await test("periods_per_year matches the PRD's annualisation table", () => {
    // 252 trading days x NSE session minutes, weekly = 52 weeks.
    assert.equal(Timeframes.periodsPerYear("1min"), 252 * 375);
    assert.equal(Timeframes.periodsPerYear("5min"), 252 * 75);
    assert.equal(Timeframes.periodsPerYear("15min"), 252 * 25);
    assert.equal(Timeframes.periodsPerYear("1hour"), 252 * 6);
    assert.equal(Timeframes.periodsPerYear("1day"), 252);
    assert.equal(Timeframes.periodsPerYear("1week"), 52);
});

await test("periods_per_year falls back to daily rather than raising", () => {
    assert.equal(Timeframes.periodsPerYear("bogus"), 252);
    assert.equal(Timeframes.periodsPerYear(null), 252);
});

await test("coarser bars annualise strictly less often", () => {
    let prev = Infinity;
    ["1min", "5min", "15min", "1hour", "4hour", "1day", "1week"].forEach((tf) => {
        const ppy = Timeframes.periodsPerYear(tf);
        assert.ok(ppy < prev, `${tf} must annualise less often than the next finer one`);
        prev = ppy;
    });
    // and the whole ladder stays above daily except at/below it
    assert.ok(Timeframes.periodsPerYear("1hour") > Timeframes.periodsPerYear("1day"));
});

await test("the timeframe dropdown offers ONLY what the symbol has", () => {
    const sel = makeEl("tf-daily-only");
    const offered = Timeframes.applyTo(sel, ["1day"]);
    eqList(offered, ["1day"]);
    assert.equal(sel.options.length, 1);
    assert.match(sel.innerHTML, /<option value="1day">1D<\/option>/);
    assert.ok(!sel.innerHTML.includes("1min"), "a phantom intraday option is exactly the bug");
});

await test("a symbol with 1min+1day offers both, coarsest last-selected by default", () => {
    const sel = makeEl("tf-both");
    const offered = Timeframes.applyTo(sel, ["1min", "1day"]);
    eqList(offered, ["1min", "1day"]);
    assert.equal(sel.value, "1day");
});

await test("a still-valid selection survives a coverage refresh", () => {
    const sel = makeEl("tf-keep");
    Timeframes.applyTo(sel, ["1min", "1day"]);
    sel.value = "1min";
    Timeframes.applyTo(sel, ["1min", "1day"]);
    assert.equal(sel.value, "1min");
});

await test("a selection that is no longer available falls back, never blanks", () => {
    const sel = makeEl("tf-fallback");
    Timeframes.applyTo(sel, ["1min", "1day"]);
    sel.value = "1min";
    Timeframes.applyTo(sel, ["1day"]);
    assert.equal(sel.value, "1day");
});

await test("unknown coverage leaves the full list (absence is not evidence)", () => {
    const sel = makeEl("tf-unknown");
    assert.equal(Timeframes.applyTo(sel, null).length, Timeframes.TIMEFRAMES.length);
    assert.equal(Timeframes.applyTo(sel, []).length, Timeframes.TIMEFRAMES.length);
});

// ------------------------------------------------------------ symbol picker
await test("mounting renders the coverage the server sent", async () => {
    calls.length = 0;
    const picker = SymbolPicker.mount({ select: "p1", search: "p1s", tabs: "p1t", summary: "p1sum" });
    await tick();
    const sel = el("p1");
    const values = sel.options.map((o) => o.value);
    assert.ok(values.includes("RELIANCE"));
    assert.ok(values.includes("NIFTY"));
    assert.ok(values.includes("TCS"));
    assert.ok(calls[0].startsWith("/api/data/coverage"));
});

await test("a symbol with NO data is listed, disabled, and explains itself", async () => {
    const picker = SymbolPicker.mount({ select: "p2", search: "p2s", tabs: "p2t", summary: "p2sum" });
    await tick();
    const tcs = el("p2").options.find((o) => o.value === "TCS");
    assert.ok(tcs, "TCS must still be listed — hiding it was the §1.3 bug");
    assert.equal(tcs.disabled, true);
    assert.match(tcs.title, /No data loaded/);
    assert.match(tcs.title, /Data tab/);
    assert.equal(tcs.className, "sym-no-data");
});

await test("a symbol WITH data is selectable and labelled with its coverage", async () => {
    SymbolPicker.mount({ select: "p3", search: "p3s", tabs: "p3t", summary: "p3sum" });
    await tick();
    const rel = el("p3").options.find((o) => o.value === "RELIANCE");
    assert.equal(rel.disabled, false);
    assert.match(rel.textContent, /RELIANCE/);
    assert.match(rel.textContent, /1min\b/);
    assert.match(rel.textContent, /1,247 bars/);
    assert.match(rel.title, /Reliance Industries/);
});

await test("the label lists what is STORED, never the derived set", async () => {
    // The regression: `label = timeframes_available` printed the same eight-item
    // list under every instrument that holds 1min data — identical text, no
    // information about the symbol it sat beside.
    SymbolPicker.mount({ select: "p6", search: "p6s", tabs: "p6t", summary: "p6sum" });
    await tick();
    const rel = el("p6").options.find((o) => o.value === "RELIANCE");
    assert.match(rel.textContent, /1min ·/, "expected the stored timeframe");
    for (const derived of ["5min", "15min", "1hour", "4hour", "1day"]) {
        assert.ok(
            !rel.textContent.includes(derived),
            `derived timeframe ${derived} must not appear in the label: "${rel.textContent}"`
        );
    }
});

await test("the dropdown still offers the DERIVED set the server reports", async () => {
    // The other half of the same distinction. timeframesFor() must keep reading
    // timeframes_AVAILABLE — the label fix must not narrow what can be chosen,
    // or a 1min symbol would offer a lone 1min and the original bug returns.
    SymbolPicker.mount({ select: "p20", search: "p20s", tabs: "p20t", summary: "p20sum" });
    await tick();
    const picker = SymbolPicker.mount({
        select: "p21", search: "p21s", tabs: "p21t", summary: "p21sum",
    });
    await tick();
    eqList(
        picker.timeframesFor("RELIANCE"),
        ["1min", "5min", "10min", "15min", "30min", "1hour", "4hour", "1day"]
    );
    eqList(picker.timeframesFor("NIFTY"), ["1day", "1week"]);
});

await test("a daily-only symbol is never offered intraday", async () => {
    // Resampling runs one way. Offering 1min for a symbol stored at 1day would
    // send the user into a run that must fail.
    const picker = SymbolPicker.mount({
        select: "p22", search: "p22s", tabs: "p22t", summary: "p22sum",
    });
    await tick();
    const tfs = picker.timeframesFor("NIFTY");
    assert.ok(!tfs.includes("1min"), `NIFTY must not offer 1min, got ${tfs}`);
});

await test("the All/Equity/Index/F&O tabs are rendered", async () => {
    SymbolPicker.mount({ select: "p4", search: "p4s", tabs: "p4t", summary: "p4sum" });
    await tick();
    const html = el("p4t").innerHTML;
    for (const label of ["All", "Equity", "Index", "F&O"]) {
        assert.ok(html.includes(label), `missing tab ${label}`);
    }
    eqList(SymbolPicker.TABS.map((t) => t.id), ["", "equity", "index", "fno"]);
});

await test("the summary separates known symbols from runnable ones", async () => {
    SymbolPicker.mount({ select: "p5", search: "p5s", tabs: "p5t", summary: "p5sum" });
    await tick();
    assert.match(el("p5sum").textContent, /2 with data/);
    assert.match(el("p5sum").textContent, /206 known/);
});

await test("symbols hidden by the data-only filter are counted and explained", async () => {
    // issues.txt B1: `available=1` hides no-data symbols; without this line a
    // user reads a short list as "these symbols do not exist" / "no indices".
    SymbolPicker.mount({ select: "p10", search: "p10s", tabs: "p10t", summary: "p10sum" });
    await tick();
    const sum = el("p10sum");
    assert.match(sum.textContent, /1 symbol hidden — load data first \(Data tab →\)/);
    assert.match(sum.title, /No data loaded/);
    assert.match(sum.className, /sym-hidden-hint/);
});

await test("no hidden symbols means no nag: the hint class and title stay away", async () => {
    const clean = Object.assign({}, COVERAGE, { hidden_total: 0 });
    const quiet = {
        console,
        document: { getElementById: el, createElement: () => makeOption(), addEventListener() {} },
        fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(clean) }),
        URLSearchParams, Object, Array, JSON, Number, String, Promise, setTimeout, clearTimeout,
    };
    quiet.globalThis = quiet;
    vm.createContext(quiet);
    vm.runInContext(pickerCode, quiet, { filename: "symbol_picker.js" });
    quiet.SymbolPicker.mount({ select: "p11", search: "p11s", tabs: "p11t", summary: "p11sum" });
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setImmediate(r));
    const sum = el("p11sum");
    assert.doesNotMatch(sum.textContent, /hidden/);
    assert.equal(sum.title, "");
    assert.doesNotMatch(sum.className, /sym-hidden-hint/);
});

await test("a truncated page says how many more with-data rows exist", async () => {
    // Same complaint, second cause: PAGE_SIZE=500 caps the list. A page whose
    // total exceeds what was returned must not look like the complete set.
    const crowded = Object.assign({}, COVERAGE, { total: 812, returned: 3, hidden_total: 0 });
    const busy = {
        console,
        document: { getElementById: el, createElement: () => makeOption(), addEventListener() {} },
        fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(crowded) }),
        URLSearchParams, Object, Array, JSON, Number, String, Promise, setTimeout, clearTimeout,
    };
    busy.globalThis = busy;
    vm.createContext(busy);
    vm.runInContext(pickerCode, busy, { filename: "symbol_picker.js" });
    busy.SymbolPicker.mount({ select: "p12", search: "p12s", tabs: "p12t", summary: "p12sum" });
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setImmediate(r));
    assert.match(el("p12sum").textContent, /809 more with data — search to find them/);
    assert.match(el("p12sum").className, /sym-hidden-hint/);
});

await test("timeframesFor answers from the loaded coverage", async () => {
    const picker = SymbolPicker.mount({ select: "p6", search: "p6s", tabs: "p6t", summary: "p6sum" });
    await tick();
    // The fixture's available sets: one 1min symbol, one daily-only, one empty.
    eqList(
        picker.timeframesFor("RELIANCE"),
        ["1min", "5min", "10min", "15min", "30min", "1hour", "4hour", "1day"]
    );
    eqList(picker.timeframesFor("NIFTY"), ["1day", "1week"]);
    eqList(picker.timeframesFor("TCS"), []);
    eqList(picker.timeframesFor("UNKNOWN"), []);
});

await test("setValue injects a symbol the coverage page did not carry", async () => {
    const picker = SymbolPicker.mount({ select: "p7", search: "p7s", tabs: "p7t", summary: "p7sum" });
    await tick();
    picker.setValue("SOMEOTHER");
    assert.equal(el("p7").value, "SOMEOTHER");
    assertNoThrow(() => picker.setValue(""));
});

await test("a failed load says so instead of showing an empty picker", async () => {
    const failing = {
        console,
        document: { getElementById: el, createElement: () => makeOption(), addEventListener() {} },
        fetch: () => Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({ error: "no database" }) }),
        URLSearchParams, Object, Array, JSON, Number, String, Promise, setTimeout, clearTimeout,
    };
    failing.globalThis = failing;
    vm.createContext(failing);
    vm.runInContext(pickerCode, failing, { filename: "symbol_picker.js" });
    const picker = failing.SymbolPicker.mount({ select: "p8", search: "p8s", tabs: "p8t", summary: "p8sum" });
    await new Promise((r) => setImmediate(r));
    await new Promise((r) => setImmediate(r));
    assert.match(el("p8sum").textContent, /Could not load symbols/);
    assert.match(el("p8sum").textContent, /no database/);
    assert.equal(picker.state.rows.length, 0);
});

console.log(`\nsymbol picker + timeframes: ${passed} tests passed`);
