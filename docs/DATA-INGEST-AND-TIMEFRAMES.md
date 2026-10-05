# Ingesting your own data, and what "correct" resampling looks like

Two things go wrong when a 1-minute export is turned into backtests, and both
are silent:

1. **The bars are stored under timestamps that do not mean what the resampler
   thinks they mean.** A file of naive *UTC* timestamps resampled to `1hour` /
   `4hour` / `1day` produces windows that start at 03:45 IST instead of 09:15.
   Nothing errors. `1day` and `1week` look perfect even when the file is UTC,
   because the UTC day boundary falls outside the NSE session either way — so
   *a daily check does not test this*.
2. **The export does not look like the parser expects** (a `ts_ist` column, a
   `date` + `time` pair, a bare `time` column). The quiet failure mode is the
   last one: a `time`-only column spanning several days, accepted as a full
   timestamp, stamps every row onto the day the import ran.

The tooling below exists to make both of those loud.

---

## 1. Load the CSV

```bash
PYTHONPATH=src python scripts/ingest_csv_to_mdc.py \
    --csv tools/out/reliance_1min_sept2026.csv \
    --symbol RELIANCE --timeframe 1min --source mstock
```

Dry run first if you want to see the shape without writing:

```bash
PYTHONPATH=src python scripts/ingest_csv_to_mdc.py --csv ... --symbol RELIANCE --dry-run
```

This is the offline equivalent of the Data tab fetch. It writes to the same
`market_data_cache` table with the same upsert semantics and the same value
checks (`_persist_bars`), so `DbSource` — and therefore every backtest, compare
and forward run — sees the data exactly as if the HTTP fetch had succeeded.

**Tolerant about shape:** comma/semicolon/tab/whitespace delimiters; `ts|time|
timestamp|date|datetime` and broker-suffixed variants such as `ts_ist`;
`open|o`, `high|h`, `low|l`, `close|c`, `volume|vol|v`; an optional symbol
column; ISO stamps, `YYYY-MM-DD HH:MM:SS`, and epoch seconds or millis.

**Strict about values:** positive prices and OHLC consistency are enforced, so a
bad bar is reported instead of stored. Duplicate timestamps are dropped (last
wins, matching the SQL upsert), because the table's
`UNIQUE(symbol, exchange, timeframe, ts)` would otherwise reject the batch.

What it will *not* do is rename your columns for you. The timestamp column it
chose is printed, and the storage timezone is *detected and reported*, never
assumed.

## 2. Check the alignment before you trust a backtest

```bash
PYTHONPATH=src python scripts/diagnose_timeframe_alignment.py --symbol RELIANCE
```

It answers three questions and prints the raw evidence for each:

* **What timezone are the stored timestamps in?** Verdict is `ist`, `utc` or
  `unknown`, from the first minute of each session (09:15 vs 03:45).
* **Do the derived buckets land where they should?** Per timeframe, the actual
  bucket start times and how many bars each holds. For an IST file the
  `4hour` buckets are `08:00`–`11:45` (165 min) and `12:00`–`15:29`
  (206 min) — window-labelled and clock-aligned, not broken. `10min`, `30min`
  and `1hour` similarly start on clock boundaries, so the first session bucket
  is short.
* **Does a resampled `1day` reproduce a native one?** Only when the symbol also
  has native daily rows; otherwise it says so rather than implying a pass.

## 3. Run the backtest

```bash
BACKTEST_SOURCE=db FORWARD_TEST_DB_URL=sqlite:///tools/out/reliance.db \
    python -m backtest.web.app --port 5003
```

`GET /api/data/coverage` then advertises every timeframe the stored set can
serve (from one 1-minute set: 1min, 5min, 10min, 15min, 30min, 1hour, 4hour,
1day, 1week), and `POST /api/backtest/run` accepts any of them.

`GET /api/data/freshness` reports the staleness chip. It is available on SQLite
as well as PostgreSQL — it used to raise on SQLite (`MAX(ts::date)` is
Postgres cast syntax) and report *"database unreachable"* for a database that
was answering queries fine.

The same cast syntax in the Data tab's coverage probe
(`data_manager._probe_covered_days`) made it return `{}` on SQLite, which is its
documented "probe failed, fetch everything" path — safe, but it meant the
resume/skip feature never once fired on the default profile: every re-run
re-fetched every symbol in full. `_day_expr()` now emits `date(ts)` on SQLite.

## The end date includes the day it names

`to_date`/`end` is a plain date — the UI control is an
`<input type="date">`. It means "up to and including this date".
`DbSource.get_candles` now resolves the window through `window_bounds()` into a
half-open `[start, end + 1 day)`, so the final session is read. A caller that
passes a real timestamp instead of a date still gets an exact instant:

| `end` | Resolved upper bound | Last session read |
|---|---|---|
| `2026-09-30` | `2026-10-01 00:00` (exclusive) | 2026-09-30 |
| `2026-09-30 12:00` | `2026-09-30 12:00` (inclusive) | 2026-09-30, up to noon |

---

## Worked example: RELIANCE, September 2026

`tools/out/reliance_1min_sept2026.csv` — 4,817 bars, 13 sessions, 2 Sep – 30 Sep
2026, naive IST, `source: mstock`. It is kept in the repo so these checks can be
re-run rather than re-argued; `tests/test_real_reliance_september.py` runs the
whole path and is skipped when the file is absent.

Facts worth knowing about this file, all of them benign:

| Observation | Reading |
|---|---|
| Every session is missing 15:16–15:19; six also miss 15:29 | A consistent 4-minute hole, not scattered corruption |
| 126 bars have `o == h == l == c` | The single-price closing auction, all at or after 15:15 |
| 9 September weekdays absent (01, 04, 07, 10, 14, 16, 22, 25, 28) | Holidays — worth confirming against the NSE calendar |
| 370–371 bars/session | vs 375 for a full session, given the hole above |

Resampling the stored minutes reproduces each session exactly (`open` at 09:15,
`close` at 15:28/15:29, true high/low, summed volume) — checked against an
independent pandas aggregation, and pinned in the test file as a literal table
of 13 daily bars. For reference, the first and last:

> Those 13 sessions are what a `1day` backtest over `to_date=2026-09-30` now
> returns. Before the end-date fix it returned 12, and the missing one was the
> 30th — the most recent day, which is the one you are most likely to be
> looking at when you decide the backtest looks wrong.

| Session | Open | High | Low | Close | Volume |
|---|---|---|---|---|---|
| 2026-09-02 | 1298.00 | 1321.80 | 1293.60 | 1313.10 | 26,013,588 |
| 2026-09-30 | 1182.00 | 1196.50 | 1181.70 | 1187.00 | 29,722,953 |

`sma_crossover` at fast=5 / slow=20 over the month, by timeframe:

| Timeframe | Bars | Return | Trades | Max DD |
|---|---|---|---|---|
| 1min | 4817 | −3.37% | 141 | −5.16% |
| 5min | 975 | −3.90% | 29 | −5.36% |
| 10min | 494 | −4.52% | 19 | −5.12% |
| 15min | 325 | −3.57% | 10 | −3.70% |
| 30min | 169 | −1.24% | 3 | −1.52% |
| 1hour | 91 | −0.78% | 1 | −1.56% |
| 4hour | 26 | 0.00% | 0 | 0.00% |
| 1day | 13 | 0.00% | 0 | 0.00% |
| 1week | 5 | 0.00% | 0 | 0.00% |

**The bar counts are the point.** Each one now equals the number of bars the
database actually holds at that granularity (compare with the bucket counts in
step 2). Before the end-date fix in `DbSource.get_candles`, every row here was
short by exactly one session — 4447 / 900 / 456 / 300 / 156 / 84 / 24 / 12 — the
whole of 30 September, silently dropped because `ts BETWEEN :start AND :end`
compared timestamps against midnight on the end date. A single-day run was worse
than lossy: `from_date == to_date` returned *no rows* and failed with "Symbol
not found in database".

The three zero-trade rows are warmup, not a defect: `slow=20` needs 20 bars to
form a first signal and those timeframes only have 26 / 13 / 5. The app logs a
warning for this. With a shorter pair the same `1day` request trades normally —
`fast=2, slow=5` gives 1 trade at −3.34%.

`buy_and_hold` over the same `1day` window returns −9.46% against a benchmark of
−9.60%, entering on 2026-09-03 at 1302.50 and still holding at the final close
of 1187.00. It enters on the *second* bar, not the first: signals are evaluated
on a closed bar and executed on the next one, so the strategy does not get to
buy at the first bar's open.
