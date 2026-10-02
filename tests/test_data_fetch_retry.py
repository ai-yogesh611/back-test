"""Tests for the mStock 502 retry path in the chunked fetch (data_manager).

Context (2026-10-03): the Data tab's ALKEM fetch reported "done, 0 failed"
while mStock's gateway 502'd ~half of the 41 chunk requests at the loop's
0.15 s cadence — manual re-probes seconds later returned the bars fine.
``_get_historical_with_retry`` must recover from transient 502/503/504, and
chunks that die even after retries must be counted, never silently swallowed.
"""

from __future__ import annotations

import pytest

from backtest.api import data_manager as dm


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


# ---------------------------------------------------------------------------
# _get_historical_with_retry
# ---------------------------------------------------------------------------


def test_transient_502_is_retried_and_recovers(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            return _Resp(502)
        return _Resp(200, _candles())

    slept: list[float] = []
    monkeypatch.setattr(dm.requests, "get", fake_get)
    monkeypatch.setattr(dm.time, "sleep", lambda s: slept.append(s))

    resp = dm._get_historical_with_retry("u", {}, {})
    assert resp.status_code == 200
    assert calls["n"] == 3
    assert len(slept) == 2  # backoff strictly between attempts, none after success


def test_persistent_failure_raises_after_bounded_attempts(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        calls["n"] += 1
        return _Resp(503)

    monkeypatch.setattr(dm.requests, "get", fake_get)
    monkeypatch.setattr(dm.time, "sleep", lambda s: None)

    with pytest.raises(dm.requests.RequestException) as ei:
        dm._get_historical_with_retry("u", {}, {}, attempts=3)
    assert calls["n"] == 3
    assert "503" in str(ei.value)


def test_4xx_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        calls["n"] += 1
        return _Resp(401)

    monkeypatch.setattr(dm.requests, "get", fake_get)
    monkeypatch.setattr(dm.time, "sleep", lambda s: None)

    with pytest.raises(dm.requests.RequestException):
        dm._get_historical_with_retry("u", {}, {})
    assert calls["n"] == 1  # an auth failure will not fix itself — fail fast


# ---------------------------------------------------------------------------
# _fetch_bars_chunked wiring
# ---------------------------------------------------------------------------


def test_chunk_loop_counts_chunks_that_die_even_after_retries(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        calls["n"] += 1
        if params["from"] == "2026-06-01":
            return _Resp(502)  # first chunk always fails (retries exhausted)
        return _Resp(200, _candles(2))

    monkeypatch.setattr(dm.requests, "get", fake_get)
    monkeypatch.setattr(dm.time, "sleep", lambda s: None)

    bars, errors = dm._fetch_bars_chunked(
        "key", "tok", "11703", "2026-06-01", "2026-06-07", "NSE", "minute", 2
    )
    assert errors == 1  # surfaced, not silent
    assert len(bars) == 2  # the surviving chunk's bars still land


def test_chunk_loop_recovers_transient_502_with_zero_errors(monkeypatch):
    seen: dict[str, int] = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        key = params["from"]
        seen[key] = seen.get(key, 0) + 1
        if key == "2026-06-01" and seen[key] == 1:
            return _Resp(502)  # transient
        return _Resp(200, _candles(3))

    monkeypatch.setattr(dm.requests, "get", fake_get)
    monkeypatch.setattr(dm.time, "sleep", lambda s: None)

    bars, errors = dm._fetch_bars_chunked(
        "key", "tok", "11703", "2026-06-01", "2026-06-07", "NSE", "minute", 2
    )
    assert errors == 0
    assert len(bars) == 6  # both chunks fully collected after the retry


# ---------------------------------------------------------------------------
# Adaptive pacing (2026-10-03): 0.5 s base, 2.0 s after 3 consecutive lost
# chunks, decay back after 3 clean ones — mStock bad hours cost less than a
# blanket pause, good hours stay fast.
# ---------------------------------------------------------------------------


def _pacing_get(fail_windows: set[str]):
    """Fake requests.get: the listed chunk windows 502 on EVERY attempt."""

    def fake_get(url, headers=None, params=None, timeout=None):
        if params["from"] in fail_windows:
            return _Resp(502)
        return _Resp(200, _candles(1))

    return fake_get


def test_healthy_run_paces_at_base(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(dm.requests, "get", _pacing_get(set()))
    monkeypatch.setattr(dm.time, "sleep", lambda s: sleeps.append(s))

    dm._fetch_bars_chunked(
        "key", "tok", "11703", "2026-06-01", "2026-06-10", "NSE", "minute", 2
    )
    assert sleeps == [dm.CHUNK_SLEEP_BASE] * 3  # no widening when all is well


def test_pace_widens_after_three_consecutive_failures_then_decays(monkeypatch):
    # 6 chunks (01,04,07,10,13,16-Jun); first three never recover.
    fails = {"2026-06-01", "2026-06-04", "2026-06-07"}
    sleeps: list[float] = []
    monkeypatch.setattr(dm.requests, "get", _pacing_get(fails))
    monkeypatch.setattr(dm.time, "sleep", lambda s: sleeps.append(s))

    bars, errors = dm._fetch_bars_chunked(
        "key", "tok", "11703", "2026-06-01", "2026-06-18", "NSE", "minute", 2
    )
    assert errors == 3
    assert len(bars) == 3  # chunks 4-6 collected

    pacing = [s for s in sleeps if s in (dm.CHUNK_SLEEP_BASE, dm.CHUNK_SLEEP_WIDE)]
    # chunks 1-2 fail one-by-one (below threshold) -> base; 3rd consecutive
    # failure widens; chunks 4,5 stay wide; 3rd clean success decays to base.
    assert pacing == [0.5, 0.5, 2.0, 2.0, 2.0, 0.5]
