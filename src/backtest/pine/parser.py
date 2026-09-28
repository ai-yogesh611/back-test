"""Pine Script v5 parser — converts Pine code to AST using regex.

Simplified regex-based parser (no Lark dependency) for the subset of Pine
Script v5 needed for strategy conversion. More robust and easier to debug
than a full grammar-based parser.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


class PineScriptParser:
    """Parse Pine Script v5 to AST using regex patterns.

    Supports:
    - Variable declarations
    - Technical indicators (ta.ema, ta.rsi, ta.sma, ta.macd, etc.)
    - Conditional logic (if/else)
    - Crossover/crossunder detection
    - Strategy entry/exit

    Does NOT support (silently ignored):
    - plot(), plotshape(), label.new(), line.new()
    - input() → converted to class __init__ params
    - request.security() → multi-timeframe (future)
    - alertcondition()
    """

    def parse(self, pine_code: str) -> Dict[str, Any]:
        """Parse Pine Script to AST.

        Args:
            pine_code: Raw Pine Script v5 source code

        Returns:
            Dictionary representing the AST with keys:
            - type: "script"
            - statements: list of statement dicts
        """
        statements = []

        # Keep original lines with their indentation for proper parsing
        original_lines = pine_code.split('\n')

        i = 0
        while i < len(original_lines):
            line = original_lines[i]
            stripped = line.split('//')[0].strip()  # Remove comments

            # Skip empty lines and version declaration
            if not stripped or stripped.startswith('//@version'):
                i += 1
                continue

            # Parse declaration: fast = ta.ema(close, 12)
            decl_match = re.match(r'(\w+)\s*=\s*(.+)', stripped)
            if decl_match:
                var_name = decl_match.group(1)
                expr = decl_match.group(2).strip()

                # Check if it's a function call
                func_match = re.match(r'(ta|math)\.(\w+)\((.*)\)', expr)
                if func_match:
                    namespace = func_match.group(1)
                    func_name = func_match.group(2)
                    args_str = func_match.group(3)
                    args = self._parse_args(args_str)

                    statements.append({
                        "type": "indicator_call",
                        "var_name": var_name,
                        "namespace": namespace,
                        "function": func_name,
                        "args": args,
                    })
                else:
                    # Simple assignment
                    statements.append({
                        "type": "assignment",
                        "name": var_name,
                        "value": expr,
                    })

                i += 1
                continue

            # Parse if statement
            if_match = re.match(r'if\s+(.+):', stripped)
            if if_match:
                condition_str = if_match.group(1).strip()
                condition = self._parse_condition(condition_str)

                # Parse body (indented lines)
                body = []
                i += 1
                while i < len(original_lines):
                    next_line = original_lines[i]
                    # Check if line is indented (part of if body)
                    if next_line and (next_line[0] == ' ' or next_line[0] == '\t'):
                        body_stripped = next_line.split('//')[0].strip()
                        if body_stripped:  # Skip empty/commented lines in body
                            body_stmt = self._parse_statement(body_stripped)
                            if body_stmt:
                                body.append(body_stmt)
                        i += 1
                    else:
                        break

                # Check for else
                else_body = []
                if i < len(original_lines):
                    else_stripped = original_lines[i].split('//')[0].strip()
                    if else_stripped.startswith('else:'):
                        i += 1
                        while i < len(original_lines):
                            next_line = original_lines[i]
                            if next_line and (next_line[0] == ' ' or next_line[0] == '\t'):
                                else_stripped = next_line.split('//')[0].strip()
                                if else_stripped:
                                    else_stmt = self._parse_statement(else_stripped)
                                    if else_stmt:
                                        else_body.append(else_stmt)
                                i += 1
                            else:
                                break

                statements.append({
                    "type": "if_statement",
                    "condition": condition,
                    "body": body,
                    "else_body": else_body,
                })
                continue

            # Parse strategy call (standalone, not in if body)
            strategy_match = re.match(r'strategy\.(entry|close)\((.*)\)', stripped)
            if strategy_match:
                func = strategy_match.group(1)
                args_str = strategy_match.group(2)

                if func == 'entry':
                    # strategy.entry("buy", strategy.long)
                    parts = [p.strip() for p in args_str.split(',')]
                    name = parts[0].strip('"\'')
                    direction = parts[1] if len(parts) > 1 else "strategy.long"

                    statements.append({
                        "type": "strategy_call",
                        "function": "strategy.entry",
                        "name": name,
                        "direction": direction,
                    })
                elif func == 'close':
                    # strategy.close("buy")
                    name = args_str.strip('"\'')

                    statements.append({
                        "type": "strategy_call",
                        "function": "strategy.close",
                        "name": name,
                    })

                i += 1
                continue

            i += 1

        return {"type": "script", "statements": statements}

    def _parse_args(self, args_str: str) -> List[Any]:
        """Parse function arguments."""
        args = []
        for arg in args_str.split(','):
            arg = arg.strip()
            if not arg:
                continue

            # Try to parse as number
            try:
                if '.' in arg:
                    args.append(float(arg))
                else:
                    args.append(int(arg))
                continue
            except ValueError:
                pass

            # String literal
            if (arg.startswith('"') and arg.endswith('"')) or \
               (arg.startswith("'") and arg.endswith("'")):
                args.append(arg)
                continue

            # Variable name or keyword
            args.append(arg)

        return args

    def _parse_condition(self, condition_str: str) -> Dict[str, Any]:
        """Parse a condition expression."""
        # Check for ta.crossover(a, b)
        crossover_match = re.match(r'ta\.crossover\(([^,]+),\s*([^)]+)\)', condition_str)
        if crossover_match:
            return {
                "type": "function_call",
                "namespace": "ta",
                "function": "crossover",
                "args": [crossover_match.group(1).strip(), crossover_match.group(2).strip()],
            }

        # Check for ta.crossunder(a, b)
        crossunder_match = re.match(r'ta\.crossunder\(([^,]+),\s*([^)]+)\)', condition_str)
        if crossunder_match:
            return {
                "type": "function_call",
                "namespace": "ta",
                "function": "crossunder",
                "args": [crossunder_match.group(1).strip(), crossunder_match.group(2).strip()],
            }

        # Check for comparison: a > b
        comp_match = re.match(r'(.+)\s*(>|<|>=|<=|==|!=)\s*(.+)', condition_str)
        if comp_match:
            return {
                "type": "comparison",
                "left": comp_match.group(1).strip(),
                "operator": comp_match.group(2),
                "right": comp_match.group(3).strip(),
            }

        # Default: treat as boolean variable
        return {
            "type": "identifier",
            "name": condition_str,
        }

    def _parse_statement(self, line: str) -> Dict[str, Any] | None:
        """Parse a single statement line."""
        if not line:
            return None

        # Strategy call
        strategy_match = re.match(r'strategy\.(entry|close)\((.*)\)', line)
        if strategy_match:
            func = strategy_match.group(1)
            args_str = strategy_match.group(2)

            if func == 'entry':
                parts = [p.strip() for p in args_str.split(',')]
                name = parts[0].strip('"\'')
                direction = parts[1] if len(parts) > 1 else "strategy.long"

                return {
                    "type": "strategy_call",
                    "function": "strategy.entry",
                    "name": name,
                    "direction": direction,
                }
            elif func == 'close':
                name = args_str.strip('"\'')
                return {
                    "type": "strategy_call",
                    "function": "strategy.close",
                    "name": name,
                }

        # Assignment with function call
        decl_match = re.match(r'(\w+)\s*=\s*(ta|math)\.(\w+)\((.*)\)', line)
        if decl_match:
            var_name = decl_match.group(1)
            namespace = decl_match.group(2)
            func_name = decl_match.group(3)
            args_str = decl_match.group(4)
            args = self._parse_args(args_str)

            return {
                "type": "indicator_call",
                "var_name": var_name,
                "namespace": namespace,
                "function": func_name,
                "args": args,
            }

        return None


# Keep Transformer for API compatibility (not used in regex parser)
class PineToASTTransformer:
    """Placeholder transformer for API compatibility."""
    pass
