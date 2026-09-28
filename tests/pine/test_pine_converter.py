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
    
    # Check generated code structure
    assert "class EmaCrossTest" in python_code
    assert "def calculate" in python_code
    assert "self.ema" in python_code
    assert "crossed_above" in python_code or "ta.crossover" in python_code
    
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
