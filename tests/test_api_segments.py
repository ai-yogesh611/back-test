"""Phase B — segments API + runner-create-with-segment (API-SPEC-001).

* ``GET /api/segments`` returns the configured segments + data routing;
* ``POST /api/segments`` validates (and only persists when asked);
* runner creation accepts ``segment`` and derives mode/broker from it;
* unknown segment / mode conflict → 400 (fail-closed, never guessed).
"""

from __future__ import annotations

import textwrap

import pytest

from backtest.brokers.segments import reset_segments_config
from backtest.brokers.session_manager import reset_default_manager
from backtest.web.app import create_app

YAML = textwrap.dedent(
    """
    segments:
      options_index:
        display_name: "Index Options"
        broker: mstock
        mode: paper
        allocated_capital: 1200000
      equity_intraday:
        display_name: "Equity Intraday"
        broker: dhan
        mode: paper
        allocated_capital: 800000
    data:
      primary: mstock
    """
)


@pytest.fixture()
def api(tmp_path, monkeypatch):
    path = tmp_path / "segments.yaml"
    path.write_text(YAML, encoding="utf-8")
    monkeypatch.setenv("SEGMENTS_CONFIG_PATH", str(path))
    monkeypatch.setenv("BROKER_REMEMBER_SESSION_PATH", str(tmp_path))
    monkeypatch.delenv("BROKER_REMEMBER_SESSION", raising=False)
    reset_segments_config()
    reset_default_manager()
    app = create_app(source="synthetic")
    try:
        yield app.test_client()
    finally:
        reset_segments_config()
        reset_default_manager()


def test_get_segments(api):
    body = api.get("/api/segments").get_json()
    assert body["success"] is True
    names = {s["name"] for s in body["segments"]}
    assert names == {"options_index", "equity_intraday"}
    seg = next(s for s in body["segments"] if s["name"] == "options_index")
    assert seg["broker"] == "mstock"
    assert seg["mode"] == "paper"
    assert seg["allocated_capital"] == 1200000
    assert body["data"]["primary"] == "mstock"


def test_post_segments_validates(api):
    ok = api.post(
        "/api/segments",
        json={"segment_id": "swing", "broker": "dhan", "mode": "paper",
              "allocated_capital": 500000},
    )
    assert ok.status_code == 200
    assert ok.get_json()["success"] is True
    assert ok.get_json()["persisted"] is False

    bad = api.post(
        "/api/segments",
        json={"segment_id": "bad", "broker": "zerodha", "mode": "live"},
    )
    assert bad.status_code == 400
    assert "unknown broker" in bad.get_json()["message"].lower()


def test_create_runner_with_segment(api):
    resp = api.post(
        "/api/portfolio/runner/create",
        json={
            "name": "seg-runner",
            "strategy": "sma_crossover",
            "symbol": "RELIANCE",
            "allocated_capital": 100000,
            "segment": "equity_intraday",
            "source": "synthetic",
        },
    )
    body = resp.get_json()
    assert resp.status_code in (200, 201), body
    runner = body.get("runner") or body
    assert runner["segment"] == "equity_intraday"
    # Segment mode is paper → runner is paper; paper never binds a broker.
    assert runner["mode"] == "paper"
    assert runner["broker"] == "paper"


def test_create_runner_unknown_segment_400(api):
    resp = api.post(
        "/api/portfolio/runner/create",
        json={
            "name": "bad-seg",
            "strategy": "sma_crossover",
            "symbol": "RELIANCE",
            "allocated_capital": 100000,
            "segment": "does_not_exist",
        },
    )
    assert resp.status_code == 400


def test_create_runner_segment_mode_conflict_400(api):
    resp = api.post(
        "/api/portfolio/runner/create",
        json={
            "name": "conflict",
            "strategy": "sma_crossover",
            "symbol": "RELIANCE",
            "allocated_capital": 100000,
            "segment": "equity_intraday",
            "mode": "live",
        },
    )
    assert resp.status_code == 400
