/**
 * Data-tab row labels — what the symbol list actually prints.
 *
 * A row reads "RELIANCE · 1min · 4,817 bars". The timeframe shown is what was
 * DOWNLOADED, not the derived set the Backtest picker offers. Both come from
 * /api/data/coverage and both are on the same row object, so the two are easy
 * to swap — and swapping them prints the same nine-item list under every
 * instrument holding 1min data, which is the bug this file exists to catch.
 *
 * dmDetail() mirrors SymbolPicker.detailOf(); tests/js/test_symbol_picker.mjs
 * covers the picker's copy of the rule.
 *
 * data_manager.js is a plain script with top-level init side effects (it binds
 * the refresh button, loads symbols, starts polling), so it runs against a
 * permissive DOM stub. Its top-level function declarations land on the sandbox
 * global, which is how dmDetail is reachable without exporting it.
 *
 * Usage: node tests/js/test_data_manager_labels.mjs
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../", import.meta.url));
const src = readFileSync(path.join(root, "src/backtest/web/static/js/data_manager.js"), "utf8");

/** Any property read returns something callable, truthy, or empty — never throw. */
function stubNode() {
    const target = {};
    return new Proxy(target, {
        get(t, k) {
            if (k in t) return t[k];
            if (k === "innerHTML" || k === "textContent" || k === "value") return "";
            if (k === "options" || k === "children") return [];
            if (k === "dataset" || k === "style") return {};
            if (k === "classList") {
                return { add() {}, remove() {}, toggle() {}, contains: () => false };
            }
            if (k === "hidden" || k === "disabled" || k === "checked") return false;
            if (k === "length") return 0;
            if (k === "addEventListener" || k === "removeEventListener") return () => {};
            if (typeof k === "string" && /^(querySelector|querySelectorAll|closest|clonedNode)$/.test(k)) {
                return () => (k.endsWith("All") ? [] : null);
            }
            return () => undefined;
        },
        set(t, k, v) {
            t[k] = v;
            return true;
        },
    });
}

const nodes = {};
const sandbox = {
    console,
    document: {
        getElementById: (id) => (nodes[id] = nodes[id] || stubNode()),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        createElement: () => stubNode(),
        body: stubNode(),
    },
    fetch: () => Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve({ instruments: [], total: 0 }),
    }),
    setTimeout: () => 0,
    clearTimeout() {},
    setInterval: () => 0,
    clearInterval() {},
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    URLSearchParams, Object, Array, JSON, Number, String, Promise, Boolean,
    Math, Date, RegExp, Error, isNaN, parseInt, parseFloat,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "data_manager.js" });

let passed = 0;
function test(name, fn) {
    fn();
    passed += 1;
    console.log(`  ✓ ${name}`);
}

const row = (over) => Object.assign({
    symbol: "RELIANCE", data_available: true, bars_count: 4817,
    timeframes_stored: ["1min"],
    timeframes_available: [
        "1min", "5min", "10min", "15min", "30min", "1hour", "4hour", "1day", "1week",
    ],
}, over);

test("the label prints the STORED timeframe and the bar count", () => {
    assert.equal(sandbox.dmDetail(row()), "1min · 4,817 bars");
});

test("the derived set never appears in the label", () => {
    // The regression: reading timeframes_available printed the same nine-item
    // list under every symbol, telling the user nothing about that symbol.
    const label = sandbox.dmDetail(row());
    for (const derived of ["5min", "15min", "1hour", "4hour", "1day", "1week"]) {
        assert.ok(!label.includes(derived), `${derived} leaked into "${label}"`);
    }
});

test("a daily-only symbol says 1day, not the two it can serve", () => {
    const label = sandbox.dmDetail(row({
        symbol: "NIFTY", bars_count: 1247,
        timeframes_stored: ["1day"], timeframes_available: ["1day", "1week"],
    }));
    assert.equal(label, "1day · 1,247 bars");
});

test("several stored timeframes are all listed, comma-free like the picker", () => {
    const label = sandbox.dmDetail(row({
        timeframes_stored: ["1min", "1day"],
        timeframes_available: ["1min", "1day", "1week"],
    }));
    assert.equal(label, "1min/1day · 4,817 bars");
});

test("a row with no data renders nothing at all", () => {
    assert.equal(sandbox.dmDetail(row({ data_available: false, bars_count: 0 })), "");
});

test("a missing timeframes_stored degrades to the bar count, never to the noise", () => {
    // An older server payload would omit the field. Showing only the bar count
    // is honest; falling back to timeframes_available would restore the bug.
    const label = sandbox.dmDetail(row({ timeframes_stored: undefined }));
    assert.equal(label, "4,817 bars");
});

console.log(`\ndata manager labels: ${passed} tests passed`);
