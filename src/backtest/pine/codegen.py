"""Code generator — converts Pine AST to Python Strategy code.

Emits the platform-correct plugin pattern: a vectorized ``entries()``
(pd.Series of booleans aligned to the candles) which the Strategy base
turns into ``generate_signals()`` via ``_signals_from_entries_exits``.
All Pine ``ta.*`` functions used are emitted as self-contained numpy
helpers, so the generated file has no dependency on indicator methods
that ``Strategy`` does not actually provide.

Known limitations (documented in generated header):
- The platform equity model is long-only; Pine short entries are OR-ed
  into the entries series and flagged in a comment.
- ``plot()``/``label()``/``alertcondition()`` are ignored (no visualization).
- ``request.security()`` (multi-timeframe) is not supported.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional


class PineCodeGenerator:
    """Generate Python Strategy code from Pine AST."""

    #: Pine ta.* function → (python helper name, arg order transform)
    SUPPORTED_FUNCS = {
        "ema": "_ema",
        "sma": "_sma",
        "rsi": "_rsi",
        "atr": None,  # special: _atr(high, low, close, period)
        "supertrend": None,  # special: _supertrend(high, low, close, period, factor)
        "change": "_changed",
        "crossover": "_cross_above",
        "crossunder": "_cross_below",
        "highest": "_highest",
        "lowest": "_lowest",
    }

    # Standard OHLC series names usable as function arguments.
    SERIES_NAMES = {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4"}

    def generate(self, ast: Dict, strategy_name: str) -> str:
        """Generate a complete, loadable plugin module for ``strategy_name``."""
        env = self._collect_env(ast)
        inputs = [s for s in ast.get("statements", []) if s.get("type") == "input"]
        logic_lines = self._generate_logic(ast, env)
        calc_lines = self._generate_indicator_calculations(ast, env)
        helpers = self._required_helpers(ast, env)
        params_block = self._generate_params(inputs)

        class_code = f'''class {strategy_name}(Strategy):
    """
    Auto-generated from Pine Script v5 by the Strategy Builder.

    Entries are OR-ed across all Pine entry conditions; the platform
    equity model is long-only, so short entries are folded into the
    same entries series (flagged below).
    """

    name = "{strategy_name.lower()}"
    description = "Imported from Pine Script v5 via Strategy Builder"
    version = "1.0"
    author = "Pine Converter"

{params_block}

    def entries(self, df: pd.DataFrame) -> pd.Series:
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        n = len(close)

        # Pine inputs → instance params (bound by Strategy.__init__)
{self._generate_param_bindings(inputs)}
        # Indicator calculations
{calc_lines}
        # Entry conditions (long-only platform: shorts folded in)
        long_entries = np.zeros(n, dtype=bool)
        short_entries = np.zeros(n, dtype=bool)
{logic_lines}
        # The base class turns entries/exits into generate_signals().
        return pd.Series(long_entries | short_entries, index=df.index)
'''

        return (
            '"""\n'
            "Auto-generated from Pine Script v5 by the Strategy Builder.\n"
            "\n"
            "Long-only platform: Pine short entries are OR-ed into entries.\n"
            "Plot/draw statements ignored (no visualization).\n"
            '"""\n\n'
            "from backtest.strategy.base import Strategy\n\n"
            "import numpy as np\n"
            "import pandas as pd\n\n\n"
            + helpers
            + "\n\n"
            + class_code
        )

    # ------------------------------------------------------------------
    # Environment: variable → expression, so conditions referencing
    # assigned variables resolve to their underlying expressions.
    # ------------------------------------------------------------------

    def _collect_env(self, ast: Dict) -> Dict[str, str]:
        env: Dict[str, str] = {}
        for st in ast.get("statements", []):
            if st.get("type") == "assignment":
                env[st["name"]] = st["value"]
            elif st.get("type") == "indicator_call":
                env[st["var_name"]] = (
                    f"{st.get('namespace', 'ta')}.{st['function']}("
                    + ", ".join(str(a) for a in st.get("args", []))
                    + ")"
                )
        return env

    # ------------------------------------------------------------------
    # Expression translation
    # ------------------------------------------------------------------

    def _expr(self, expr: str, env: Dict[str, str], depth: int = 0) -> str:
        """Translate one Pine expression (possibly nested calls) to Python."""
        e = expr.strip()
        if depth > 6:  # cycle / runaway guard
            return "False"

        if e in env:
            return self._expr(env[e], env, depth + 1)

        for m in re.finditer(r"(ta|math)\.(\w+)\(([^()]*)\)", e):
            ns, func, args_str = m.group(1), m.group(2), m.group(3)
            args = [a.strip() for a in args_str.split(",") if a.strip()]
            py_args = [self._expr(a, env, depth + 1) for a in args]
            repl = self._map_call(ns, func, py_args)
            if repl is None:
                return "False"  # unsupported function → condition never fires
            e = e[: m.start()] + repl + e[m.end():]

        return e

    def _map_call(self, ns: str, func: str, args: List[str]) -> Optional[str]:
        """Map one ta./math. call to its Python helper, or None if unsupported."""
        if ns == "math":
            if func == "max":
                return f"np.maximum({', '.join(args)})" if len(args) == 2 else None
            if func == "min":
                return f"np.minimum({', '.join(args)})" if len(args) == 2 else None
            if func == "abs":
                return f"np.abs({args[0]})" if args else None
            return None

        if func == "atr" and len(args) >= 1:
            return f"_atr(high, low, close, int({args[-1]}))"
        if func == "supertrend" and len(args) >= 2:
            # Pine returns [supertrend, direction]; we need direction.
            return f"_supertrend(high, low, close, int({args[1]}), float({args[0]}))"
        if func == "crossover" and len(args) == 2:
            return f"_cross_above({args[0]}, {args[1]})"
        if func == "crossunder" and len(args) == 2:
            return f"_cross_below({args[0]}, {args[1]})"
        if func == "change" and len(args) == 1:
            return f"_changed({args[0]})"
        if func == "highest" and len(args) == 2:
            return f"_highest({args[0]}, int({args[1]}))"
        if func == "lowest" and len(args) == 2:
            return f"_lowest({args[0]}, int({args[1]}))"
        helper = self.SUPPORTED_FUNCS.get(func)
        if helper and len(args) >= 2:
            src = self._series_arg(args[0])
            return f"{helper}({src}, int({args[1]}))"
        return None

    def _series_arg(self, arg: str) -> str:
        """Map a Pine series identifier to the local series variable."""
        a = arg.strip()
        if a == "hl2":
            return "(high + low) / 2.0"
        if a == "hlc3":
            return "(high + low + close) / 3.0"
        if a == "ohlc4":
            return "(open + high + low + close) / 4.0"
        if a in self.SERIES_NAMES:
            return a
        return a  # a previously computed variable

    # ------------------------------------------------------------------
    # Logic generation
    # ------------------------------------------------------------------

    def _generate_logic(self, ast: Dict, env: Dict[str, str]) -> str:
        lines: List[str] = []
        for st in ast.get("statements", []):
            if st.get("type") != "if_statement":
                continue
            cond = self._translate_condition(st["condition"], env)
            for inner in st.get("body", []):
                if inner.get("type") == "strategy_call" and inner["function"] == "strategy.entry":
                    if "long" in inner.get("direction", ""):
                        lines.append(f"        long_entries |= {cond}")
                    else:
                        lines.append(
                            f"        short_entries |= {cond}"
                            "  # short folded in (long-only platform)"
                        )
                elif inner.get("type") == "strategy_call" and inner["function"] == "strategy.close":
                    lines.append(f"        # strategy.close under: {cond} (handled by exits model)")
            for inner in st.get("else_body", []):
                if inner.get("type") == "strategy_call" and inner["function"] == "strategy.entry":
                    if "long" in inner.get("direction", ""):
                        lines.append(f"        # else-branch entry skipped: {cond}")
        if not lines:
            lines.append(
                "        # No entry conditions recognised — strategy stays flat."
            )
            lines.append(
                "        # Re-check the Pine script: only `if <condition>:` +"
                " strategy.entry(...) blocks convert."
            )
        return "\n".join(lines)

    def _translate_condition(self, condition: Dict, env: Dict[str, str]) -> str:
        if not isinstance(condition, dict):
            return "False"
        ctype = condition.get("type")

        if ctype == "function_call":
            ns = condition.get("namespace", "ta")
            func = condition.get("function", "")
            args = [self._expr(str(a), env) for a in condition.get("args", [])]
            repl = self._map_call(ns, func, args)
            return repl if repl is not None else "False"

        if ctype == "comparison":
            left = self._expr(str(condition["left"]), env)
            right = self._expr(str(condition["right"]), env)
            return f"{left} {condition['operator']} {right}"

        if ctype == "change_flip":
            var = str(condition.get("variable", ""))
            op = condition.get("operator", "<")
            # Pine: ta.change(direction) < 0 → flipped to bull → long;
            #       ta.change(direction) > 0 → flipped to bear → short.
            src = self._expr(var, env) if var in env else var
            if op == "<":
                return f"_changed({src}) < 0"
            return f"_changed({src}) > 0"

        if ctype == "identifier":
            return self._expr(str(condition.get("name", "")), env)

        return "False"

    # ------------------------------------------------------------------
    # Indicator calculations + inputs → params
    # ------------------------------------------------------------------

    def _generate_indicator_calculations(self, ast: Dict, env: Dict[str, str]) -> str:
        lines: List[str] = []
        stmts = [
            s for s in ast.get("statements", []) if s.get("type") == "indicator_call"
        ]
        # Destructured tuples: the direction element (index 1) must be emitted
        # before the line element (index 0), which references it.
        stmts.sort(
            key=lambda s: 1 if s.get("tuple_index") == 1 else 0, reverse=True
        )  # direction (index 1) first — the line element references it
        for st in stmts:
            var = st["var_name"]
            func = st.get("function", "")
            args = [str(a) for a in st.get("args", [])]
            tuple_len = st.get("tuple_len", 0)
            expr = self._map_call(st.get("namespace", "ta"), func, args)
            if expr is None:
                lines.append(f"        # {var}: ta.{func} not supported — skipped")
                continue
            if tuple_len == 2:
                # [value, direction] = ta.supertrend(...) — the SECOND name gets
                # the direction array; the first gets the supertrend line itself.
                if st.get("tuple_index") == 1:
                    lines.append(f"        {var} = {expr}")
                else:
                    dir_var = self._tuple_partner(ast, st)
                    partner = f"{dir_var}" if dir_var else "None"
                    lines.append(
                        f"        {var} = _supertrend_line(high, low, close, "
                        f"int({args[1] if len(args) > 1 else 10}), "
                        f"float({args[0] if args else 3.0}), {partner})"
                    )
            else:
                lines.append(f"        {var} = {expr}")
        if not lines:
            lines.append("        # No indicators to precompute")
        return "\n".join(lines)

    def _tuple_partner(self, ast: Dict, st: Dict) -> Optional[str]:
        """For a destructured tuple element, find the sibling variable name."""
        idx = st.get("tuple_index")
        if idx is None:
            return None
        for other in ast.get("statements", []):
            if (
                other.get("type") == "indicator_call"
                and other.get("tuple_index") == (1 if idx == 0 else 0)
                and other.get("tuple_len") == st.get("tuple_len")
                and other.get("args") == st.get("args")
            ):
                return other["var_name"]
        return None

    def _generate_params(self, inputs: List[Dict]) -> str:
        if not inputs:
            return "    params: dict = {}  # Pine script declared no input() parameters"
        lines = ["    params = {"]
        for p in inputs:
            default = p.get("default", 0)
            # NB: statement dicts carry their own "type" key ("input"), so the
            # param type is inferred from the default value instead.
            if isinstance(default, float):
                ptype = "float"
            elif isinstance(default, int):
                ptype = "int"
            else:
                ptype = "str"
            lines.append(f'        "{p["name"]}": {{')
            lines.append(f'            "default": {default!r},')
            lines.append(f'            "type": "{ptype}",')
            label = p.get("label", p["name"]).replace("_", " ").title()
            lines.append(f'            "label": "{label}",')
            lines.append('            "tooltip": "Pine Script input()",')
            lines.append("        },")
        lines.append("    }")
        return "\n".join(lines)

    def _generate_param_bindings(self, inputs: List[Dict]) -> str:
        if not inputs:
            return ""
        lines = [
            f"        {p['name']} = self.{p['name']}"  # Pine input → param
            for p in inputs
        ]
        return "\n".join(lines) + "\n" if lines else ""

    # ------------------------------------------------------------------
    # Helpers: only emit what the translated code actually references
    # ------------------------------------------------------------------

    def _required_helpers(self, ast: Dict, env: Dict[str, str]) -> str:
        all_exprs = list(env.values())
        for st in ast.get("statements", []):
            if st.get("type") == "if_statement":
                all_exprs.append(str(st["condition"]))
        blob = " ".join(all_exprs)

        needed = set()
        for func, helper in self.SUPPORTED_FUNCS.items():
            if helper and re.search(rf"\b{helper}\(", helper) and False:
                pass
        for func in ("ema", "sma", "rsi", "highest", "lowest"):
            if re.search(rf"ta\.{func}\(", blob):
                needed.add(self.SUPPORTED_FUNCS[func])
        if re.search(r"ta\.atr\(", blob):
            needed.add("_atr")
        if re.search(r"ta\.supertrend\(", blob):
            needed.add("_supertrend")
        if re.search(r"ta\.change\(", blob) or "'change_flip'" in blob:
            needed.add("_changed")
        if re.search(r"ta\.crossover\(", blob):
            needed.add("_cross_above")
        if re.search(r"ta\.crossunder\(", blob):
            needed.add("_cross_below")
        if re.search(r"ta\.atr\(", blob) or re.search(r"ta\.supertrend\(", blob):
            needed.add("_tr_atr")

        helpers = []
        if "_ema" in needed:
            helpers.append(
                "def _ema(src, period):\n"
                "    src = np.asarray(src, dtype=float)\n"
                "    out = np.empty_like(src)\n"
                "    alpha = 2.0 / (period + 1.0)\n"
                "    out[0] = src[0]\n"
                "    for i in range(1, len(src)):\n"
                "        out[i] = alpha * src[i] + (1.0 - alpha) * out[i - 1]\n"
                "    return out"
            )
        if "_sma" in needed:
            helpers.append(
                "def _sma(src, period):\n"
                "    src = np.asarray(src, dtype=float)\n"
                "    out = np.full(len(src), np.nan)\n"
                "    if len(src) >= period:\n"
                "        out[period - 1:] = np.convolve(\n"
                "            src, np.ones(period) / period, mode=\"valid\")\n"
                "    return out"
            )
        if "_rsi" in needed:
            helpers.append(
                "def _rsi(src, period):\n"
                "    src = np.asarray(src, dtype=float)\n"
                "    delta = np.diff(src, prepend=src[0])\n"
                "    gain = np.where(delta > 0, delta, 0.0)\n"
                "    loss = np.where(delta < 0, -delta, 0.0)\n"
                "    avg_gain = _ema(gain, period)\n"
                "    avg_loss = _ema(loss, period)\n"
                "    rs = np.divide(avg_gain, avg_loss, out=np.full(len(src), 100.0),\n"
                "                  where=avg_loss != 0)\n"
                "    return 100.0 - 100.0 / (1.0 + rs)"
            )
        if "_tr_atr" in needed:
            helpers.append(
                "def _tr_atr(high, low, close, period):\n"
                "    n = len(close)\n"
                "    tr = np.empty(n)\n"
                "    tr[0] = high[0] - low[0]\n"
                "    for i in range(1, n):\n"
                "        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]),\n"
                "                 abs(low[i] - close[i - 1]))\n"
                "    atr = np.empty(n)\n"
                "    atr[0] = tr[0]\n"
                "    for i in range(1, n):\n"
                "        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period\n"
                "    return atr"
            )
        if "_atr" in needed:
            helpers.append(
                "def _atr(high, low, close, period):\n"
                "    return _tr_atr(high, low, close, period)"
            )
        if "_supertrend" in needed:
            needed.add("_supertrend_line")
            helpers.append(
                "def _supertrend(high, low, close, period, factor):\n"
                "    \"\"\"Pine ta.supertrend: +1 bull, -1 bear.\"\"\"\n"
                "    n = len(close)\n"
                "    atr = _tr_atr(high, low, close, period)\n"
                "    hl2 = (high + low) / 2.0\n"
                "    upper = hl2 + factor * atr\n"
                "    lower = hl2 - factor * atr\n"
                "    direction = np.ones(n, dtype=int)\n"
                "    for i in range(1, n):\n"
                "        if not (upper[i] < upper[i - 1] or close[i - 1] > upper[i - 1]):\n"
                "            upper[i] = upper[i - 1]\n"
                "        if not (lower[i] > lower[i - 1] or close[i - 1] < lower[i - 1]):\n"
                "            lower[i] = lower[i - 1]\n"
                "        if direction[i - 1] == 1:\n"
                "            direction[i] = -1 if close[i] < lower[i] else 1\n"
                "        else:\n"
                "            direction[i] = 1 if close[i] > upper[i] else -1\n"
                "    return direction"
            )
            helpers.append(
                "def _supertrend_line(high, low, close, period, factor, direction):\n"
                "    \"\"\"The supertrend price line, given a direction array.\"\"\"\n"
                "    atr = _tr_atr(high, low, close, period)\n"
                "    hl2 = (high + low) / 2.0\n"
                "    return np.where(direction == 1, hl2 - factor * atr, hl2 + factor * atr)"
            )
        if "_changed" in needed:
            helpers.append(
                "def _changed(arr):\n"
                "    arr = np.asarray(arr)\n"
                "    out = np.zeros(len(arr), dtype=arr.dtype)\n"
                "    out[1:] = arr[1:] - arr[:-1]\n"
                "    return out"
            )
        if "_cross_above" in needed:
            helpers.append(
                "def _cross_above(a, b):\n"
                "    a = np.asarray(a, dtype=float)\n"
                "    b = np.asarray(b, dtype=float)\n"
                "    out = np.zeros(len(a), dtype=bool)\n"
                "    out[1:] = (a[:-1] <= b[:-1]) & (a[1:] > b[1:])\n"
                "    return out"
            )
        if "_cross_below" in needed:
            helpers.append(
                "def _cross_below(a, b):\n"
                "    a = np.asarray(a, dtype=float)\n"
                "    b = np.asarray(b, dtype=float)\n"
                "    out = np.zeros(len(a), dtype=bool)\n"
                "    out[1:] = (a[:-1] >= b[:-1]) & (a[1:] < b[1:])\n"
                "    return out"
            )
        if "_highest" in needed:
            helpers.append(
                "def _highest(src, period):\n"
                "    return pd.Series(src).rolling(period).max().values"
            )
        if "_lowest" in needed:
            helpers.append(
                "def _lowest(src, period):\n"
                "    return pd.Series(src).rolling(period).min().values"
            )

        return "\n\n\n".join(helpers)
