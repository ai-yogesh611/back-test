"""Code generator — converts Pine AST to Python Strategy code.

Maps Pine Script functions to platform Strategy API calls, generates
the class structure with __init__, calculate(), and proper imports.
"""

from __future__ import annotations

from typing import Any, Dict, List


class PineCodeGenerator:
    """Generate Python Strategy code from Pine AST."""

    INDICATOR_MAP = {
        # Pine function → Platform method
        "ta.ema": "self.ema",
        "ta.sma": "self.sma",
        "ta.rsi": "self.rsi",
        "ta.macd": "self.macd",
        "ta.atr": "self.atr",
        "ta.bbands": "self.bollinger_bands",
        "ta.stoch": "self.stochastic",
        "ta.adx": "self.adx",
        "ta.crossover": "crossed_above",
        "ta.crossunder": "crossed_below",
        "math.max": "max",
        "math.min": "min",
    }

    def generate(self, ast: Dict, strategy_name: str) -> str:
        """Generate Python strategy code from AST.

        Args:
            ast: Parsed Pine Script AST
            strategy_name: Python class name for the strategy

        Returns:
            Complete Python strategy source code as string
        """
        # Extract metadata
        indicators_used = self._extract_indicators(ast)
        config_params = self._extract_input_params(ast)

        # Generate class
        code = f'''"""Auto-generated from Pine Script v5.

Original strategy logic preserved.
Plot/draw statements ignored (no visualization).
"""

from backtest.strategy.base import Strategy
from backtest.strategy.signal import Signal
import pandas as pd
import numpy as np


def crossed_above(a, b):
    """Helper: returns True where a crosses above b."""
    if len(a) < 2 or len(b) < 2:
        return False
    return a[-2] <= b[-2] and a[-1] > b[-1]


def crossed_below(a, b):
    """Helper: returns True where a crosses below b."""
    if len(a) < 2 or len(b) < 2:
        return False
    return a[-2] >= b[-2] and a[-1] < b[-1]


class {strategy_name}(Strategy):
    """
    Auto-generated from Pine Script v5.

    Original strategy logic preserved.
    Plot/draw statements ignored (no visualization).
    """

    name = "{strategy_name.lower()}"
    description = "Auto-generated from Pine Script v5"
    version = "1.0"
    author = "Pine Converter"

    params = {{
{self._generate_init_params(config_params)}
    }}

    def calculate(self, df: pd.DataFrame) -> Signal:
        """
        Main strategy logic.
        Returns Signal(+1, -1, or 0) based on Pine Script conditions.
        """
        # Extract OHLCV
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        volume = df['volume'].values if 'volume' in df.columns else None

        # Calculate indicators
{self._generate_indicator_calculations(indicators_used, ast)}

        # Strategy logic
{self._generate_logic(ast)}

        return Signal(0)  # Default: no action
'''

        return code

    def _generate_init_params(self, config_params: List[Dict]) -> str:
        """Generate __init__ parameters from Pine input() calls."""
        if not config_params:
            return "        # No configurable parameters"

        lines = []
        for param in config_params:
            name = param["name"]
            default = param.get("default", 0)
            ptype = param.get("type", "float")
            lines.append(f'        "{name}": {{')
            lines.append(f'            "default": {default},')
            lines.append(f'            "type": "{ptype}",')
            lines.append(f'            "label": "{name.replace("_", " ").title()}",')
            lines.append(f'            "tooltip": "Parameter from Pine Script",')
            lines.append(f"        }},")
        return "\n".join(lines)

    def _generate_indicator_calculations(
        self, indicators: List[Dict], ast: Dict
    ) -> str:
        """Generate indicator calculation code."""
        lines = []

        for indicator in indicators:
            func_key = f"{indicator.get('namespace', 'ta')}.{indicator['function']}"
            var_name = indicator.get("var_name", f"_{indicator['function']}")
            args = indicator.get("args", [])

            if func_key == "ta.ema" and len(args) >= 2:
                period = args[1] if isinstance(args[1], (int, float)) else 14
                lines.append(f"        {var_name} = self.ema(close, {period})")

            elif func_key == "ta.sma" and len(args) >= 2:
                period = args[1] if isinstance(args[1], (int, float)) else 14
                lines.append(f"        {var_name} = self.sma(close, {period})")

            elif func_key == "ta.rsi" and len(args) >= 2:
                period = args[1] if isinstance(args[1], (int, float)) else 14
                lines.append(f"        {var_name} = self.rsi(close, {period})")

            elif func_key == "ta.macd" and len(args) >= 3:
                fast = args[1] if isinstance(args[1], (int, float)) else 12
                slow = args[2] if isinstance(args[2], (int, float)) else 26
                signal_period = args[3] if len(args) > 3 and isinstance(args[3], (int, float)) else 9
                lines.append(
                    f"        {var_name}_macd, {var_name}_signal, {var_name}_hist = self.macd(close, {fast}, {slow}, {signal_period})"
                )

            elif func_key == "ta.atr" and len(args) >= 4:
                period = args[3] if isinstance(args[3], (int, float)) else 14
                lines.append(f"        {var_name} = self.atr(high, low, close, {period})")

            elif func_key == "ta.bbands" and len(args) >= 3:
                period = args[1] if isinstance(args[1], (int, float)) else 20
                std = args[2] if isinstance(args[2], (int, float)) else 2
                lines.append(
                    f"        {var_name}_upper, {var_name}_middle, {var_name}_lower = self.bollinger_bands(close, {period}, {std})"
                )

            elif func_key in ("ta.crossover", "ta.crossunder"):
                # These are handled in condition translation, not as standalone calculations
                pass

        if not lines:
            lines.append("        # No indicators to calculate")

        return "\n".join(lines)

    def _generate_logic(self, ast: Dict) -> str:
        """Generate conditional logic (if/else)."""
        lines = []

        for statement in ast.get("statements", []):
            if statement["type"] == "if_statement":
                condition = self._translate_condition(statement["condition"])
                lines.append(f"        if {condition}:")

                # Check for strategy.entry() in if block
                for inner_stmt in statement.get("body", []):
                    if inner_stmt["type"] == "strategy_call":
                        if inner_stmt["function"] == "strategy.entry":
                            direction = inner_stmt.get("direction", "")
                            signal = "+1" if "long" in direction else "-1"
                            lines.append(f"            return Signal({signal})")

                        elif inner_stmt["function"] == "strategy.close":
                            lines.append(f"            return Signal(0)")

                # Handle else block
                if statement.get("else_body"):
                    lines.append("        else:")
                    for inner_stmt in statement["else_body"]:
                        if inner_stmt["type"] == "strategy_call":
                            if inner_stmt["function"] == "strategy.entry":
                                direction = inner_stmt.get("direction", "")
                                signal = "+1" if "long" in direction else "-1"
                                lines.append(f"            return Signal({signal})")
                            elif inner_stmt["function"] == "strategy.close":
                                lines.append(f"            return Signal(0)")

        if not lines:
            lines.append("        # No entry/exit logic found")
            lines.append("        # TODO: Implement your strategy logic here")

        return "\n".join(lines)

    def _translate_condition(self, condition: Dict) -> str:
        """Translate Pine condition to Python."""
        if not isinstance(condition, dict):
            return "False"

        if condition["type"] == "function_call":
            func = condition["function"]
            namespace = condition.get("namespace", "ta")
            full_func = f"{namespace}.{func}"
            args = condition.get("args", [])

            if full_func == "ta.crossover" and len(args) >= 2:
                arg0 = self._format_arg(args[0])
                arg1 = self._format_arg(args[1])
                return f"crossed_above({arg0}, {arg1})"
            elif full_func == "ta.crossunder" and len(args) >= 2:
                arg0 = self._format_arg(args[0])
                arg1 = self._format_arg(args[1])
                return f"crossed_below({arg0}, {arg1})"

        elif condition["type"] == "comparison":
            left = self._format_arg(condition["left"])
            op = condition["operator"]
            right = self._format_arg(condition["right"])
            return f"{left} {op} {right}"

        return "False"  # Fallback

    def _format_arg(self, arg: Any) -> str:
        """Format an argument for code generation."""
        if isinstance(arg, dict):
            if arg["type"] == "function_call":
                func = arg["function"]
                namespace = arg.get("namespace", "ta")
                full_func = f"{namespace}.{func}"
                args = ", ".join(self._format_arg(a) for a in arg.get("args", []))
                mapped = self.INDICATOR_MAP.get(full_func, func)
                return f"{mapped}({args})"
            elif arg["type"] == "identifier":
                return str(arg.get("name", "unknown"))
        elif isinstance(arg, (int, float)):
            return str(arg)
        elif isinstance(arg, str):
            # Could be a variable name or quoted string
            if arg.startswith(("'", '"')) and arg.endswith(("'", '"')):
                return arg  # Keep as string literal
            return arg  # Treat as variable name
        return str(arg)

    def _extract_indicators(self, ast: Dict) -> List[Dict]:
        """Extract all indicator function calls from AST."""
        indicators = []
        for statement in ast.get("statements", []):
            if statement["type"] == "function_call" or (
                statement.get("type") == "indicator_call"
            ):
                indicators.append(statement)
        return indicators

    def _extract_input_params(self, ast: Dict) -> List[Dict]:
        """Extract input() parameters (not yet implemented, placeholder)."""
        # TODO: Parse input() calls from Pine Script
        return []
