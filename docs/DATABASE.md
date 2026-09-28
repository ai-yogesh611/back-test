# Database

## Adding a new table (~25 minutes)

**Option A — Alembic/SQLAlchemy (recommended):**
1. Add the model to `src/backtest/db/models.py`:
```python
class MyTable(Base):
    __tablename__ = "my_table"
    id = mapped_column(Integer, primary_key=True)
    name = mapped_column(String(64), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
```
2. Create and apply the migration:
```bash
cd db && alembic revision --autogenerate -m "add my_table"
cd db && alembic upgrade head
```
(See `db/migrations/` for the numbered chain — Postgres + a paired
`.sqlite.sql` mirror for tests. Keep the chain linear: one head.)

**Option B — raw SQL:**
```bash
PGPASSWORD=postgres psql -U postgres -d forward_test -c "CREATE TABLE my_table (...);"
```
then access via `DatabaseManager`:
```python
from backtest.db import DatabaseManager
db = DatabaseManager.from_env()
rows = db.fetch_all("SELECT * FROM my_table")
```

## Stack

- **PostgreSQL 18.4** on `localhost:5432`
- **TimescaleDB 2.29.2** extension for time-series optimization
- **SQLAlchemy 2.0** ORM for forward-test tables
- **Raw SQL** for `market_data_cache` and `instruments` (created via scripts)

## Connection

From `.env`:
```
FORWARD_TEST_DB_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/forward_test
```

## Databases

| Database | Purpose |
|----------|---------|
| `forward_test` | Main app database (all tables below) |
| `algo_trader` | Separate project (not used by this app) |

## Tables

### Time-Series Tables (TimescaleDB Hypertables)

#### `market_data_cache` — OHLCV Candle Data
```sql
CREATE TABLE market_data_cache (
    data_id     BIGSERIAL PRIMARY KEY,
    symbol      VARCHAR(64) NOT NULL,
    exchange    VARCHAR(16) NOT NULL DEFAULT 'NSE',
    timeframe   VARCHAR(8) NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    open        NUMERIC(20,8) NOT NULL,
    high        NUMERIC(20,8) NOT NULL,
    low         NUMERIC(20,8) NOT NULL,
    close       NUMERIC(20,8) NOT NULL,
    volume      NUMERIC(20,4) NOT NULL DEFAULT 0,
    source      VARCHAR(32) NOT NULL DEFAULT 'mstock',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE (symbol, exchange, timeframe, ts)
);
CREATE INDEX ix_mdc_symbol_tf_ts ON market_data_cache (symbol, timeframe, ts DESC);
-- Converted to hypertable: SELECT create_hypertable('market_data_cache', 'ts');
```

**Current data:** 467,151 bars across 201 NIFTY 200 stocks (Jan 2020 – Aug 2026).

#### `equity_curve` — Mark-to-Market Snapshots
```sql
-- Hypertable, partitioned by ts
-- Columns: portfolio_id, ts, equity, cash, positions_value
```

#### `strategy_signals` — Signal Audit Log
```sql
-- Hypertable, partitioned by ts
-- Columns: portfolio_id, symbol, ts, signal_type, confidence
```

#### `system_logs` — Application Logs
```sql
-- Hypertable, partitioned by ts
-- Columns: level, component, message, ts
```

### Relational Tables (SQLAlchemy ORM)

#### `portfolios` — Root Aggregate
```sql
-- Columns: portfolio_id (UUID), name, initial_capital, current_cash, status,
--          mode (paper|live), source (synthetic|replay|mstock), created_at
-- mode/source added by 002_add_mode_source.sql (defaults: paper/synthetic)
```

#### `positions` — Open/Closed Exposure
```sql
-- Columns: position_id, portfolio_id (FK), symbol, side, quantity, avg_entry_price, current_price, status
```

#### `orders` — Order Lifecycle
```sql
-- Columns: order_id, portfolio_id (FK), symbol, side, order_type, quantity, price, status, created_at
-- Statuses: pending, filled, partially_filled, cancelled, rejected
```

#### `fills` — Individual Executions
```sql
-- Columns: fill_id, order_id (FK), quantity, price, commission, timestamp
```

#### `trades` — Matched Round-Trip
```sql
-- Columns: trade_id, portfolio_id (FK), symbol, side, entry_price, exit_price, quantity, pnl, entry_time, exit_time
```

#### `market_data_cache` — Local OHLCV Cache
```sql
-- (described above)
```

#### `performance_metrics` — Daily Rollup
```sql
-- Columns: metric_id, portfolio_id (FK), date, total_value, daily_return, sharpe_rolling, max_drawdown
```

#### `instruments` — mStock Instrument Catalog
```sql
CREATE TABLE instruments (
    instrument_token INTEGER PRIMARY KEY,
    tradingsymbol    VARCHAR(64) NOT NULL,
    exchange         VARCHAR(16) NOT NULL,
    instrument_type  VARCHAR(32),
    segment          VARCHAR(32),
    name             VARCHAR(256),
    last_price       NUMERIC(20,8),
    expiry           DATE,
    strike           NUMERIC(20,8),
    tick_size        NUMERIC(20,8),
    lot_size         INTEGER,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_instruments_symbol ON instruments (tradingsymbol);
CREATE INDEX ix_instruments_exchange ON instruments (exchange);
```

**Current data:** 154,406 instruments across 5 exchanges (NFO, BSE, NSE, CDS, BFO).

#### `system_logs` — Structured Application Logs
```sql
-- (hypertable, described above)
```

## DatabaseManager (`db/manager.py`)

Wraps SQLAlchemy with operational features:

```python
from backtest.db import DatabaseManager

db = DatabaseManager.from_env()
db.connect()

# Raw SQL
rows = db.fetch_all("SELECT * FROM positions WHERE portfolio_id = :pid", {"pid": pid})

# Transaction
with db.transaction() as conn:
    conn.execute(text("UPDATE portfolios SET current_cash = :c"), {"c": 100})

# ORM session
with db.session() as s:
    s.add(Portfolio(name="run-1", initial_capital=100000))

db.disconnect()
```

**Features:**
- Bounded connection pool
- Auto-recovery from DB restart
- Transient fault retries (3 attempts)
- Thread-local transaction scoping
- Health checks

## TimescaleDB Hypertables

Converted tables (partitioned by time):

| Hypertable | Chunks | Purpose |
|-----------|--------|---------|
| `market_data_cache` | 348 | OHLCV data (auto-partitioned) |
| `equity_curve` | 0 | Equity snapshots |
| `strategy_signals` | 0 | Signal audit |
| `system_logs` | 0 | App logs |

**Benefits:**
- Queries on date ranges auto-route to relevant chunks (no full table scan)
- Compression: old chunks can be compressed 90%+
- Continuous aggregates: pre-computed daily/weekly summaries

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/fetch_nifty500_historical.py` | Fetch historical data from mStock → PostgreSQL |
| `tests/test_e2e_nifty_auth_to_db.py` | E2E: auth → NIFTY bars → DB → validate |
| `tests/test_e2e_instruments_to_db.py` | E2E: auth → 154K instruments → DB → validate |
