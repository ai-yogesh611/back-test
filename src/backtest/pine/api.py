"""API endpoints for Pine Script converter.

Provides REST endpoints for converting Pine Script v5 to Python strategies,
validating them, and saving as plugins.
"""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from .converter import PineConversionError, PineScriptConverter
from .parser import PineScriptParser
from .codegen import PineCodeGenerator

pine_bp = Blueprint("pine", __name__, url_prefix="/api/pine")


@pine_bp.route("/convert", methods=["POST"])
def convert_pine_script():
    """Convert Pine Script v5 to Python Strategy code.

    POST /api/pine/convert

    Body:
        pine_code: str (Pine Script v5 code)
        strategy_name: str (optional)

    Returns:
        {
            "success": bool,
            "python_code": str,
            "metadata": {...},
            "strategy_name": str,
            "error": str (if failed)
        }
    """
    data = request.json

    pine_code = data.get("pine_code")
    strategy_name = data.get("strategy_name")

    if not pine_code:
        return jsonify({"success": False, "error": "No Pine Script code provided"}), 400

    # Convert
    parser = PineScriptParser()
    codegen = PineCodeGenerator()
    converter = PineScriptConverter(parser=parser, codegen=codegen)

    try:
        python_code, metadata = converter.convert(pine_code, strategy_name)

        return jsonify(
            {
                "success": True,
                "python_code": python_code,
                "metadata": metadata,
                "strategy_name": metadata["original_name"],
            }
        )

    except PineConversionError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": f"Unexpected error: {e}"}), 500


@pine_bp.route("/validate", methods=["POST"])
def validate_converted_strategy():
    """Validate a converted strategy by running a backtest.

    POST /api/pine/validate

    Body:
        python_code: str
        strategy_name: str
        symbol: str (default: NIFTY)
        days: int (default: 30)

    Returns:
        {
            "is_valid": bool,
            "validation_status": "PASS" | "FAIL",
            "backtest_metrics": {...},
            "rejection_reason": str (if failed)
        }
    """
    from .validator import ConvertedStrategyValidator

    data = request.json

    # TODO: Get backtest engine from app context
    validator = ConvertedStrategyValidator(
        backtest_engine=None,  # Will be set when engine is available
        min_sharpe=0.5,
    )

    is_valid, result = validator.validate(
        strategy_code=data["python_code"],
        strategy_name=data["strategy_name"],
        symbol=data.get("symbol", "NIFTY"),
        days=data.get("days", 30),
    )

    return jsonify({"is_valid": is_valid, **result})


@pine_bp.route("/save", methods=["POST"])
def save_as_plugin():
    """Save a converted strategy as a plugin file.

    POST /api/pine/save

    Body:
        python_code: str
        strategy_name: str
        metadata: dict
        segment: str — required (issues.txt S2). Every saved strategy is
            linked to a capital partition from config/segments.yaml so the
            spawn form preselects it and backtest → paper → live all land in
            the same broker/mode bucket.

    Returns:
        {
            "success": bool,
            "plugin_path": str,
            "strategy_name": str,
            "segment": str
        }
    """
    data = request.json

    from backtest.brokers.segments import get_segments_config

    segment = str(data.get("segment") or "").strip().lower()
    known = set(get_segments_config().segments)
    if not segment:
        return jsonify(
            {
                "success": False,
                "error": "segment is required — pick which capital partition this "
                f"strategy runs in ({', '.join(sorted(known)) or 'configure segments first'})",
            }
        ), 400
    if segment not in known:
        return jsonify(
            {
                "success": False,
                "error": f"unknown segment '{segment}' — configured segments: "
                f"{', '.join(sorted(known)) or 'none'}",
            }
        ), 400

    converter = PineScriptConverter()

    try:
        filepath = converter.save_as_plugin(
            python_code=data["python_code"],
            strategy_name=data["strategy_name"],
            metadata=data["metadata"],
            segment=segment,
        )

        # Hot-load the new plugin into the registry so it appears in the
        # strategy dropdowns without an app restart. Re-saving the same name
        # replaces the previous version: unregister first, then force a rescan
        # so the freshly written file is what gets registered.
        from backtest.plugins import discover_plugins, plugin_strategy_names
        from backtest.strategy.registry import get_strategy, unregister

        # The codegen turns the user's name into a CamelCase class whose
        # registry name is the lowercased concatenation ("Outside Bar
        # Strategy" -> "outsidebarstrategy") — mirror that sanitization.
        safe = re.sub(r"[^a-zA-Z0-9 ]", "", data["strategy_name"])
        expected = "".join(w.capitalize() for w in safe.split()).lower() or "importedstrategy"
        for existing in list(plugin_strategy_names()):
            if existing == expected:
                unregister(existing)
        discover_plugins(force=True)
        load_error = None
        try:
            get_strategy(expected)
        except Exception:  # noqa: BLE001 — not registered → surface it honestly
            load_error = (
                "Plugin file saved, but it failed to load — check the app "
                "log for 'plugin ... skipped'. The generated code may need "
                "manual fixes."
            )

        return jsonify(
            {
                "success": True,
                "plugin_path": str(filepath),
                "strategy_name": data["strategy_name"],
                "segment": segment,
                "loaded": load_error is None,
                "load_error": load_error,
            }
        )

    except PineConversionError as e:
        # Segment injection / generated-code problem — the user can fix this
        # from the builder, so it is a 400 with the reason, not a 500.
        return jsonify({"success": False, "error": str(e)}), 400

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@pine_bp.route("/prompt", methods=["GET"])
def get_llm_prompt():
    """Get the master LLM prompt for manual Pine Script conversion.

    GET /api/pine/prompt

    Returns the master LLM prompt that users can copy and use with
    ChatGPT/Claude for complex strategies that automated conversion
    cannot handle.

    Returns:
        {
            "prompt": str,
            "usage": str
        }
    """

    prompt = """You are a Pine Script v5 to Python strategy converter.

CONTEXT:
The target platform is a Python-based algorithmic trading system.
Strategies extend a base Strategy class with this API:

```python
class Strategy:
    # Indicators (available as self.xxx)
    def ema(self, series, period) -> np.ndarray
    def sma(self, series, period) -> np.ndarray
    def rsi(self, series, period) -> np.ndarray
    def macd(self, series, fast, slow, signal) -> tuple
    def bollinger_bands(self, series, period, std) -> tuple
    def atr(self, high, low, close, period) -> np.ndarray

    # Helper functions
    def crossed_above(a, b) -> bool
    def crossed_below(a, b) -> bool

    # Main method (YOU MUST IMPLEMENT)
    def calculate(self, df: pd.DataFrame) -> Signal:
        # df has columns: open, high, low, close, volume
        # Return Signal(+1) for buy, Signal(-1) for sell, Signal(0) for hold
        pass
```

RULES:
1. Ignore plot(), plotshape(), label.new(), line.new() (no visualization)
2. Convert input() parameters to class __init__ params
3. Convert strategy.entry("buy", strategy.long) → return Signal(+1)
4. Convert strategy.close() → return Signal(0)
5. Pine's "close" variable → df['close'].values
6. Pine's ta.ema(close, 12) → self.ema(df['close'].values, 12)
7. Pine's ta.crossover(a, b) → crossed_above(a, b)
8. Preserve ALL logic and conditions exactly
9. Add docstring explaining the strategy

INPUT (Pine Script v5):
{paste your Pine Script here}

OUTPUT (Python Strategy):
"""

    return jsonify(
        {
            "prompt": prompt,
            "usage": (
                "Copy this prompt + your Pine Script, paste into ChatGPT/Claude, "
                "get Python code back"
            ),
        }
    )
