/**
 * Data Manager page controller.
 * Handles: instrument picker (Equity/Index tabs, search, multi-select),
 * fetch start/stop, progress polling, inventory display.
 *
 * The fetch controls are STATE-DRIVEN: every /api/data/status poll (and the
 * init poll on page load) decides whether Start or Stop is showing, so a
 * reload mid-fetch still offers the Stop button.
 */

const $ = (id) => document.getElementById(id);

// Set default to_date to today
$('dm-toDate').value = new Date().toISOString().split('T')[0];

let pollTimer = null;

// -----------------------------------------------------------------------
// Instrument picker (replaces the old free-text symbols box)
// -----------------------------------------------------------------------

const DM_TABS = [
    { id: 'equity,index', label: 'All' },
    { id: 'equity', label: 'Equity' },
    { id: 'index', label: 'Index' },
];
const DM_PAGE_SIZE = 500;

const dmState = {
    tab: 'equity,index',
    rows: [],
    selected: new Set(),   // symbols ticked for the next fetch
    error: null,
};

function dmEsc(v) {
    return String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, (c) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
}

async function dmFetchCoverage(params) {
    const qs = new URLSearchParams(params).toString();
    const r = await fetch(`/api/data/coverage?${qs}`);
    const data = await r.json();
    if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
    return data;
}

function dmDetail(row) {
    if (!row.data_available) return '';
    const bits = [];
    if (row.timeframes_available && row.timeframes_available.length) {
        bits.push(row.timeframes_available.join('/'));
    }
    if (row.bars_count) bits.push(`${row.bars_count.toLocaleString()} bars`);
    return bits.join(' · ');
}

function dmRenderList() {
    const list = $('dm-symbol-list');
    if (dmState.error) {
        list.innerHTML = `<div class="dm-symbol-empty">Could not load instruments — ${dmEsc(dmState.error)}</div>`;
        return;
    }
    if (!dmState.rows.length) {
        list.innerHTML = '<div class="dm-symbol-empty">No instruments match this search.</div>';
        return;
    }
    list.innerHTML = dmState.rows.map((row) => {
        const checked = dmState.selected.has(row.symbol) ? 'checked' : '';
        const detail = dmDetail(row);
        const name = row.name && row.name !== row.symbol ? `<span class="dm-symbol-name">${dmEsc(row.name)}</span>` : '';
        const cov = detail
            ? `<span class="dm-symbol-cov">${dmEsc(detail)}</span>`
            : '<span class="dm-symbol-cov dm-symbol-nodata">no data</span>';
        return `
            <label class="dm-symbol-row" title="${dmEsc(row.symbol)}${row.name ? ' — ' + dmEsc(row.name) : ''}">
                <input type="checkbox" value="${dmEsc(row.symbol)}" ${checked}>
                <span class="dm-symbol-sym">${dmEsc(row.symbol)}</span>
                ${name}
                ${cov}
            </label>`;
    }).join('');
}

function dmRenderCount() {
    const n = dmState.selected.size;
    const parts = [`${dmState.rows.length} shown`];
    if (n) {
        const names = [...dmState.selected].slice(0, 6).join(', ');
        parts.push(`${n} selected: ${names}${n > 6 ? '…' : ''}`);
    } else {
        const unfetched = {
            'equity,index': 'fetches all NIFTY 200 stocks + NSE indices',
            equity: 'fetches all NIFTY 200 stocks',
            index: 'fetches all NSE indices',
        };
        parts.push(`none selected — ${unfetched[dmState.tab] || unfetched['equity,index']}`);
    }
    $('dm-symbol-count').textContent = parts.join(' · ');
}

async function dmLoadSymbols() {
    try {
        const params = { limit: DM_PAGE_SIZE, types: dmState.tab, curated: 1 };
        const q = $('dm-symbol-search').value.trim();
        if (q) params.q = q;
        const data = await dmFetchCoverage(params);
        dmState.rows = data.instruments || [];
        dmState.error = null;
    } catch (err) {
        dmState.error = err.message || String(err);
        dmState.rows = [];
    }
    dmRenderList();
    dmRenderCount();
}

function dmRenderTabs() {
    const tabsEl = $('dm-symbol-tabs');
    tabsEl.innerHTML = DM_TABS.map((t) => (
        `<button type="button" class="sym-tab${t.id === dmState.tab ? ' active' : ''}" data-tab="${t.id}">${t.label}</button>`
    )).join('');
    tabsEl.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-tab]');
        if (!btn) return;
        dmState.tab = btn.dataset.tab;
        tabsEl.querySelectorAll('[data-tab]').forEach((b) => b.classList.toggle('active', b === btn));
        dmLoadSymbols();
    });
}

$('dm-symbol-list').addEventListener('change', (e) => {
    const box = e.target.closest('input[type="checkbox"]');
    if (!box) return;
    if (box.checked) dmState.selected.add(box.value);
    else dmState.selected.delete(box.value);
    dmRenderCount();
});

$('dm-symbol-search').addEventListener('input', (() => {
    let timer = null;
    return () => {
        clearTimeout(timer);
        timer = setTimeout(dmLoadSymbols, 250);
    };
})());

$('dm-symbol-clear').addEventListener('click', () => {
    dmState.selected.clear();
    dmRenderList();
    dmRenderCount();
});

// -----------------------------------------------------------------------
// Fetch start / stop
// -----------------------------------------------------------------------

function dmSetFormEnabled(enabled) {
    [$('dm-timeframe'), $('dm-fromDate'), $('dm-toDate'), $('dm-symbol-search'), $('dm-symbol-clear')]
        .forEach((el) => { el.disabled = !enabled; });
    $('dm-symbol-list').querySelectorAll('input[type="checkbox"]')
        .forEach((el) => { el.disabled = !enabled; });
}

/** The single place that decides Start-vs-Stop visibility. */
function applyJobState(j) {
    const running = j.status === 'running';
    $('dm-fetchBtn').hidden = running;
    $('dm-stopBtn').hidden = !running;
    $('dm-stopBtn').disabled = running && !!j.cancel;  // stop already requested
    // Clear is only meaningful once the panel is showing a FINISHED job —
    // while running, the stop flow owns the panel.
    $('dm-clearProgress').hidden = running;
    if (running || j.status === 'done' || j.status === 'error') {
        $('dm-progress-section').hidden = false;
    }
    dmSetFormEnabled(!running);
}

$('dm-fetchBtn').addEventListener('click', async () => {
    const timeframe = $('dm-timeframe').value;
    const fromDate = $('dm-fromDate').value;
    const toDate = $('dm-toDate').value;

    if (!fromDate || !toDate) {
        showToast('Pick a date range', 'warning');
        return;
    }

    const body = { timeframe, from_date: fromDate, to_date: toDate };
    if (dmState.selected.size) {
        body.symbols = [...dmState.selected].sort();
    }
    // Nothing ticked: the active tab is the fetch scope (All = NIFTY 200
    // equities + indices, Equity = 200 stocks, Index = indices). The server
    // enforces this too — the old "no list = every NSE/BSE stock" fallback
    // is gone (2026-10-02).
    body.scope = dmState.tab;

    try {
        const resp = await fetch('/api/data/fetch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (!resp.ok) {
            showToast(data.error || 'Failed to start fetch', 'error');
            return;
        }
        showToast('Fetch started', 'success');
        applyJobState({ status: 'running' });
        startPolling();
    } catch (err) {
        showToast(err.message, 'error');
    }
});

$('dm-stopBtn').addEventListener('click', async () => {
    try {
        const resp = await fetch('/api/data/stop', { method: 'POST' });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showToast(data.error || 'Could not stop the fetch', 'error');
            return;
        }
        showToast('Stopping after current chunk/symbol…', 'warning');
        pollStatus();  // reflect the cancel flag on the button right away
    } catch (err) {
        showToast(err.message, 'error');
    }
});

$('dm-clearProgress').addEventListener('click', async () => {
    try {
        const resp = await fetch('/api/data/clear', { method: 'POST' });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            showToast(data.error || 'Could not clear the progress panel', 'error');
            return;
        }
        // The server is back to idle, so a page reload won't resurrect the
        // finished panel either.
        $('dm-progress-section').hidden = true;
        applyJobState({ status: 'idle' });
        showToast('Fetch progress cleared', 'success');
    } catch (err) {
        showToast(err.message, 'error');
    }
});

// -----------------------------------------------------------------------
// Progress polling
// -----------------------------------------------------------------------

function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(pollStatus, 2000);
}

function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function pollStatus() {
    try {
        const resp = await fetch('/api/data/status');
        const j = await resp.json();
        updateProgress(j);
        applyJobState(j);

        if (j.status === 'running') {
            // Covers a page load while a job is already running: keep polling.
            if (!pollTimer) startPolling();
            return;
        }

        if (j.status === 'done' || j.status === 'error' || j.status === 'idle') {
            const wasRunning = pollTimer !== null;
            stopPolling();
            if (wasRunning && j.status === 'done') {
                // Coverage-aware skip: a resume over an already-fetched range
                // legitimately stores ~0 bars — say so, or it reads as a hang.
                const skipNote = j.skipped > 0
                    ? ` (${j.skipped} already covered, skipped)`
                    : '';
                if (j.chunk_errors > 0) {
                    // mStock ate chunks even after retries — a "done" with
                    // holes must never render as a clean success.
                    showToast(`Finished, but ${j.chunk_errors} chunk request(s) failed even after retries — data may be partial; re-run the same fetch, it only pulls the missing days`, 'error');
                } else {
                    showToast(`Fetch complete: ${j.fetched} symbols${skipNote}, ${j.bars_total.toLocaleString()} bars`, 'success');
                }
                loadInventory();  // refresh inventory
            }
            if (wasRunning && j.status === 'error') {
                showToast(j.error || 'Fetch failed', 'error');
            }
        }
    } catch (err) {
        // server might be restarting
    }
}

function dmElapsed(j) {
    // Prefer a live countdown (the server only refreshes `elapsed` per
    // symbol, which freezes for minutes on wide 1-min ranges).
    if (j.status === 'running' && j.started_at) {
        const s = Math.max(0, Math.floor(Date.now() / 1000 - j.started_at));
        return `${Math.floor(s / 60)}m ${s % 60}s`;
    }
    return j.elapsed || '';
}

function updateProgress(j) {
    const total = j.total || 1;
    // Continuous progress: completed symbols + the fraction of chunks done
    // inside the current symbol, so the bar moves every request.
    const inSymbol = j.chunk_total > 0 ? Math.min((j.chunk_done || 0) / j.chunk_total, 1) : 0;
    const pct = Math.min(100, Math.round(((j.fetched || 0) + inSymbol) / total * 100));

    $('dm-progress-bar').style.width = pct + '%';
    $('dm-status-text').textContent =
        j.status === 'running' ? (j.cancel ? 'Stopping…' : `Fetching ${j.timeframe} data...`) :
        j.status === 'done' ? (j.error === 'Cancelled by user' ? 'Cancelled' : 'Done') :
        j.status === 'error' ? (j.error ? 'Error: ' + j.error.split(';')[0] : 'Error') : 'Idle';
    $('dm-fetched-count').textContent = j.chunk_total > 0 && j.status === 'running'
        ? `${j.fetched || 0} / ${j.total || 0} symbols · chunk ${j.chunk_done || 0}/${j.chunk_total}`
        : `${j.fetched || 0} / ${j.total || 0} symbols`;
    $('dm-bars-count').textContent = `${(j.bars_total || 0).toLocaleString()} bars`;
    $('dm-elapsed').textContent = dmElapsed(j);

    if (j.symbol) {
        $('dm-current-symbol').hidden = false;
        $('dm-current-name').textContent = j.symbol;
    } else {
        $('dm-current-symbol').hidden = true;
    }

    // Show errors
    if (j.failed_list && j.failed_list.length > 0) {
        $('dm-errors').hidden = false;
        $('dm-error-list').innerHTML = j.failed_list.map(
            ([sym, err]) => `<div class="error-item"><strong>${dmEsc(sym)}</strong>: ${dmEsc(err)}</div>`
        ).join('');
    }
}

// -----------------------------------------------------------------------
// Inventory
// -----------------------------------------------------------------------

async function loadInventory() {
    const tbody = $('dm-inv-table') ? $('dm-inv-table').querySelector('tbody') : null;
    if (tbody && !tbody.children.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center; padding:16px;">Loading price data inventory…</td></tr>';
    }
    try {
        const resp = await fetch('/api/data/inventory');
        const j = await resp.json();
        if (!resp.ok) return;

        const syms = j.symbols || {};
        const names = Object.keys(syms).sort();
        const timeframes = new Set();

        if (tbody) tbody.innerHTML = '';

        if (names.length === 0) {
            $('dm-inv-empty').hidden = false;
            $('dm-inv-table').hidden = true;
        } else {
            $('dm-inv-empty').hidden = true;
            $('dm-inv-table').hidden = false;

            for (const sym of names) {
                const entries = syms[sym];
                for (const e of entries) {
                    timeframes.add(e.timeframe);
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td><strong>${dmEsc(sym)}</strong></td>
                        <td>${dmEsc(e.timeframe)}</td>
                        <td>${e.bars.toLocaleString()}</td>
                        <td>${e.earliest || '-'}</td>
                        <td>${e.latest || '-'}</td>
                    `;
                    tbody.appendChild(tr);
                }
            }
        }

        $('dm-inv-symbols').textContent = `${names.length} symbols`;
        $('dm-inv-bars').textContent = `${(j.total_bars || 0).toLocaleString()} bars`;
        $('dm-inv-timeframes').textContent = [...timeframes].join(', ') || '-';
    } catch (err) {
        console.error('Inventory load failed:', err);
        if (tbody) tbody.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center; padding:16px;">Failed to load data inventory.</td></tr>';
        $('dm-inv-symbols').textContent = 'Error loading inventory';
    }
}

$('dm-refreshBtn').addEventListener('click', loadInventory);

// -----------------------------------------------------------------------
// Init
// -----------------------------------------------------------------------
dmRenderTabs();
dmLoadSymbols();
loadInventory();
pollStatus();  // restore running state (stop button + polling) if a job is live
