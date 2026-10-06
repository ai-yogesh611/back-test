"""What a backtest says when the window has no data — it names the dates.

The re-architecture (2026-10-06): the symbol picker no longer asks the server
to measure every instrument before it can draw a list, so the question "does
this symbol have this timeframe, over these dates?" is answered when the run
is attempted. That makes this error message load-bearing UI: it is the only
place the user learns that the request was impossible, so it has to say WHICH
dates are actually stored rather than that the fetch failed.

The message this replaced printed the query's own bounds back at the user
("between X and Y"), which they already knew because they had just typed them,
and named the internal source timeframe ("no 1min data") rather than the one
they asked for.
"""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from backtest.data.db_source import DbSource

_MDC_DDL = """
CREATE TABLE market_data_cache (
    data_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol    TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts        TEXT NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    volume    REAL
)
"""


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'err.db'}")
    with eng.begin() as conn:
        conn.execute(text(_MDC_DDL))
    yield eng
    eng.dispose()


def _seed(engine, rows):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO market_data_cache "
                "(symbol, timeframe, ts, open, high, low, close, volume) "
                "VALUES (:s, :t, :ts, 100, 101, 99, 100.5, 1000)"
            ),
            [{"s": s, "t": t, "ts": ts} for s, t, ts in rows],
        )


def _source(engine) -> DbSource:
    """A DbSource with the test engine injected (no DB-URL resolution)."""
    src = DbSource.__new__(DbSource)
    src._engine = engine
    return src


def _days(symbol: str, timeframe: str, first: str, last: str) -> list[tuple[str, str, str]]:
    """One bar per day, ``first``..``last`` inclusive (ISO dates)."""
    from datetime import date, timedelta

    d0, d1 = date.fromisoformat(first), date.fromisoformat(last)
    out = []
    d = d0
    while d <= d1:
        out.append((symbol, timeframe, f"{d.isoformat()} 09:15:00"))
        d += timedelta(days=1)
    return out


def _message(source: DbSource, symbol: str, start: str, end: str, interval: str) -> str:
    with pytest.raises(ValueError) as excinfo:
        source.get_candles(symbol, start, end, interval)
    return str(excinfo.value)


def test_the_error_names_the_dates_the_symbol_actually_holds(engine):
    """The requirement: "mention the details of available from-to dates"."""
    _seed(engine, _days("RELIANCE", "1min", "2026-09-02", "2026-09-14"))
    src = _source(engine)

    msg = _message(src, "RELIANCE", "2026-10-01", "2026-10-31", "1day")

    assert "RELIANCE" in msg
    assert "2026-10-01" in msg and "2026-10-31" in msg, "the window that failed"
    assert "02 Sep 2026" in msg and "14 Sep 2026" in msg, "the window that would work"
    assert "4,823" not in msg  # exact count varies; the DATES are the contract


def test_the_error_counts_the_stored_bars(engine):
    _seed(engine, _days("A", "1min", "2026-09-02", "2026-09-14"))
    msg = _message(_source(engine), "A", "2026-10-01", "2026-10-02", "1min")
    assert "(13 bars)" in msg, "13 calendar days of seeded bars"


def test_the_error_names_the_requested_timeframe_not_the_stored_one(engine):
    """Asking for 1day and being told "no 1min data" describes our resampling
    ladder, not the user's request. They asked for daily."""
    _seed(engine, _days("A", "1min", "2026-09-02", "2026-09-14"))
    msg = _message(_source(engine), "A", "2026-10-01", "2026-10-02", "1day")
    assert "no 1day data" in msg
    assert "no 1min data" not in msg


def test_a_symbol_with_no_data_at_all_says_so_and_where_to_get_it(engine):
    msg = _message(_source(engine), "GHOST", "2026-09-02", "2026-09-30", "1day")
    assert "GHOST" in msg
    assert "no data of any timeframe is stored" in msg
    assert "Data tab" in msg, "an error must still say what to do next"


def test_the_timeframe_the_user_asked_for_is_flagged_in_the_list(engine):
    """Distinguishes "that timeframe is missing entirely" from "it is there but
    not over those dates" — two different things to do next."""
    _seed(engine, _days("A", "1min", "2026-09-02", "2026-09-14"))
    _seed(engine, _days("A", "1day", "2026-01-02", "2026-03-31"))
    msg = _message(_source(engine), "A", "2026-10-01", "2026-10-02", "1day")

    assert "1day 02 Jan 2026 to 31 Mar 2026 (89 bars) [the timeframe you asked for]" in msg
    assert "1min 02 Sep 2026 to 14 Sep 2026" in msg


def test_stored_timeframes_are_listed_finest_first(engine):
    """CANONICAL_TIMEFRAMES is finest-first, and the message follows it so the
    fine data a user probably wants is not buried under a daily row."""
    _seed(engine, _days("A", "1day", "2026-01-02", "2026-03-31"))
    _seed(engine, _days("A", "1min", "2026-09-02", "2026-09-14"))
    msg = _message(_source(engine), "A", "2026-10-01", "2026-10-02", "1week")

    assert msg.index("1min") < msg.index("1day")


def test_a_reporting_failure_never_masks_the_original_error(engine, monkeypatch):
    """This helper runs on the failure path. If it throws, the user gets a
    traceback about the reporter instead of the reason their run failed.

    Only the DESCRIBE query is broken (the second read_sql) — failing the
    first would just mean the run never reached the code under test.
    """
    src = DbSource.__new__(DbSource)
    src._engine = engine

    real_read_sql = pd.read_sql
    calls = {"n": 0}

    def flaky(query, *a, **kw):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("reporting broke")
        return real_read_sql(query, *a, **kw)

    monkeypatch.setattr("backtest.data.db_source.pd.read_sql", flaky)
    msg = _message(src, "A", "2026-09-02", "2026-09-30", "1day")
    assert calls["n"] >= 2, "the describe query must actually have been attempted"
    assert "no 1day data" in msg
    assert "reporting broke" not in msg


def test_a_space_in_the_requested_window_is_still_served(engine):
    """Guard against over-eager errors: a symbol whose bars cover part of the
    window must return those bars, not fail because the window is wider."""
    _seed(engine, _days("A", "1min", "2026-09-02", "2026-09-14"))
    df = _source(engine).get_candles("A", "2026-08-01", "2026-09-30", "1min")
    assert not df.empty
    assert len(df) == 13
