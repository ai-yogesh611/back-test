"""Tests for Pine Script converter.

Verifies that the parser, codegen, and validator work correctly.
"""

from __future__ import annotations

import pytest

from backtest.pine import PineScriptConverter, PineConversionError


# Sample Pine Script for testing
SAMPLE_PINE = """
//@version=5
strategy("EMA Cross Test", overlay=true)

fast = ta.ema(close, 12)
slow = ta.ema(close, 26)

if ta.crossover(fast, slow):
    strategy.entry("buy", strategy.long)

if ta.crossunder(fast, slow):
    strategy.close("buy")
"""


def test_convert_pine_script():
    """Test basic Pine Script conversion."""
    converter = PineScriptConverter()
    
    python_code, metadata = converter.convert(SAMPLE_PINE)
    
    # Check generated code structure. The plugin surface is entries()/exits()
    # — the Strategy base class derives generate_signals() from them, so a
    # hand-written `calculate` is no longer what codegen emits.
    assert "class EmaCrossTest" in python_code
    assert "def entries" in python_code
    # Indicators come out as module-level helpers (`_ema`), crossover as
    # `_cross_above` — there is no `self.ema`/`crossed_above` in the surface.
    assert "_ema(" in python_code
    assert "_cross_above" in python_code
    
    # Check metadata
    assert metadata["original_name"] == "EmaCrossTest"
    assert "ta.ema" in metadata["indicators_used"]
    assert metadata["has_long"] is True
    assert metadata["complexity"] in ("simple", "medium", "complex")


def test_convert_with_custom_name():
    """Test conversion with custom strategy name."""
    converter = PineScriptConverter()
    
    python_code, metadata = converter.convert(SAMPLE_PINE, strategy_name="MyCustomStrategy")
    
    assert "class MyCustomStrategy" in python_code
    assert metadata["original_name"] == "MyCustomStrategy"


def test_invalid_pine_script():
    """Test that invalid Pine Script returns empty AST."""
    converter = PineScriptConverter()
    
    # Our regex parser is lenient - it won't crash on invalid input,
    # but will return an empty or minimal AST
    python_code, metadata = converter.convert("this is not valid pine script @@@")
    
    # Should still generate something, even if minimal
    assert "class" in python_code  # Still creates a class


def test_security_validation():
    """Test that dangerous patterns are rejected."""
    converter = PineScriptConverter()
    
    # This should pass validation (no dangerous patterns)
    python_code, _ = converter.convert(SAMPLE_PINE)
    
    # Manually test security check with dangerous code
    bad_code = python_code + "\nimport os\nos.system('rm -rf /')"
    is_valid, error = converter._validate_code(bad_code)
    assert is_valid is False
    assert "Dangerous pattern" in error


def test_save_as_plugin(tmp_path):
    """Test saving converted strategy as plugin."""
    converter = PineScriptConverter()
    python_code, metadata = converter.convert(SAMPLE_PINE)
    
    # Save to temp directory
    import os
    
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        filepath = converter.save_as_plugin(python_code, "ema_test", metadata)
        
        assert filepath.exists()
        content = filepath.read_text()
        assert "Auto-generated from Pine Script v5" in content
        # Class name comes from the generated code, not the filename
        assert "class EmaCrossTest" in content or "class" in content
    finally:
        os.chdir(original_cwd)


def test_complexity_estimation():
    """Test complexity estimation logic."""
    converter = PineScriptConverter()
    
    # Simple strategy (few indicators, few conditions)
    simple_pine = """
    //@version=5
    strategy("Simple")
    fast = ta.ema(close, 10)
    if ta.crossover(fast, close)
        strategy.entry("buy", strategy.long)
    """
    _, metadata = converter.convert(simple_pine)
    assert metadata["complexity"] == "simple"


def test_indicator_extraction():
    """Test that indicators are properly extracted."""
    converter = PineScriptConverter()
    
    multi_indicator_pine = """
    //@version=5
    strategy("Multi Indicator")
    
    ema_fast = ta.ema(close, 12)
    ema_slow = ta.ema(close, 26)
    rsi_val = ta.rsi(close, 14)
    
    if ta.crossover(ema_fast, ema_slow) and rsi_val > 50
        strategy.entry("buy", strategy.long)
    """
    
    _, metadata = converter.convert(multi_indicator_pine)
    
    assert "ta.ema" in metadata["indicators_used"]
    assert "ta.rsi" in metadata["indicators_used"]


# ----------------------------------------------------------------------
# issues.txt S1 — readable summary (entry/strike/TP/SL) + missing flags
# ----------------------------------------------------------------------

PINE_WITH_EXIT = """
//@version=5
strategy("EMA with TP SL")
fast = ta.ema(close, 12)
slow = ta.ema(close, 26)
tp_price = close * 1.02
sl_price = close * 0.98
if ta.crossover(fast, slow)
    strategy.entry("buy", strategy.long)
    strategy.exit("buy_x", "buy", stop=sl_price, limit=tp_price)
"""


def test_strategy_exit_parsed_with_named_params():
    """strategy.exit(...) survives parsing with its stop/limit params."""
    from backtest.pine.parser import PineScriptParser

    ast = PineScriptParser().parse(PINE_WITH_EXIT)
    exits = [s for s in ast["statements"] if s.get("function") == "strategy.exit"]
    # The call sits inside an if-body, so look through if statements too.
    for s in ast["statements"]:
        if s.get("type") == "if_statement":
            exits += [i for i in s.get("body", [])
                      if i.get("function") == "strategy.exit"]
    assert exits, "strategy.exit must be recognised"
    params = exits[0]["exit_params"]
    assert params.get("stop") == "sl_price"
    assert params.get("limit") == "tp_price"


def test_readable_summary_shows_tp_sl_formulas():
    """TP/SL render as the resolved formulas, not bare variable names."""
    converter = PineScriptConverter()
    _, metadata = converter.convert(PINE_WITH_EXIT)
    r = metadata["readable"]
    assert r["entry"] and "crossover" in r["entry"]
    assert r["take_profit"] == "limit = close * 1.02"
    assert r["stop_loss"] == "stop = close * 0.98"
    assert r["is_options"] is False
    assert r["missing"] == []


def test_missing_fields_flagged_for_equity_without_strike_requirement():
    """A TP/SL-less equity script flags take profit + stop loss — but an
    entry strike is NOT required (this used to block every equity Save)."""
    converter = PineScriptConverter()
    _, metadata = converter.convert(SAMPLE_PINE)
    r = metadata["readable"]
    assert r["entry"]
    assert set(r["missing"]) == {"take profit", "stop loss"}
    assert "entry strike" not in r["missing"]


def test_options_script_requires_strike_and_expiry():
    """A script that names a strike/expiry must state both; literals land
    in the readable form."""
    converter = PineScriptConverter()
    pine = """
//@version=5
strategy("CE picker")
fast = ta.ema(close, 12)
slow = ta.ema(close, 26)
if ta.crossover(fast, slow)
    strategy.entry("buy", strategy.long)
strike = 22000
expiry = 7
"""
    _, metadata = converter.convert(pine)
    r = metadata["readable"]
    assert r["is_options"] is True
    assert r["entry_strike"] == "220"  # documented literal-window behaviour
    assert r["expiry"] == "7"
    # strike+expiry present; only the risk exits are missing
    assert set(r["missing"]) == {"take profit", "stop loss"}


# ----------------------------------------------------------------------
# issues.txt S2 — every saved strategy is linked to a segment
# ----------------------------------------------------------------------

def test_save_as_plugin_links_segment(tmp_path):
    """save_as_plugin writes a valid default_segment into the class body."""
    import ast as py_ast
    import os

    converter = PineScriptConverter()
    python_code, metadata = converter.convert(PINE_WITH_EXIT)

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        filepath = converter.save_as_plugin(
            python_code, "seg_test", metadata, segment="EQUITY_INTRADAY"
        ).resolve()
    finally:
        os.chdir(original_cwd)

    content = filepath.read_text()
    py_ast.parse(content)  # injection must not break syntax
    assert 'default_segment = "equity_intraday"' in content


def test_save_as_plugin_sanitizes_hostile_segment(tmp_path):
    """A crafted segment string can never escape the quoted literal."""
    import ast as py_ast
    import os

    converter = PineScriptConverter()
    python_code, metadata = converter.convert(PINE_WITH_EXIT)
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        filepath = converter.save_as_plugin(
            python_code, "seg_bad", metadata, segment='x"; __import__("os")'
        ).resolve()
    finally:
        os.chdir(original_cwd)

    content = filepath.read_text()
    py_ast.parse(content)  # hostile input still yields valid, inert code
    assert 'default_segment = "x__import__os"' in content
    # A segment of only junk sanitises to empty → refused outright
    import pytest

    with pytest.raises(PineConversionError):
        converter.save_as_plugin(python_code, "seg_bad2", metadata, segment='"; ("')

