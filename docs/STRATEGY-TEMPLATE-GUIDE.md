# Strategy Template Guide (2026-09-29)

This guide documents the **mandatory strategy description requirement** and the **Pine Script converter** added on 2026-09-29.

---

## Table of Contents

1. [Strategy Description Requirement](#strategy-description-requirement)
2. [Using the Strategy Template](#using-the-strategy-template)
3. [Backfilling Descriptions for Old Strategies](#backfilling-descriptions-for-old-strategies)
4. [Pine Script Converter](#pine-script-converter)
5. [Database Schema](#database-schema)

---

## Strategy Description Requirement

As of **2026-09-29**, all strategies must define a non-empty `description` field. This improves:

- **Discoverability**: Users can search/filter strategies by description
- **Understanding**: Traders know what a strategy does before running it
- **Documentation**: Auto-generated API docs include descriptions
- **Validation**: The strategy contract now enforces this requirement

### What Happens Without a Description?

```python
class BadStrategy(Strategy):
    name = "bad_strategy"
    # No description defined ❌

# Raises: StrategyContractError: bad_strategy: strategy must define a non-empty 'description'
```

### How to Fix

Add a rich description explaining:
1. **What** the strategy does (core logic)
2. **When** to use it (market regime)
3. **Key parameters** traders should understand

```python
class GoodStrategy(Strategy):
    name = "good_strategy"
    description = (
        "EMA crossover with RSI filter. Buys when fast EMA crosses above slow EMA "
        "and RSI > 50 (momentum confirmation). Best in trending markets with VIX 12-20. "
        "Exit on opposite crossover or RSI < 40."
    )
    # ... rest of strategy
```

---

## Using the Strategy Template

A comprehensive template is provided at `templates/strategy_template.py`. To create a new strategy:

### Step 1: Copy the Template

```bash
cp templates/strategy_template.py src/backtest/strategies/my_strategy.py
```

### Step 2: Fill in Required Fields

Edit the file and replace all `TODO` comments:

```python
class MyNewStrategy(Strategy):
    name = "my_new_strategy"  # ← unique, lowercase
    
    description = (  # ← REQUIRED
        "Clear explanation of what this strategy does and when to use it."
    )
    
    params = {
        "param1": {
            "default": 14,
            "min": 5,
            "max": 50,
            "type": "int",
            "label": "Param 1",
            "tooltip": "Explain this parameter",
        },
    }
    
    def entries(self, candles):
        # Your entry logic here
        pass
```

### Step 3: Test Your Strategy

```bash
cd src
python -m pytest ../tests/strategies/test_my_strategy.py -q
```

The test suite checks:
- ✅ Contract conformance (name, description, params)
- ✅ No lookahead bias
- ✅ Backtest ≡ Forward consistency
- ✅ Performance budget (<20ms per bar)

---

## Backfilling Descriptions for Old Strategies

Older strategies without descriptions need backfilled data. Two approaches:

### Option 1: Automatic Backfill (Quick)

Run the backfill script to add default descriptions:

```bash
python -m src.backtest.pine.backfill_descriptions
```

This adds generic descriptions based on strategy names. You should still review and improve them manually.

### Option 2: Manual Update (Recommended)

Edit each strategy file directly:

```python
# src/backtest/strategies/sma_crossover.py
class SmaCrossover(Strategy):
    name = "sma_crossover"
    description = (  # ← Add this
        "Simple Moving Average crossover. Buys when fast SMA crosses above slow SMA, "
        "sells on crossunder. Works best in sustained trends (VIX 10-18). Whipsaws in choppy markets."
    )
    # ... rest unchanged
```

### Migration to Database

After updating descriptions, populate the `strategy_metadata` table:

```bash
python -m src.backtest.pine.migrate_strategy_metadata
```

This creates persistent records for all strategies with their current metadata.

---

## Pine Script Converter

The platform now includes a **pure code transpiler** for converting Pine Script v5 strategies to Python plugins — **no LLM required**.

### Architecture

```
Pine Script (v5 only)
    ↓
AST Parser (lark/pyparsing)
    ↓
Semantic Analyzer (validate Pine semantics)
    ↓
Code Generator (Pine AST → Python Strategy)
    ↓
Validator (syntax + security + backtest)
    ↓
Plugin Strategy (saved to plugins/strategies/)
```

### Supported Pine Features

✅ Variable declarations (`fast = ta.ema(close, 12)`)
✅ Technical indicators (`ta.ema`, `ta.rsi`, `ta.sma`, `ta.macd`, etc.)
✅ Conditional logic (`if/else`)
✅ Crossover/crossunder detection (`ta.crossover`, `ta.crossunder`)
✅ Strategy entry/exit (`strategy.entry`, `strategy.close`)

❌ Ignored (silently):
- `plot()`, `plotshape()`, `label.new()`, `line.new()` (no visualization)
- `input()` → converted to class `__init__` params
- `request.security()` → multi-timeframe (future feature)
- `alertcondition()`

### API Endpoints

#### Convert Pine Script

```http
POST /api/pine/convert
Content-Type: application/json

{
  "pine_code": "//@version=5\nstrategy(\"EMA Cross\", overlay=true)\n...",
  "strategy_name": "ema_cross_imported"  // optional
}
```

Response:
```json
{
  "success": true,
  "python_code": "class EmaCrossImported(Strategy):\n...",
  "metadata": {
    "original_name": "ema_cross_imported",
    "indicators_used": ["ta.ema"],
    "has_long": true,
    "has_short": false,
    "complexity": "simple"
  },
  "strategy_name": "ema_cross_imported"
}
```

#### Validate Strategy

```http
POST /api/pine/validate
Content-Type: application/json

{
  "python_code": "class EmaCrossImported(Strategy):\n...",
  "strategy_name": "ema_cross_imported",
  "symbol": "NIFTY",
  "days": 30
}
```

Response:
```json
{
  "is_valid": true,
  "validation_status": "PASS",
  "backtest_metrics": {
    "sharpe_ratio": 1.85,
    "max_drawdown": -0.082,
    "total_trades": 12
  }
}
```

#### Save as Plugin

```http
POST /api/pine/save
Content-Type: application/json

{
  "python_code": "class EmaCrossImported(Strategy):\n...",
  "strategy_name": "ema_cross_imported",
  "metadata": {...}
}
```

Response:
```json
{
  "success": true,
  "plugin_path": "plugins/strategies/ema_cross_imported.py",
  "strategy_name": "ema_cross_imported"
}
```

#### Get LLM Prompt (For Complex Strategies)

```http
GET /api/pine/prompt
```

Returns a master prompt users can copy into ChatGPT/Claude for strategies the automated converter cannot handle.

### Usage Example

```python
from backtest.pine import PineScriptConverter

pine_code = '''
//@version=5
strategy("EMA Cross", overlay=true)

fast = ta.ema(close, 12)
slow = ta.ema(close, 26)

if ta.crossover(fast, slow)
    strategy.entry("buy", strategy.long)

if ta.crossunder(fast, slow)
    strategy.close("buy")
'''

converter = PineScriptConverter()
python_code, metadata = converter.convert(pine_code)

print(python_code)  # Generated Python strategy
print(metadata)     # {'indicators_used': ['ta.ema'], ...}

# Save as plugin
filepath = converter.save_as_plugin(python_code, "ema_cross", metadata)
print(f"Saved to: {filepath}")
```

### Configuration

Edit `config/pine_converter.yaml`:

```yaml
pine_converter:
  validation:
    min_sharpe: 0.5           # Minimum Sharpe for validation
    backtest_days: 30         # Validation backtest period
    backtest_symbol: "NIFTY"  # Default symbol
    
  paper_trading:
    required_days: 30         # Paper trade before live
    min_trades: 5             # Minimum trades in paper
    
  supported_indicators:
    - ta.ema
    - ta.sma
    - ta.rsi
    - ta.macd
    - ta.atr
    - ta.bbands
    - ta.stoch
    - ta.adx
    - ta.crossover
    - ta.crossunder
```

---

## Database Schema

### New Table: `strategy_metadata`

Stores rich strategy information beyond class attributes:

```sql
CREATE TABLE strategy_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name VARCHAR(64) NOT NULL UNIQUE,
    description TEXT NOT NULL,
    documentation TEXT,
    author VARCHAR(100),
    version VARCHAR(20),
    signal_kind VARCHAR(20) NOT NULL DEFAULT 'equity',
    regime_vix_low NUMERIC(8, 2),
    regime_vix_high NUMERIC(8, 2),
    eligible_instruments VARCHAR(255),
    tags VARCHAR(255),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    CHECK (signal_kind IN ('equity', 'option')),
    CHECK (regime_vix_low IS NULL OR regime_vix_high IS NULL OR regime_vix_low <= regime_vix_high)
);

CREATE INDEX idx_strategy_metadata_name ON strategy_metadata(strategy_name);
CREATE INDEX idx_strategy_metadata_signal_kind ON strategy_metadata(signal_kind);
```

### Columns Explained

| Column | Type | Description |
|--------|------|-------------|
| `strategy_name` | VARCHAR(64) | Canonical name (matches `Strategy.name`) |
| `description` | TEXT | Rich description (required) |
| `documentation` | TEXT | Long-form Markdown docs |
| `author` | VARCHAR(100) | Strategy author |
| `version` | VARCHAR(20) | Semver version string |
| `signal_kind` | VARCHAR(20) | "equity" or "option" |
| `regime_vix_low` | NUMERIC | Low end of VIX range |
| `regime_vix_high` | NUMERIC | High end of VIX range |
| `eligible_instruments` | VARCHAR(255) | Comma-separated list (e.g., "NIFTY,BANKNIFTY") |
| `tags` | VARCHAR(255) | Searchable tags (e.g., "momentum,trend") |

### Query Examples

Get all strategies with descriptions:
```sql
SELECT strategy_name, description, signal_kind 
FROM strategy_metadata 
ORDER BY strategy_name;
```

Find strategies for a specific regime:
```sql
SELECT strategy_name, description 
FROM strategy_metadata 
WHERE regime_vix_low <= 15 AND regime_vix_high >= 15;
```

List option strategies:
```sql
SELECT strategy_name, eligible_instruments 
FROM strategy_metadata 
WHERE signal_kind = 'option';
```

---

## Checklist for New Strategies

Before committing a new strategy:

- [ ] Copied from `templates/strategy_template.py`
- [ ] Unique `name` (lowercase, no spaces)
- [ ] Rich `description` (what + when + key params)
- [ ] Params have `min`/`max` bounds and `tooltip`
- [ ] Implements `entries()` or `generate_market_view()`
- [ ] Passes conformance tests (`pytest tests/strategies/test_*.py`)
- [ ] Added to `strategy_metadata` table (run migration script)
- [ ] Documented in this guide (if special considerations)

---

## Migration Timeline

- **2026-09-29**: Description requirement enforced for new strategies
- **2026-10-15**: Grace period ends — all strategies must have descriptions
- **2026-10-31**: Old strategies without descriptions hidden from UI (still runnable via API)

Use the backfill scripts now to avoid disruption later.
