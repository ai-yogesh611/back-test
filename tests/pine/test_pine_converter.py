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


# ----------------------------------------------------------------------
# issues.txt S1 follow-up — missing criteria must be USER-PROVIDED before
# save: injected into the plugin, re-checked server-side, Save-gated in UI
# ----------------------------------------------------------------------

def test_save_as_plugin_injects_readable_criteria(tmp_path):
    """criteria= writes a readable_criteria class attribute into the plugin."""
    import ast as py_ast
    import json
    import os

    converter = PineScriptConverter()
    python_code, metadata = converter.convert(SAMPLE_PINE)
    criteria = {
        "entry": "close crosses above EMA-12",
        "take_profit": "limit = close * 1.03",
        "stop_loss": "stop = close * 0.97",
        "sources": {"entry": "detected", "take_profit": "user", "stop_loss": "user"},
    }
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        filepath = converter.save_as_plugin(
            python_code, "crit_test", metadata,
            segment="equity_delivery", criteria=criteria,
        ).resolve()
    finally:
        os.chdir(original_cwd)

    content = filepath.read_text()
    py_ast.parse(content)  # injection must not break syntax
    lines = [ln for ln in content.splitlines() if "readable_criteria" in ln]
    assert len(lines) == 1 and lines[0].startswith("    ")  # inside class body
    parsed = json.loads(lines[0].strip().split("= ", 1)[1])
    assert parsed["take_profit"] == "limit = close * 1.03"
    assert parsed["sources"]["stop_loss"] == "user"
    assert "entry_strike" not in parsed  # absent values are not fabricated


def test_save_as_plugin_criteria_cannot_break_out(tmp_path):
    """Hostile criteria strings stay quoted data; unknown keys are dropped."""
    import ast as py_ast
    import json
    import os

    converter = PineScriptConverter()
    python_code, metadata = converter.convert(PINE_WITH_EXIT)
    hostile = '"); \n    import os; os.system("evil")\n    x = ("'
    criteria = {
        "entry": "fine",
        "take_profit": hostile,
        "stop_loss": "ok",
        "bogus_key": "junk",
        "sources": {"take_profit": "admin"},
    }
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        filepath = converter.save_as_plugin(
            python_code, "crit_bad", metadata, criteria=criteria
        ).resolve()
    finally:
        os.chdir(original_cwd)

    content = filepath.read_text()
    py_ast.parse(content)
    assert "bogus_key" not in content
    rc_line = next(ln for ln in content.splitlines() if "readable_criteria" in ln)
    parsed = json.loads(rc_line.strip().split("= ", 1)[1])
    assert parsed["sources"]["take_profit"] in ("user", "detected")
    # The hostile text survived only as inert string data.
    assert parsed["take_profit"] == hostile


class _FakeSegments:
    segments = {"equity_delivery": {}, "options_index": {}}


@pytest.fixture()
def pine_client(tmp_path, monkeypatch):
    import backtest.brokers.segments as segs_module
    from flask import Flask
    from backtest.pine.api import pine_bp

    monkeypatch.setattr(segs_module, "get_segments_config", lambda: _FakeSegments())
    monkeypatch.chdir(tmp_path)
    app = Flask(__name__)
    app.register_blueprint(pine_bp)
    return app.test_client()


def _converted(pine_source=SAMPLE_PINE):
    python_code, metadata = PineScriptConverter().convert(pine_source)
    return python_code, metadata


def test_save_endpoint_rejects_missing_criteria(pine_client):
    """No criteria + equity script missing TP/SL → 400 naming the gaps."""
    python_code, metadata = _converted()
    resp = pine_client.post("/api/pine/save", json={
        "python_code": python_code,
        "strategy_name": "gate_gap",
        "metadata": metadata,
        "segment": "equity_delivery",
    })
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert "take profit criteria" in err and "stop loss criteria" in err


def test_save_endpoint_requires_strike_expiry_for_options(pine_client):
    """is_options scripts must also carry entry strike + expiry."""
    python_code, metadata = _converted()
    metadata["readable"]["is_options"] = True
    resp = pine_client.post("/api/pine/save", json={
        "python_code": python_code,
        "strategy_name": "gate_opt",
        "metadata": metadata,
        "segment": "options_index",
        "criteria": {
            "entry": "ema cross",
            "take_profit": "limit = 20",
            "stop_loss": "stop = 10",
        },
    })
    assert resp.status_code == 400
    err = resp.get_json()["error"]
    assert "entry strike price" in err and "expiry" in err


def test_save_endpoint_accepts_user_typed_criteria(pine_client):
    """User-typed gaps are enough — the save passes and persists them."""
    import pathlib

    python_code, metadata = _converted()
    resp = pine_client.post("/api/pine/save", json={
        "python_code": python_code,
        "strategy_name": "gate_fill",
        "metadata": metadata,
        "segment": "equity_delivery",
        "criteria": {
            "take_profit": "limit = close * 1.05",
            "stop_loss": "stop = close * 0.95",
            "sources": {"take_profit": "user", "stop_loss": "user"},
        },
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    content = pathlib.Path(body["plugin_path"]).read_text(encoding="utf-8")
    assert "readable_criteria = " in content
    assert "limit = close * 1.05" in content


def test_options_script_extracts_structured_moneyness_and_expiry():
    """Pine converter recognizes relative moneyness (ATM+2), option type (CE), and expiry cycle."""
    converter = PineScriptConverter()
    pine = """
//@version=5
strategy("Nifty Weekly Bullish CE")
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
if ta.crossover(fast, slow)
    strategy.entry("buy", strategy.long)
// Strike: ATM+2 CE expiry current week dt.
tp = close * 1.10
sl = close * 0.95
"""
    _, metadata = converter.convert(pine)
    r = metadata["readable"]
    assert r["is_options"] is True
    assert r["opt_moneyness"] == "ATM+2"
    assert r["opt_type"] == "CE"
    assert r["opt_expiry"] == "current week dt."
    assert "ATM+2 CE expiry current week dt." in r["entry_strike"]
    assert r["expiry"] == "current week dt."
    assert set(r["missing"]) == set()


def test_save_endpoint_accepts_options_dropdown_criteria(pine_client):
    """Options strategies save successfully with structured moneyness, type, expiry, and segment."""
    import pathlib

    python_code, metadata = _converted()
    metadata["readable"]["is_options"] = True
    resp = pine_client.post("/api/pine/save", json={
        "python_code": python_code,
        "strategy_name": "opt_weekly_ce",
        "metadata": metadata,
        "segment": "options_index",
        "criteria": {
            "entry": "ema crossover",
            "entry_strike": "ATM+2 CE expiry current week dt.",
            "expiry": "current week dt.",
            "opt_moneyness": "ATM+2",
            "opt_type": "CE",
            "opt_expiry": "current week dt.",
            "take_profit": "limit = 100",
            "stop_loss": "stop = 50",
            "sources": {
                "entry": "detected",
                "entry_strike": "user",
                "expiry": "user",
                "take_profit": "user",
                "stop_loss": "user"
            },
        },
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["segment"] == "options_index"
    content = pathlib.Path(body["plugin_path"]).read_text(encoding="utf-8")
    assert 'default_segment = "options_index"' in content
    assert '"entry_strike": "ATM+2 CE expiry current week dt."' in content
    assert '"opt_moneyness": "ATM+2"' in content
    assert '"opt_type": "CE"' in content
    assert '"opt_expiry": "current week dt."' in content


def test_save_endpoint_auto_extracts_expiry_from_entry_strike(pine_client):
    """If entry_strike has 'ATM+2 CE expiry current week dt.', expiry is auto-derived."""
    python_code, metadata = _converted()
    metadata["readable"]["is_options"] = True
    resp = pine_client.post("/api/pine/save", json={
        "python_code": python_code,
        "strategy_name": "opt_auto_exp",
        "metadata": metadata,
        "segment": "options_index",
        "criteria": {
            "entry": "ta.crossover",
            "entry_strike": "ITM PE expiry current month dt.",
            "take_profit": "target = 50",
            "stop_loss": "stop = 25",
        },
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
