"""Fetch scope — what an unticked picker means (Data tab, 2026-10-02).

The invariant the UI promises and the server must enforce: with nothing
ticked, the active tab decides the fetch universe —

  * All      → NIFTY 200 equities + built-in indices
  * Equity   → NIFTY 200 equities only
  * Index    → indices only

It is never the whole scriptmaster catalogue again: that fallback let a job
walk into BSE bond symbols (``001HCCL29``) across ~15k rows while the page
said "all NIFTY 200 stocks". Explicitly ticked symbols always win over the
scope. These are the tests the original change shipped without.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from backtest.api import data_manager
from backtest.api.data_manager import _load_instruments
from backtest.data.coverage import INDEX_UNIVERSE, load_equity_universe

_INSTRUMENTS_DDL = """
CREATE TABLE instruments (
    instrument_token INTEGER PRIMARY KEY,
    tradingsymbol    TEXT NOT NULL,
    name             TEXT,
    instrument_type  TEXT,
    segment          TEXT,
    exchange         TEXT
)
"""


@pytest.fixture()
def engine(tmp_path):
    """Catalogue holding a slice of the NIFTY 200 plus decoys.

    The decoys are what the bug used to fetch: a BSE "Equity"-typed bond and
    a non-universe NSE equity. They may appear in the catalogue but must
    never enter an unticked fetch.
    """
    eng = create_engine(f"sqlite:///{tmp_path / 'cat.db'}", echo=False)
    universe = [r["symbol"] for r in load_equity_universe()]
    seeded = universe[:5]
    with eng.connect() as conn:
        conn.execute(text(_INSTRUMENTS_DDL))
        for i, sym in enumerate(seeded, start=1):
            conn.execute(
                text(
                    "INSERT INTO instruments (instrument_token, tradingsymbol, "
                    "instrument_type, exchange) VALUES (:t, :s, 'EQ', 'NSE')"
                ),
                {"t": 1000 + i, "s": sym},
            )
        for i, (sym, itype, exch) in enumerate(
            [
                ("001HCCL29", "Equity", "BSE"),  # the incident symbol
                ("PENNIND", "EQ", "NSE"),  # non-universe equity
            ],
            start=9000,
        ):
            conn.execute(
                text(
                    "INSERT INTO instruments (instrument_token, tradingsymbol, "
                    "instrument_type, exchange) VALUES (:t, :s, :i, :e)"
                ),
                {"t": i, "s": sym, "i": itype, "e": exch},
            )
        conn.commit()
    eng.seeded_universe = seeded  # type: ignore[attr-defined]
    yield eng
    eng.dispose()


def _symbols(rows):
    return {str(r["tradingsymbol"]).strip().upper() for r in rows}


# ---------------------------------------------------------------------------
# The three scope invariants
# ---------------------------------------------------------------------------


def test_equity_scope_is_the_curated_universe_not_the_catalogue(engine):
    rows, not_found = _load_instruments(engine, None, "equity")
    got = _symbols(rows)
    universe = {str(r["symbol"]).upper() for r in load_equity_universe()}
    assert got == {s.upper() for s in engine.seeded_universe}
    assert "001HCCL29" not in got and "PENNIND" not in got
    # every universe member the catalogue cannot resolve is REPORTED, never
    # silently dropped
    assert set(not_found) == universe - got
    assert all("instrument_token" in r for r in rows)


def test_index_scope_is_the_built_in_index_universe(engine):
    rows, not_found = _load_instruments(engine, None, "index")
    assert _symbols(rows) == {sym for sym, _ in INDEX_UNIVERSE}
    assert not not_found
    # every index carries a real token, and SENSEX lands on BSE
    assert all(str(r["instrument_token"]) for r in rows)
    sensex = [r for r in rows if r["tradingsymbol"] == "SENSEX"]
    assert sensex and sensex[0]["exchange"] == "BSE"


def test_all_scope_is_equities_plus_indices(engine):
    rows, _ = _load_instruments(engine, None, "equity,index")
    got = _symbols(rows)
    assert {s.upper() for s in engine.seeded_universe} <= got
    assert {sym for sym, _ in INDEX_UNIVERSE} <= got
    assert "001HCCL29" not in got  # the catalogue decoy stays out


# ---------------------------------------------------------------------------
# Explicit ticks win over the scope
# ---------------------------------------------------------------------------


def test_ticked_symbols_win_and_resolve_through_both_maps(engine):
    rows, not_found = _load_instruments(
        engine, ["RELIANCE", "NIFTY", "001HCCL29"], "index"
    )
    got = _symbols(rows)
    assert {"NIFTY", "001HCCL29"} <= got
    # RELIANCE is only in the catalogue if the seed happened to include it;
    # either way nothing may silently disappear:
    assert ("RELIANCE" in got) or ("RELIANCE" in not_found)


def test_unknown_symbols_are_counted_not_dropped(engine):
    rows, not_found = _load_instruments(engine, ["NOTAREALCO"], "equity")
    assert not rows
    assert not_found == ["NOTAREALCO"]


def test_no_symbols_and_default_scope_covers_everything_the_picker_shows(engine):
    """The Data tab's picker lists ``curated=1`` rows; an unticked All-tab
    fetch must resolve exactly that universe — no more (15k catalogue), no
    fewer (indices-only or empty)."""
    rows, _ = _load_instruments(engine, None)  # scope defaults to equity,index
    got = _symbols(rows)
    assert {sym for sym, _ in INDEX_UNIVERSE} <= got
    assert len(got) < 600  # curated slices, never the raw catalogue


# ---------------------------------------------------------------------------
# Endpoint plumbing
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch):
    from backtest.web.app import create_app

    app = create_app(source="synthetic")
    monkeypatch.setattr(
        data_manager, "_run_fetch_job", lambda *a, **k: None
    )  # never start real work
    # fake an authenticated broker session (token must be ≥16 chars)
    import backtest.brokers.session_manager as sm

    class _Fake:
        def get_active_session_token(self):
            return "x" * 32

    monkeypatch.setattr(sm, "get_session_manager", lambda: _Fake())
    return app.test_client()


def test_bad_scope_is_rejected_with_400(client):
    r = client.post(
        "/api/data/fetch",
        json={"timeframe": "1day", "scope": "every-single-stock"},
    )
    assert r.status_code == 400
    assert "scope" in r.get_json()["error"].lower()


def test_scope_is_plumbed_into_the_job(client, monkeypatch):
    captured = {}

    def spy(token, timeframe, from_date, to_date, symbols, scope):
        captured.update(scope=scope, symbols=symbols)

    monkeypatch.setattr(data_manager, "_run_fetch_job", spy)
    r = client.post("/api/data/fetch", json={"timeframe": "1day", "scope": "index"})
    assert r.status_code == 200
    assert r.get_json()["scope"] == "index"
    assert captured["scope"] == "index"
    # the fake job never runs, so clear the running flag for the next test
    data_manager._job["status"] = "idle"
