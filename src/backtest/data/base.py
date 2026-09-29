from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

CANDLE_COLUMNS = ["open", "high", "low", "close", "volume"]

#: The ONE canonical timeframe vocabulary (ticket P4.3). Every layer — API,
#: config, DB (``market_data_cache.timeframe`` CHECK), UI, feeds — speaks
#: these names. Resolved with the lead as the descriptive set.
CANONICAL_TIMEFRAMES = ("1min", "5min", "15min", "1hour", "4hour", "1day", "1week")

#: Canonical timeframe -> mStock TypeA wire interval. Broker-specific
#: translation only; the rest of the codebase never speaks these names.
#: ``4hour`` has no mStock historical equivalent (intentionally absent).
MSTOCK_INTERVAL_MAP = {
    "1min": "minute",
    "5min": "5minute",
    "15min": "15minute",
    "1hour": "60minute",
    "1day": "day",
    "1week": "week",
}

#: NSE equity session length in minutes (09:15–15:30).
TRADING_MINUTES_PER_DAY = 375

#: Trading days per year (NSE). The annualisation base for every daily-derived
#: number: Sharpe, CAGR, Sortino, volatility.
TRADING_DAYS_PER_YEAR = 252

#: Canonical timeframe -> bars in one NSE trading day. Weekly is the
#: exception: a week is not a fifth of a 252-day year, it is 52 of them.
BARS_PER_TRADING_DAY: dict[str, float] = {
    "1min": TRADING_MINUTES_PER_DAY,  # 375
    "5min": 75,
    "10min": 37,  # not in CANONICAL_TIMEFRAMES, but brokers ask for it
    "15min": 25,
    "30min": 12,  # idem
    "1hour": 6,
    "4hour": 2,
    "1day": 1,
}

#: Aliases accepted from the UI and from broker payloads. Same spirit as
#: ``resolve_interval`` but a pure map, so the engine can ask for a period
#: count without pulling in the whole backtest module.
_TIMEFRAME_ALIASES: dict[str, str] = {
    "1m": "1min",
    "m1": "1min",
    "1minute": "1min",
    "minute": "1min",
    "5m": "5min",
    "m5": "5min",
    "5minute": "5min",
    "10m": "10min",
    "m10": "10min",
    "10minute": "10min",
    "15m": "15min",
    "m15": "15min",
    "15minute": "15min",
    "30m": "30min",
    "m30": "30min",
    "30minute": "30min",
    "1h": "1hour",
    "h1": "1hour",
    "60min": "1hour",
    "60minute": "1hour",
    "hour": "1hour",
    "4h": "4hour",
    "h4": "4hour",
    "240min": "4hour",
    "4hour": "4hour",
    "d": "1day",
    "1d": "1day",
    "day": "1day",
    "daily": "1day",
    "1daily": "1day",
    "w": "1week",
    "1w": "1week",
    "week": "1week",
    "weekly": "1week",
}

#: Weeks per year — used for every ``1week`` annualisation.
WEEKS_PER_YEAR = 52


def normalize_timeframe(timeframe: str | None) -> str | None:
    """Map any accepted spelling onto the canonical name (``None`` if unknown).

    Case-insensitive and alias-tolerant, so the engine can be handed ``"1D"``,
    ``"day"`` or ``"1day"`` from three different layers and get one answer.
    """
    key = str(timeframe or "").strip().lower()
    if not key:
        return None
    if key in CANONICAL_TIMEFRAMES or key in BARS_PER_TRADING_DAY:
        return key
    return _TIMEFRAME_ALIASES.get(key)


def periods_per_year(timeframe: str | None, default: int = TRADING_DAYS_PER_YEAR) -> int:
    """Periods in a year at this granularity — the annualisation factor.

    ``periods_per_year`` is what turns a per-bar mean and a per-bar standard
    deviation into an annual Sharpe and an annual CAGR. Scoring 1-minute bars
    with the daily factor of 252 understates volatility by ~15x and reports a
    Sharpe that is confidently wrong (PRD backTest-enhance §1.4).

    Unknown or missing timeframes fall back to the daily factor rather than
    raising: a caller that never said must not crash a run, and daily is the
    one assumption that is right for the overwhelmingly common case.
    """
    key = normalize_timeframe(timeframe)
    if key is None:
        return default
    if key == "1week":
        return WEEKS_PER_YEAR
    per_day = BARS_PER_TRADING_DAY.get(key)
    if per_day is None:
        return default
    return max(1, round(TRADING_DAYS_PER_YEAR * per_day))


@runtime_checkable
class DataSource(Protocol):
    def get_candles(
        self, symbol: str, start: str, end: str, interval: str = "1day"
    ) -> pd.DataFrame: ...


def normalize_candles(df: pd.DataFrame) -> pd.DataFrame:
    if df is None:
        raise ValueError("candles frame is required")

    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]

    missing = [c for c in CANDLE_COLUMNS if c not in out.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    if out.empty:
        raise ValueError("candles frame is empty")

    if not isinstance(out.index, pd.DatetimeIndex):
        raise ValueError("candles index must be DatetimeIndex")

    out = out.loc[:, CANDLE_COLUMNS]
    out = out[~out.index.duplicated(keep="last")]
    out = out.sort_index()
    out = out.dropna(subset=["close"])

    if out.empty:
        raise ValueError("candles frame is empty after cleaning")

    for col in CANDLE_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="raise")

    return out
