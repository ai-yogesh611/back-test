"""The backtest window's end date, and why it used to lose a day.

Reported symptom: a September backtest of a 1-minute RELIANCE export returned
4817 stored bars at 1min but the engine only ever saw 4447 — exactly one
session short. A single-day run (`from_date == to_date`) failed outright with
"Symbol not found in database". The cause was one clause::

    AND ts BETWEEN :start AND :end

``:end`` is a plain date (``2026-09-30``) from an ``<input type="date">``, so it
resolves to midnight. Every bar of the final session is *after* midnight on the
end date, so the last day was dropped — silently, because a shorter frame is a
valid answer to the query. It happened on PostgreSQL and SQLite alike, and on
every timeframe: 1min lost 370 bars, 1hour lost 7, 1day lost 1, 1week lost 0.

The rule these tests pin: **``end`` includes the day it names.** A caller that
passes a timestamp instead of a date is asking for a real instant and gets one.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from backtest.data.db_source import DbSource, window_bounds

_MDC_DDL = """
CREATE TABLE market_data_cache (
    data_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol    TEXT NOT NULL,
    exchange  TEXT,
    timeframe TEXT NOT NULL,
    ts        TEXT NOT NULL,
    open      REAL NOT NULL,
    high      REAL NOT NULL,
    low       REAL NOT NULL,
    close     REAL NOT NULL,
    volume    REAL NOT NULL,
    source    TEXT,
    UNIQUE (symbol, exchange, timeframe, ts)
)
"""


# ---------------------------------------------------------------------------
# window_bounds — the rule itself
# ---------------------------------------------------------------------------


def test_a_date_only_end_widens_to_the_next_midnight():
    lo, hi = window_bounds("2026-09-02", "2026-09-30")
    assert lo == pd.Timestamp("2026-09-02 00:00")
    assert hi == pd.Timestamp("2026-10-01 00:00")


def test_the_final_midnight_bar_of_the_end_date_is_not_the_cutoff():
    """The exact failure: ``ts < 2026-09-30`` excludes the whole 30th."""
    _, hi = window_bounds("2026-09-02", "2026-09-30")
    assert pd.Timestamp("2026-09-30 09:15") < hi
    assert pd.Timestamp("2026-09-30 15:28") < hi
    assert not (pd.Timestamp("2026-10-01 09:15") < hi)


def test_the_first_bar_of_a_single_day_window_is_included():
    lo, hi = window_bounds("2026-09-30", "2026-09-30")
    assert lo <= pd.Timestamp("2026-09-30 09:15") < hi


def test_an_end_that_carries_a_clock_time_stays_an_instant():
    """A mid-session cut-off must not be silently widened by a whole day."""
    _, hi = window_bounds("2026-09-02", "2026-09-30 12:00")
    assert pd.Timestamp("2026-09-30 12:00") < hi
    assert not (pd.Timestamp("2026-09-30 12:01") < hi)


def test_an_iso_timestamp_end_is_also_an_instant():
    _, hi = window_bounds("2026-09-02", "2026-09-30T15:29:00")
    assert pd.Timestamp("2026-09-30 15:29") < hi
    assert not (pd.Timestamp("2026-09-30 15:30") < hi)


def test_a_window_crossing_a_month_boundary():
    lo, hi = window_bounds("2026-09-30", "2026-09-30")
    assert lo < hi
    _, hi2 = window_bounds("2026-09-01", "2026-09-30")
    assert hi2 == pd.Timestamp("2026-10-01")


# ---------------------------------------------------------------------------
# Through a real database — the read path the backtest actually uses
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded(tmp_path):
    """400 1-minute bars on 2026-09-30, the day the old query dropped."""
    engine = create_engine(f"sqlite:///{tmp_path / 'bars.db'}")
    with engine.begin() as conn:
        conn.execute(text(_MDC_DDL))
        conn.execute(
            text(
                "INSERT INTO market_data_cache "
                "(symbol, exchange, timeframe, ts, open, high, low, close, volume, source) "
                "VALUES ('RELIANCE', 'NSE', '1min', :ts, 100, 101, 99, 100.5, 1000, 'test')"
            ),
            [
                {"ts": f"2026-09-30 {9 + (m // 60):02d}:{m % 60:02d}:00"}
                for m in range(15, 15 + 400)
            ],
        )
    yield engine
    engine.dispose()


def _source(engine) -> DbSource:
    src = DbSource.__new__(DbSource)  # no engine/URL resolution needed
    src._engine = engine
    return src


def test_the_end_dates_bars_are_returned(seeded):
    out = _source(seeded).get_candles("RELIANCE", "2026-09-30", "2026-09-30", "1min")
    assert len(out) == 400


def test_a_later_end_date_returns_the_same_bars(seeded):
    """Asking through 1 October cannot invent data, and must not lose any."""
    out = _source(seeded).get_candles("RELIANCE", "2026-09-30", "2026-10-01", "1min")
    assert len(out) == 400


def test_the_last_bar_is_present_and_is_the_latest_one(seeded):
    out = _source(seeded).get_candles("RELIANCE", "2026-09-30", "2026-09-30", "1min")
    assert out.index[-1] == pd.Timestamp("2026-09-30 15:54")
    assert out.index[0] == pd.Timestamp("2026-09-30 09:15")


def test_a_window_starting_on_the_end_date_alone_is_not_empty(seeded):
    """``from_date == to_date`` used to raise "Symbol not found in database"."""
    out = _source(seeded).get_candles("RELIANCE", "2026-09-30", "2026-09-30", "1min")
    assert len(out) == 400


def test_a_genuinely_absent_window_still_raises(seeded):
    """Widening the end date must not turn a real gap into a silent empty frame."""
    with pytest.raises(ValueError, match="not found in database"):
        _source(seeded).get_candles("RELIANCE", "2026-09-25", "2026-09-25", "1min")


def test_the_error_message_reports_the_resolved_window(seeded):
    """The bug was invisible for months; the message now shows the bounds."""
    with pytest.raises(ValueError) as excinfo:
        _source(seeded).get_candles("RELIANCE", "2026-09-25", "2026-09-25", "1min")
    assert "2026-09-26 00:00:00" in str(excinfo.value)


def test_bars_before_the_start_date_are_not_returned(seeded):
    out = _source(seeded).get_candles("RELIANCE", "2026-09-30 12:00", "2026-09-30", "1min")
    assert out.index[0] == pd.Timestamp("2026-09-30 12:00")


def test_the_exclusive_upper_bound_never_leaks_the_next_day():
    """Guard the other direction: widening must stop at the end of the day."""
    _, hi = window_bounds("2026-09-01", "2026-09-30")
    assert not (pd.Timestamp("2026-10-01 00:00") < hi)
    assert datetime(2026, 9, 30, 23, 59, 59) < hi.to_pydatetime()
