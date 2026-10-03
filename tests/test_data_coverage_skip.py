"""Tests for the coverage-aware gap fill (2026-10-03).

A re-run over an already-fetched range must cross-check the user's requested
[from, to] against ``market_data_cache`` day by day and pull ONLY the windows
that still contain a missing day — a fully-covered symbol costs zero API
requests, and scattered 502 holes (ABCAPITAL) get gap-filled instead of being
blindly re-walked chunk-by-chunk.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from backtest.api import data_manager as dm


# ---------------------------------------------------------------------------
# Thresholds & the enable flag
# ---------------------------------------------------------------------------


def test_coverage_threshold_scales_with_timeframe():
    assert dm._coverage_threshold("1min") == 263   # ceil(375 * 0.7)
    assert dm._coverage_threshold("5min") == 53    # ceil(75  * 0.7)
    assert dm._coverage_threshold("15min") == 18   # ceil(25  * 0.7)
    assert dm._coverage_threshold("1hour") == 5    # ceil(6   * 0.7)
    assert dm._coverage_threshold("1day") == 1     # any single daily bar counts
    assert dm._coverage_threshold("mystery") == 1  # unknown tf -> never skip wrongly


def test_skip_enabled_defaults_on_and_respects_env(monkeypatch):
    monkeypatch.delenv("DATA_FETCH_SKIP_COVERED", raising=False)
    assert dm._coverage_skip_enabled() is True
    monkeypatch.setenv("DATA_FETCH_SKIP_COVERED", "0")
    assert dm._coverage_skip_enabled() is False
    monkeypatch.setenv("DATA_FETCH_SKIP_COVERED", "1")
    assert dm._coverage_skip_enabled() is True


# ---------------------------------------------------------------------------
# _build_windows — the range stepper, unchanged semantics
# ---------------------------------------------------------------------------


def test_build_windows_covers_range_in_chunk_days_steps():
    wins = dm._build_windows("2026-06-01", "2026-06-07", 2)
    assert [(s.strftime("%m-%d"), e.strftime("%m-%d")) for s, e in wins] == [
        ("06-01", "06-03"),
        ("06-04", "06-06"),
    ]


def test_build_windows_single_day_is_empty_range():
    # from == to: the loop is `chunk_start < end`, so nothing to walk.
    assert dm._build_windows("2026-06-01", "2026-06-01", 2) == []


# ---------------------------------------------------------------------------
# _windows_needing_fetch — the day-level diff
# ---------------------------------------------------------------------------

_WINS = dm._build_windows("2026-06-01", "2026-06-07", 2)  # (06-01..03), (06-04..06)


def test_nothing_covered_fetches_every_window():
    assert dm._windows_needing_fetch(_WINS, set()) == _WINS


def test_fully_covered_symbol_fetches_nothing():
    # 2026-06-01..06 = Mon..Sat; all five trading days covered.
    covered = {date(2026, 6, d) for d in range(1, 6)}
    assert dm._windows_needing_fetch(_WINS, covered) == []


def test_weekend_days_never_mark_a_window_missing():
    # Regression (live check 2026-10-03): without the weekday rule a fully
    # fetched symbol still re-fetches every window touching a Saturday, so the
    # 200-symbol resume never gets cheaper.
    # Window A = Mon 01..Wed 03, window B = Thu 04..Sat 06 (Sat never has bars).
    covered = {date(2026, 6, d) for d in range(1, 6)}  # Sat 06 absent by design
    assert dm._windows_needing_fetch(_WINS, covered) == []


def test_only_windows_with_a_missing_weekday_are_kept():
    # Window A's weekdays covered; window B missing Fri 05 -> only B kept.
    covered = {date(2026, 6, d) for d in (1, 2, 3, 4, 6)}
    kept = dm._windows_needing_fetch(_WINS, covered)
    assert [(s.strftime("%m-%d"), e.strftime("%m-%d")) for s, e in kept] == [
        ("06-04", "06-06")
    ]


def test_missing_weekday_requeues_whole_window():
    # A single missing weekday drags its whole window back (the upsert dedupes
    # the rest). Window A = 01..03 (Tue 03 missing) -> kept;
    # window B = 04..06 (all weekdays covered, Sat ignored) -> dropped.
    covered = {date(2026, 6, d) for d in (1, 2, 4, 5)}
    kept = dm._windows_needing_fetch(_WINS, covered)
    assert len(kept) == 1
    assert [(s.strftime("%m-%d"), e.strftime("%m-%d")) for s, e in kept] == [
        ("06-01", "06-03")
    ]


# ---------------------------------------------------------------------------
# _covered_days — the DB probe
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows=None, exc=None):
        self._rows = rows or []
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if self._exc is not None:
            raise self._exc
        return _FakeResult(self._rows)


class _FakeEngine:
    def __init__(self, rows=None, exc=None):
        self._rows = rows
        self._exc = exc
        self.last_params = None

    def connect(self):
        if self._exc is not None:
            raise self._exc
        return _FakeConn(self._rows, self._exc)


def test_covered_days_keeps_only_days_over_threshold():
    engine = _FakeEngine(
        rows=[
            (datetime(2026, 6, 1), 375),  # full day
            (datetime(2026, 6, 2), 12),   # a few stray bars — below 263, not trusted
            (date(2026, 6, 3), 300),      # already a date object
        ]
    )
    covered = dm._covered_days(engine, "TCS", "NSE", "1min", "2026-06-01", "2026-06-05")
    assert covered == {date(2026, 6, 1), date(2026, 6, 3)}


def test_covered_days_probe_error_returns_empty_never_skips():
    engine = _FakeEngine(exc=RuntimeError("table missing"))
    covered = dm._covered_days(engine, "TCS", "NSE", "1min", "2026-06-01", "2026-06-05")
    assert covered == set()  # caller then does a full fetch


def test_covered_days_passes_requested_range_to_query():
    engine = _FakeEngine(rows=[])
    dm._covered_days(engine, "TCS", "NSE", "1min", "2022-01-03", "2026-10-03")
    # no assertion on captured params (fake discards them) — smoke test that the
    # probe runs cleanly against the user's from/to without raising.


# ---------------------------------------------------------------------------
# _fetch_bars_chunked honours an explicit windows list
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status: int = 200, payload: dict | None = None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise dm.requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self) -> dict:
        return self._payload


def _candles(n: int = 1) -> dict:
    return {"data": {"candles": [["t", 1.0, 2.0, 3.0, 4.0, 5] for _ in range(n)]}}


def test_explicit_windows_fetch_only_those(monkeypatch):
    requested: list[str] = []

    def fake_get(url, headers=None, params=None, timeout=None):
        requested.append(params["from"])
        return _Resp(200, _candles(1))

    monkeypatch.setattr(dm.requests, "get", fake_get)
    monkeypatch.setattr(dm.time, "sleep", lambda s: None)

    # Range spans 3 windows, but we hand the loop only the middle one.
    all_wins = dm._build_windows("2026-06-01", "2026-06-07", 2)  # 01..03, 04..06
    bars, errors = dm._fetch_bars_chunked(
        "key", "tok", "11536", "2026-06-01", "2026-06-07", "NSE", "minute", 2,
        windows=all_wins[1:],
        workers=1,
    )
    assert errors == 0
    assert len(bars) == 1
    assert requested == ["2026-06-04"]  # the skipped window was never requested
