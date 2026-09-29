"""``GET /api/data/coverage`` — the shared symbol-picker endpoint (PRD §1.3).

Two properties matter most and are pinned here: the picker is NEVER empty
(an app with no database still offers the indices and the shipped universe,
marked as having no data), and it never claims a symbol has data it does not.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from backtest.api import data_manager
from backtest.web.app import create_app

_MDC_DDL = """
CREATE TABLE market_data_cache (
    data_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol    TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts        TEXT NOT NULL
)
"""


@pytest.fixture()
def app(tmp_path):
    """An app whose coverage endpoint reads a seeded SQLite cache."""
    application = create_app(source="synthetic")
    url = f"sqlite:///{tmp_path / 'coverage.db'}"
    eng = create_engine(url, echo=False)
    with eng.connect() as conn:
        conn.execute(text(_MDC_DDL))
        for symbol, timeframe, ts in [
            ("RELIANCE", "1day", "2024-01-05"),
            ("RELIANCE", "1day", "2024-01-08"),
            ("RELIANCE", "1min", "2024-01-05 09:15"),
            ("NIFTY", "1day", "2024-01-05"),
        ]:
            conn.execute(
                text("INSERT INTO market_data_cache (symbol, timeframe, ts) VALUES (:s,:t,:ts)"),
                {"s": symbol, "t": timeframe, "ts": ts},
            )
        conn.commit()
    eng.dispose()

    original = data_manager.DB_URL
    data_manager.DB_URL = url
    data_manager.invalidate_coverage_cache()
    yield application
    data_manager.DB_URL = original
    data_manager.invalidate_coverage_cache()


@pytest.fixture()
def client(app):
    return app.test_client()


def get(client, path="/api/data/coverage", **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"{path}?{query}" if query else path)


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_endpoint_answers_with_the_full_contract(client):
    body = get(client).get_json()
    for key in (
        "instruments",
        "total",
        "returned",
        "known_total",
        "available_total",
        "db_available",
        "catalogue_source",
        "instrument_types",
        "hint",
        "generated_at",
    ):
        assert key in body, key
    assert body["instrument_types"] == ["equity", "index", "futures", "options"]


def test_a_symbol_with_bars_reports_its_real_coverage(client):
    rows = {r["symbol"]: r for r in get(client, q="RELIANCE").get_json()["instruments"]}
    row = rows["RELIANCE"]
    assert row["data_available"] is True
    assert row["bars_count"] == 3
    assert row["from_date"] == "2024-01-05"
    assert row["to_date"] == "2024-01-08"
    # §1.4 depends on this: only timeframes that really exist are offered
    assert row["timeframes_available"] == ["1min", "1day"]
    assert "hint" not in row


def test_a_symbol_with_no_bars_is_listed_and_explained(client):
    """The §1.3 complaint, verbatim: it used to silently disappear."""
    body = get(client).get_json()
    rows = {r["symbol"]: r for r in body["instruments"]}
    assert "TCS" in rows
    assert rows["TCS"]["data_available"] is False
    assert rows["TCS"]["bars_count"] == 0
    assert rows["TCS"]["timeframes_available"] == []
    assert rows["TCS"]["hint"] == body["hint"]


def test_indices_are_offered_whether_or_not_they_have_bars(client):
    rows = {r["symbol"]: r for r in get(client, types="index").get_json()["instruments"]}
    assert {"NIFTY", "BANKNIFTY", "SENSEX", "INDIAVIX", "MIDCPNIFTY"} <= set(rows)
    assert rows["NIFTY"]["data_available"] is True
    assert rows["BANKNIFTY"]["data_available"] is False


def test_totals_separate_known_from_runnable(client):
    body = get(client, limit=0).get_json()
    assert body["known_total"] > 100  # the shipped universe is always there
    assert body["available_total"] == 2  # only RELIANCE and NIFTY have bars
    assert body["returned"] == body["total"] == body["known_total"]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_search_filters_by_symbol_and_name(client):
    assert get(client, q="reliance").get_json()["total"] == 1
    assert get(client, q="Reliance").get_json()["total"] == 1
    assert get(client, q="ZZZZ").get_json()["total"] == 0


def test_equity_and_index_tabs_partition_the_list(client):
    all_rows = {r["symbol"] for r in get(client, limit=0).get_json()["instruments"]}
    equity = {r["symbol"] for r in get(client, types="equity", limit=0).get_json()["instruments"]}
    index = {r["symbol"] for r in get(client, types="index", limit=0).get_json()["instruments"]}
    assert equity and index
    assert equity.isdisjoint(index)


def test_available_filter_hides_symbols_that_cannot_run(client):
    body = get(client, available=1, limit=0).get_json()
    assert {r["symbol"] for r in body["instruments"]} == {"RELIANCE", "NIFTY"}


def test_paging_covers_the_whole_list(client):
    everything = get(client, limit=0).get_json()
    first = get(client, limit=2, offset=0).get_json()
    last = get(client, limit=2, offset=everything["total"] - 1).get_json()
    assert first["total"] == last["total"] == everything["total"]
    assert first["returned"] == 2 and last["returned"] == 1
    assert first["offset"] == 0 and last["offset"] == everything["total"] - 1


def test_bad_paging_arguments_are_400_not_a_stack_trace(client):
    assert get(client, limit="abc").status_code == 400
    assert get(client, offset="abc").status_code == 400
    assert "error" in get(client, limit="abc").get_json()


# ---------------------------------------------------------------------------
# Degradation + cache
# ---------------------------------------------------------------------------


def test_picker_is_never_empty_without_a_database(monkeypatch, app):
    """`--source synthetic` with no DB: still a usable list, honestly labelled."""
    monkeypatch.setattr(data_manager, "DB_URL", "postgresql+psycopg2://nobody@127.0.0.1:1/none")
    data_manager.invalidate_coverage_cache()
    body = app.test_client().get("/api/data/coverage").get_json()

    assert body["db_available"] is False
    assert body["catalogue_source"] == "builtin"
    assert body["known_total"] > 100
    assert body["available_total"] == 0
    assert body["warnings"], "an unreachable database must be reported, not hidden"
    assert all(r["data_available"] is False for r in body["instruments"])


def test_catalogue_absent_but_cache_present_still_serves(client):
    """`instruments` comes from a broker ingest; its absence is normal."""
    body = get(client, limit=0).get_json()
    assert body["db_available"] is True
    assert body["catalogue_source"] == "market_data_cache"
    assert body["available_total"] == 2


def test_repeated_reads_reuse_the_cached_report(client, monkeypatch):
    from backtest.data import coverage as coverage_mod

    calls = {"n": 0}
    original = coverage_mod.load_bar_coverage

    def counting(eng):
        calls["n"] += 1
        return original(eng)

    monkeypatch.setattr(coverage_mod, "load_bar_coverage", counting)
    data_manager.invalidate_coverage_cache()
    get(client)
    get(client)
    get(client)
    assert calls["n"] == 1, "three pages mounting a picker must not rescan the cache"


def test_refresh_bypasses_the_cache(client, monkeypatch):
    from backtest.data import coverage as coverage_mod

    calls = {"n": 0}
    original = coverage_mod.load_bar_coverage

    def counting(eng):
        calls["n"] += 1
        return original(eng)

    monkeypatch.setattr(coverage_mod, "load_bar_coverage", counting)
    data_manager.invalidate_coverage_cache()
    get(client)
    get(client, refresh=1)
    assert calls["n"] == 2


def test_fetch_job_invalidates_the_cached_report(client):
    get(client)
    assert data_manager._coverage_cache["report"] is not None
    data_manager.invalidate_coverage_cache()
    assert data_manager._coverage_cache["report"] is None
