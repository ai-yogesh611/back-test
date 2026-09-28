/**
 * Strategy Performance Analytics — Frontend Controller
 * Powers /analytics overview, strategy detail deep dive, equity/drawdown charts,
 * rolling Sharpe metrics, and edge degradation alerts.
 */

(function () {
    'use strict';

    // XSS guard (gap fix #1): EVERY user-influenced string interpolated into
    // an innerHTML template goes through esc() — runner names are set at
    // spawn time by the user and used to be injected raw.
    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // Error banner (gap fix #9): fetch failures used to die in console.error
    // with a silently blank pane.
    function showError(message) {
        let banner = document.getElementById('analyticsErrorBanner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'analyticsErrorBanner';
            banner.setAttribute('role', 'alert');
            banner.style.cssText = 'margin: 10px 0; padding: 10px 14px; border-radius: 6px; ' +
                'background: rgba(239,68,68,0.12); color: var(--danger); border: 1px solid rgba(239,68,68,0.4); ' +
                'display: flex; justify-content: space-between; align-items: center; gap: 12px;';
            const host = document.querySelector('.page-content') || document.body;
            host.insertBefore(banner, host.firstChild);
        }
        banner.innerHTML = '<span>⚠️ ' + esc(message) + '</span>' +
            '<button type="button" class="btn btn-ghost btn-small" id="analyticsErrorDismiss">Dismiss</button>';
        banner.style.display = 'flex';
        const dismiss = document.getElementById('analyticsErrorDismiss');
        if (dismiss) dismiss.addEventListener('click', () => { banner.style.display = 'none'; });
    }

    function clearError() {
        const banner = document.getElementById('analyticsErrorBanner');
        if (banner) banner.style.display = 'none';
    }

    // State
    let currentPeriod = '30d';
    let currentMode = 'all';
    let currentStrategyId = null;

    // Chart instances
    let portfolioEquityChart = null;
    let detailEquityChart = null;
    let detailRollingChart = null;
    let detailDistributionChart = null;

    // DOM Elements
    const tabOverviewBtn = document.getElementById('tabOverviewBtn');
    const tabDetailBtn = document.getElementById('tabDetailBtn');
    const analyticsOverviewPane = document.getElementById('analyticsOverviewPane');
    const analyticsDetailPane = document.getElementById('analyticsDetailPane');
    const periodSelect = document.getElementById('periodSelect');
    const modeFilterSelect = document.getElementById('modeFilterSelect');
    const refreshBtn = document.getElementById('refreshAnalyticsBtn');
    const backToOverviewBtn = document.getElementById('backToOverviewBtn');

    // URL parameters check
    const urlParams = new URLSearchParams(window.location.search);
    const initialStrategyId = urlParams.get('strategy');

    function init() {
        bindEvents();

        if (initialStrategyId) {
            loadStrategyDetail(initialStrategyId);
        } else {
            loadOverview();
        }

        // Auto-refresh (gap fix #9): numbers went silently stale on a
        // long-open tab. Poll every 30s, only while the tab is visible.
        setInterval(() => {
            if (document.visibilityState !== 'visible') return;
            if (currentStrategyId) {
                loadStrategyDetail(currentStrategyId);
            } else {
                loadOverview();
            }
        }, 30000);
    }

    function bindEvents() {
        if (tabOverviewBtn) {
            tabOverviewBtn.addEventListener('click', () => {
                switchTab('overview');
            });
        }

        if (tabDetailBtn) {
            tabDetailBtn.addEventListener('click', () => {
                if (currentStrategyId) {
                    switchTab('detail');
                }
            });
        }

        if (backToOverviewBtn) {
            backToOverviewBtn.addEventListener('click', () => {
                switchTab('overview');
                // Update URL without reload
                window.history.pushState({}, '', '/analytics');
            });
        }

        if (periodSelect) {
            periodSelect.addEventListener('change', (e) => {
                currentPeriod = e.target.value;
                if (analyticsDetailPane.style.display !== 'none' && currentStrategyId) {
                    loadStrategyDetail(currentStrategyId);
                } else {
                    loadOverview();
                }
            });
        }

        if (modeFilterSelect) {
            modeFilterSelect.addEventListener('change', (e) => {
                currentMode = e.target.value;
                loadOverview();
            });
        }

        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => {
                if (analyticsDetailPane.style.display !== 'none' && currentStrategyId) {
                    loadStrategyDetail(currentStrategyId);
                } else {
                    loadOverview();
                }
            });
        }
    }

    function switchTab(tab) {
        if (tab === 'overview') {
            tabOverviewBtn.classList.add('active');
            tabDetailBtn.classList.remove('active');
            analyticsOverviewPane.style.display = 'block';
            analyticsDetailPane.style.display = 'none';
        } else {
            tabOverviewBtn.classList.remove('active');
            tabDetailBtn.classList.add('active');
            tabDetailBtn.style.display = 'inline-block';
            analyticsOverviewPane.style.display = 'none';
            analyticsDetailPane.style.display = 'block';
        }
    }

    // -------------------------------------------------------------------------
    // OVERVIEW
    // -------------------------------------------------------------------------

    async function loadOverview() {
        try {
            const modeParam = currentMode !== 'all' ? `&mode=${encodeURIComponent(currentMode)}` : '';
            const res = await fetch(`/api/analytics/overview?period=${currentPeriod}${modeParam}`);
            const data = await res.json();

            if (!data.success) {
                console.error('Failed to load analytics overview:', data.error);
                showError(`Analytics overview failed: ${data.error || 'unknown error'}`);
                return;
            }

            clearError();
            renderOverviewMetrics(data.portfolio_metrics, data.active_runners, data.total_runners);
            renderPortfolioEquityChart(data.portfolio_equity_curve);
            renderStrategyCards(data.strategy_cards);
            renderOverviewAlerts(data.alerts);
        } catch (err) {
            console.error('Error fetching analytics overview:', err);
            showError('Could not reach the analytics API — is the server up?');
        }
    }

    function renderOverviewMetrics(pm, activeCount, totalCount) {
        const activePill = document.getElementById('activeRunnersPill');
        if (activePill) {
            activePill.textContent = `${activeCount} / ${totalCount} Active Strategies`;
        }

        const totalRetEl = document.getElementById('overviewTotalReturn');
        const totalPnlEl = document.getElementById('overviewTotalPnl');
        if (totalRetEl && pm) {
            const retSign = pm.total_return_pct >= 0 ? '+' : '';
            totalRetEl.textContent = `${retSign}${pm.total_return_pct}%`;
            totalRetEl.style.color = pm.total_return_pct >= 0 ? 'var(--success)' : 'var(--danger)';
            totalPnlEl.textContent = `₹${pm.total_pnl.toLocaleString()}`;
        }

        const sharpeEl = document.getElementById('overviewSharpe');
        const sharpeRatingEl = document.getElementById('overviewSharpeRating');
        if (sharpeEl && pm) {
            sharpeEl.textContent = pm.sharpe_ratio.toFixed(2);
            let rating = '🔴 Poor';
            if (pm.sharpe_ratio >= 1.5) rating = '🟢 Excellent';
            else if (pm.sharpe_ratio >= 1.0) rating = '🟡 Good';
            sharpeRatingEl.textContent = rating;
        }

        const winRateEl = document.getElementById('overviewWinRate');
        const tradeCountEl = document.getElementById('overviewTradeCount');
        if (winRateEl && pm) {
            winRateEl.textContent = `${pm.win_rate}%`;
            tradeCountEl.textContent = `${pm.total_trades} trades (${pm.winning_trades}W / ${pm.losing_trades}L)`;
        }

        const maxDdEl = document.getElementById('overviewMaxDd');
        const maxDdAmtEl = document.getElementById('overviewMaxDdAmt');
        if (maxDdEl && pm) {
            maxDdEl.textContent = `-${pm.max_drawdown_pct}%`;
            maxDdEl.style.color = pm.max_drawdown_pct > 15 ? 'var(--danger)' : 'var(--text)';
            maxDdAmtEl.textContent = `₹${pm.max_drawdown_amount.toLocaleString()}`;
        }
    }

    function renderPortfolioEquityChart(curve) {
        const canvas = document.getElementById('portfolioEquityChart');
        if (!canvas) return;

        if (portfolioEquityChart) {
            portfolioEquityChart.destroy();
        }

        const labels = (curve || []).map(p => p.date);
        const dataEquity = (curve || []).map(p => p.equity);
        const dataDd = (curve || []).map(p => -Math.abs(p.drawdown_pct));

        const ctx = canvas.getContext('2d');
        portfolioEquityChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Portfolio Equity (₹)',
                        data: dataEquity,
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.1)',
                        fill: true,
                        tension: 0.25,
                        pointRadius: labels.length > 30 ? 0 : 3,
                        yAxisID: 'y',
                    },
                    {
                        label: 'Drawdown (%)',
                        data: dataDd,
                        borderColor: 'rgba(239, 68, 68, 0.7)',
                        backgroundColor: 'rgba(239, 68, 68, 0.05)',
                        fill: true,
                        tension: 0.25,
                        pointRadius: 0,
                        borderDash: [4, 4],
                        yAxisID: 'y1',
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                    x: { grid: { display: false } },
                    y: {
                        position: 'left',
                        title: { display: true, text: 'Equity (₹)' },
                        ticks: {
                            callback: val => '₹' + Number(val).toLocaleString()
                        }
                    },
                    y1: {
                        position: 'right',
                        title: { display: true, text: 'Drawdown (%)' },
                        grid: { drawOnChartArea: false },
                        max: 0,
                        ticks: {
                            callback: val => val + '%'
                        }
                    }
                }
            }
        });
    }

    function renderStrategyCards(cards) {
        const container = document.getElementById('strategyCardsGrid');
        const emptyState = document.getElementById('noStrategiesEmpty');
        if (!container) return;

        container.innerHTML = '';
        if (!cards || cards.length === 0) {
            emptyState.style.display = 'block';
            return;
        }
        emptyState.style.display = 'none';

        cards.forEach((card, idx) => {
            const m = card.metrics;
            const health = card.health || { badge: '🟢 Healthy', status: 'green' };
            const cardEl = document.createElement('div');
            cardEl.className = 'card strategy-analytics-card';
            cardEl.style.cssText = 'padding: 16px; display: flex; flex-direction: column; justify-content: space-between; transition: transform 0.15s ease, box-shadow 0.15s ease; cursor: pointer;';

            const retColor = m.total_return_pct >= 0 ? 'var(--success)' : 'var(--danger)';
            const retSign = m.total_return_pct >= 0 ? '+' : '';

            cardEl.innerHTML = `
                <div>
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;">
                        <div>
                            <strong style="font-size: 1.05rem; display: block;">${esc(card.name)}</strong>
                            <span class="small muted">${esc(card.strategy_name)} · ${esc(card.mode.toUpperCase())} · ${esc(card.symbols.join(', '))}</span>
                        </div>
                        <span class="badge" style="font-size: 0.75rem; background: var(--surface-2); padding: 2px 8px; border-radius: 10px;">${esc(health.badge)}</span>
                    </div>

                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 12px 0; background: var(--surface-2); padding: 10px; border-radius: 6px;">
                        <div>
                            <div class="small muted" style="font-size: 0.7rem;">RETURN</div>
                            <strong style="color: ${retColor};">${retSign}${m.total_return_pct}%</strong>
                        </div>
                        <div>
                            <div class="small muted" style="font-size: 0.7rem;">SHARPE</div>
                            <strong>${m.sharpe_ratio.toFixed(2)}</strong>
                        </div>
                        <div>
                            <div class="small muted" style="font-size: 0.7rem;">MAX DD</div>
                            <strong style="color: ${m.max_drawdown_pct > 12 ? 'var(--danger)' : 'inherit'};">-${m.max_drawdown_pct}%</strong>
                        </div>
                    </div>

                    <div style="height: 50px; position: relative; margin-bottom: 8px;">
                        <canvas id="miniChart_${idx}"></canvas>
                    </div>
                </div>

                <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--border); padding-top: 10px; margin-top: 4px;">
                    <span class="small muted">${m.total_trades} trades (WR: ${m.win_rate}%)</span>
                    <button class="btn btn-ghost btn-small" type="button" style="color: var(--accent);">View Analytics →</button>
                </div>
            `;

            cardEl.addEventListener('click', () => {
                loadStrategyDetail(card.instance_id);
                window.history.pushState({}, '', `/analytics?strategy=${encodeURIComponent(card.instance_id)}`);
            });

            container.appendChild(cardEl);

            // Render mini sparkline
            setTimeout(() => {
                const canvas = document.getElementById(`miniChart_${idx}`);
                if (canvas && card.mini_curve && card.mini_curve.length > 1) {
                    // Time labels when the backend provides them (fix #9) —
                    // index labels were misleading once curves decimate.
                    const tsLabels = (card.mini_curve_ts && card.mini_curve_ts.length === card.mini_curve.length)
                        ? card.mini_curve_ts
                        : card.mini_curve.map((_, i) => i);
                    new Chart(canvas.getContext('2d'), {
                        type: 'line',
                        data: {
                            labels: tsLabels,
                            datasets: [{
                                data: card.mini_curve,
                                borderColor: m.total_return_pct >= 0 ? '#10b981' : '#ef4444',
                                borderWidth: 2,
                                fill: false,
                                pointRadius: 0,
                                tension: 0.2
                            }]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: { legend: { display: false }, tooltip: { enabled: false } },
                            scales: { x: { display: false }, y: { display: false } }
                        }
                    });
                }
            }, 0);
        });
    }

    function renderOverviewAlerts(alerts) {
        const container = document.getElementById('overviewAlertsList');
        if (!container) return;

        container.innerHTML = '';
        if (!alerts || alerts.length === 0) {
            container.innerHTML = '<div class="muted small">No active risk or performance degradation alerts detected.</div>';
            return;
        }

        alerts.forEach(a => {
            const item = document.createElement('div');
            const icon = a.severity === 'critical' ? '🔴' : (a.severity === 'warning' ? '⚠️' : 'ℹ️');
            item.className = 'small';
            item.style.cssText = 'display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; background: var(--surface-2); border-radius: 6px;';
            item.innerHTML = `
                <div>
                    <span>${icon}</span> <strong>${esc(a.strategy)}</strong>: ${esc(a.message)}
                </div>
                <button class="btn btn-ghost btn-small" style="font-size: 0.75rem;" type="button">Drill Down</button>
            `;
            item.querySelector('button').addEventListener('click', (e) => {
                e.stopPropagation();
                loadStrategyDetail(a.instance_id);
            });
            container.appendChild(item);
        });
    }

    // -------------------------------------------------------------------------
    // STRATEGY DETAIL
    // -------------------------------------------------------------------------

    async function loadStrategyDetail(instanceId) {
        try {
            currentStrategyId = instanceId;
            switchTab('detail');

            const res = await fetch(`/api/analytics/strategy/${encodeURIComponent(instanceId)}?period=${currentPeriod}`);
            const data = await res.json();

            if (!data.success) {
                console.error('Failed to load strategy detail:', data.error);
                showError(`Strategy detail failed: ${data.error || 'unknown error'}`);
                return;
            }

            clearError();
            renderDetailHeader(data);
            renderDetailMetrics(data.metrics, data.health);
            renderDegradationBanner(data.edge_degradation);
            renderDetailEquityChart(data.equity_curve);
            renderDetailRollingChart(data.rolling_metrics);
            renderMonthlyBreakdown(data.monthly_breakdown);
            renderTradeDistribution(data.trade_distribution);
            renderTradesTable(data.recent_trades);
        } catch (err) {
            console.error('Error fetching strategy detail:', err);
            showError('Could not reach the analytics API — is the server up?');
        }
    }

    function renderDetailHeader(data) {
        const titleEl = document.getElementById('detailStrategyTitle');
        const subEl = document.getElementById('detailStrategySubtitle');
        const backtestLink = document.getElementById('detailBacktestLink');
        const portfolioLink = document.getElementById('detailPortfolioLink');

        if (titleEl) {
            titleEl.textContent = data.name;
        }
        if (subEl) {
            subEl.textContent = `Mode: ${data.mode.toUpperCase()} · Timeframe: ${data.timeframe || '1H'} · Symbols: ${data.symbols.join(', ')} · Capital: ₹${data.allocated_capital.toLocaleString()}`;
        }
        if (backtestLink) {
            backtestLink.href = `/compare?strategy=${encodeURIComponent(data.strategy_name)}`;
        }
        if (portfolioLink) {
            portfolioLink.href = `/portfolio/${data.mode}`;
        }
    }

    function renderDegradationBanner(deg) {
        const banner = document.getElementById('degradationAlertBanner');
        if (!banner) return;

        if (deg) {
            banner.style.display = 'block';
            document.getElementById('degradationTitle').textContent = `Edge Degradation Alert (${deg.severity.toUpperCase()})`;
            document.getElementById('degradationDesc').textContent = deg.message;
        } else {
            banner.style.display = 'none';
        }
    }

    function renderDetailMetrics(m, health) {
        const retEl = document.getElementById('detailReturn');
        const pnlEl = document.getElementById('detailPnl');
        if (retEl && m) {
            const retSign = m.total_return_pct >= 0 ? '+' : '';
            retEl.textContent = `${retSign}${m.total_return_pct}%`;
            retEl.style.color = m.total_return_pct >= 0 ? 'var(--success)' : 'var(--danger)';
            pnlEl.textContent = `₹${m.total_pnl.toLocaleString()}`;
        }

        const sharpeEl = document.getElementById('detailSharpe');
        const sharpeBadgeEl = document.getElementById('detailSharpeBadge');
        if (sharpeEl && m) {
            sharpeEl.textContent = m.sharpe_ratio.toFixed(2);
            sharpeBadgeEl.textContent = health ? health.badge : '🟢 Normal';
        }

        const sortinoEl = document.getElementById('detailSortino');
        if (sortinoEl && m) {
            // null = no downside deviation to divide by (fix #5) — say so,
            // don't silently print the Sharpe under a Sortino label.
            sortinoEl.textContent = m.sortino_ratio == null ? 'n/a (no losses)' : m.sortino_ratio.toFixed(2);
        }

        const calmarEl = document.getElementById('detailCalmar');
        if (calmarEl && m) {
            calmarEl.textContent = m.calmar_ratio.toFixed(2);
        }

        const winRateEl = document.getElementById('detailWinRate');
        const winsLossesEl = document.getElementById('detailWinsLosses');
        if (winRateEl && m) {
            winRateEl.textContent = `${m.win_rate}%`;
            winsLossesEl.textContent = `${m.winning_trades}W / ${m.losing_trades}L (${m.total_trades} total)`;
        }

        const pfEl = document.getElementById('detailProfitFactor');
        const expEl = document.getElementById('detailExpectancy');
        if (pfEl && m) {
            // null = no losing trades (fix #8: explicit sentinel, not 99.99).
            pfEl.textContent = m.profit_factor == null ? '∞' : m.profit_factor.toFixed(2);
            expEl.textContent = `Exp: ₹${m.expectancy}/trade`;
        }

        const maxDdEl = document.getElementById('detailMaxDd');
        const maxDdAmtEl = document.getElementById('detailMaxDdAmt');
        if (maxDdEl && m) {
            maxDdEl.textContent = `-${m.max_drawdown_pct}%`;
            maxDdAmtEl.textContent = `₹${m.max_drawdown_amount.toLocaleString()}`;
        }

        const streakEl = document.getElementById('detailStreak');
        const maxStreakEl = document.getElementById('detailMaxStreak');
        if (streakEl && m) {
            const strk = m.streaks || {};
            streakEl.textContent = `${strk.current_streak} ${strk.current_streak_is_win ? 'Win(s)' : 'Loss(es)'}`;
            maxStreakEl.textContent = `Max: ${strk.max_win_streak}W / ${strk.max_loss_streak}L`;
        }
    }

    function renderDetailEquityChart(curve) {
        const canvas = document.getElementById('detailEquityChart');
        if (!canvas) return;

        if (detailEquityChart) {
            detailEquityChart.destroy();
        }

        const labels = (curve || []).map(p => p.date || p.timestamp || '');
        const dataEquity = (curve || []).map(p => p.equity);
        const dataDd = (curve || []).map(p => -Math.abs(p.drawdown_pct));

        const ctx = canvas.getContext('2d');
        detailEquityChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Equity (₹)',
                        data: dataEquity,
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.12)',
                        fill: true,
                        tension: 0.25,
                        pointRadius: labels.length > 40 ? 0 : 2,
                        yAxisID: 'y',
                    },
                    {
                        label: 'Drawdown (%)',
                        data: dataDd,
                        borderColor: 'rgba(239, 68, 68, 0.8)',
                        backgroundColor: 'rgba(239, 68, 68, 0.08)',
                        fill: true,
                        tension: 0.25,
                        pointRadius: 0,
                        borderDash: [4, 4],
                        yAxisID: 'y1',
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                    x: { grid: { display: false } },
                    y: {
                        position: 'left',
                        title: { display: true, text: 'Equity (₹)' },
                        ticks: { callback: val => '₹' + Number(val).toLocaleString() }
                    },
                    y1: {
                        position: 'right',
                        title: { display: true, text: 'Drawdown (%)' },
                        grid: { drawOnChartArea: false },
                        max: 0,
                        ticks: { callback: val => val + '%' }
                    }
                }
            }
        });
    }

    function renderDetailRollingChart(rolling) {
        const canvas = document.getElementById('detailRollingChart');
        if (!canvas) return;

        if (detailRollingChart) {
            detailRollingChart.destroy();
        }

        const labels = (rolling || []).map(r => r.date || `T#${r.trade_index}`);
        const dataSharpe = (rolling || []).map(r => r.rolling_sharpe);
        const dataWr = (rolling || []).map(r => r.rolling_win_rate);

        const ctx = canvas.getContext('2d');
        detailRollingChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Rolling Sharpe (10 trades)',
                        data: dataSharpe,
                        borderColor: '#3b82f6',
                        backgroundColor: 'rgba(59, 130, 246, 0.1)',
                        fill: false,
                        tension: 0.2,
                        yAxisID: 'y',
                    },
                    {
                        label: 'Rolling Win Rate (%)',
                        data: dataWr,
                        borderColor: '#f59e0b',
                        backgroundColor: 'rgba(245, 158, 11, 0.1)',
                        fill: false,
                        borderDash: [3, 3],
                        tension: 0.2,
                        yAxisID: 'y1',
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                    x: { grid: { display: false } },
                    y: {
                        position: 'left',
                        title: { display: true, text: 'Rolling Sharpe' }
                    },
                    y1: {
                        position: 'right',
                        title: { display: true, text: 'Win Rate (%)' },
                        grid: { drawOnChartArea: false },
                        min: 0,
                        max: 100,
                        ticks: { callback: v => v + '%' }
                    }
                }
            }
        });
    }

    function renderMonthlyBreakdown(monthly) {
        const tbody = document.getElementById('detailMonthlyTbody');
        if (!tbody) return;

        tbody.innerHTML = '';
        if (!monthly || monthly.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center muted">No monthly trade data yet</td></tr>';
            return;
        }

        monthly.forEach(m => {
            const tr = document.createElement('tr');
            const pnlColor = m.pnl >= 0 ? 'var(--success)' : 'var(--danger)';
            const pnlSign = m.pnl >= 0 ? '+' : '';
            tr.innerHTML = `
                <td><strong>${esc(m.month)}</strong></td>
                <td class="text-right">${m.trades}</td>
                <td class="text-right">${m.win_rate}%</td>
                <td class="text-right" style="color: ${pnlColor}; font-weight: 600;">${pnlSign}₹${m.pnl.toLocaleString()}</td>
                <td class="text-right" style="color: ${pnlColor};">${pnlSign}${m.return_pct}%</td>
                <td class="text-right">-${m.max_drawdown_pct}%</td>
                <td class="text-right">${m.sharpe_ratio == null ? '—' : m.sharpe_ratio.toFixed(2)}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    function renderTradeDistribution(dist) {
        const canvas = document.getElementById('detailDistributionChart');
        const insightsList = document.getElementById('distributionInsightsList');
        if (!canvas) return;

        if (detailDistributionChart) {
            detailDistributionChart.destroy();
        }

        const hist = (dist && dist.histogram) ? dist.histogram : [];
        const labels = hist.map(h => h.range);
        const dataCounts = hist.map(h => h.count);
        const bgColors = hist.map(h => h.type === 'win' ? 'rgba(16, 185, 129, 0.65)' : 'rgba(239, 68, 68, 0.65)');

        const ctx = canvas.getContext('2d');
        detailDistributionChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Trade Count',
                    data: dataCounts,
                    backgroundColor: bgColors,
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { font: { size: 10 } } },
                    y: { beginAtZero: true, ticks: { precision: 0 } }
                }
            }
        });

        if (insightsList) {
            insightsList.innerHTML = '';
            const insights = (dist && dist.insights) ? dist.insights : [];
            if (insights.length === 0) {
                insightsList.innerHTML = '<div>Accumulating trade sample size...</div>';
            } else {
                insights.forEach(ins => {
                    const item = document.createElement('div');
                    item.textContent = ins;
                    insightsList.appendChild(item);
                });
            }
        }
    }

    function renderTradesTable(trades) {
        const tbody = document.getElementById('detailTradesTbody');
        if (!tbody) return;

        tbody.innerHTML = '';
        if (!trades || trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center muted">No closed trades recorded yet</td></tr>';
            return;
        }

        // Show newest trades first
        const reversed = [...trades].reverse();
        reversed.forEach(t => {
            const tr = document.createElement('tr');
            const pnl = Number(t.pnl || 0);
            const pnlColor = pnl >= 0 ? 'var(--success)' : 'var(--danger)';
            const pnlSign = pnl >= 0 ? '+' : '';
            const badge = pnl >= 0 ? '<span class="badge" style="background: rgba(16,185,129,0.15); color: var(--success); padding: 2px 6px; border-radius: 4px;">WIN</span>'
                                   : '<span class="badge" style="background: rgba(239,68,68,0.15); color: var(--danger); padding: 2px 6px; border-radius: 4px;">LOSS</span>';

            // Instrument class tag (fix #10): option qty is LOTS, equity qty
            // is shares — say which one the row is.
            const cls = t.instrument_class || t.kind || 'equity';
            const clsBadge = cls === 'option'
                ? ' <span class="badge small" style="background: rgba(99,102,241,0.15); color: #818cf8; padding: 1px 5px; border-radius: 4px;">OPT</span>'
                : '';
            const qtyLabel = cls === 'option' ? `${t.qty || 1} lot` : `${t.qty || 1}`;
            tr.innerHTML = `
                <td>${t.exit_ts ? esc(t.exit_ts.replace('T', ' ').slice(0, 19)) : '—'}</td>
                <td><strong>${esc(t.symbol || '—')}</strong>${clsBadge}</td>
                <td><span class="small">${esc(t.side || 'LONG')}</span></td>
                <td class="text-right">${esc(qtyLabel)}</td>
                <td class="text-right">₹${Number(t.entry_price || 0).toLocaleString()}</td>
                <td class="text-right">₹${Number(t.exit_price || 0).toLocaleString()}</td>
                <td class="text-right" style="color: ${pnlColor}; font-weight: 600;">${pnlSign}₹${pnl.toLocaleString()}</td>
                <td>${badge}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
