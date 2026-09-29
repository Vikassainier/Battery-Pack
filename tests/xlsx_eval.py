"""Minimal evaluator for the simple Excel formulas used in the workbook (arithmetic, ^, ABS/EXP/SQRT/MAX/MIN/IF, same-sheet refs).

LibreOffice is not available in every environment, so the live formulas in the 'Hand calcs' sheet are verified with this evaluator.
"""
from __future__ import annotations

import math
import re

_REF = re.compile(r"\b([A-Z]{1,2}[0-9]+)\b")


def _split_args(s: str) -> list[str]:
    args, depth, cur, quote = [], 0, "", False
    for ch in s:
        if ch == '"':
            quote = not quote
        if not quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                args.append(cur)
                cur = ""
                continue
        cur += ch
    args.append(cur)
    return args


def _convert_if(expr: str) -> str:
    """IF(c,a,b) -> ((a) if (c) else (b)), innermost first."""
    while "IF(" in expr:
        i = expr.rindex("IF(")
        j, depth = i + 3, 1
        while depth:
            depth += {"(": 1, ")": -1}.get(expr[j], 0)
            j += 1
        c, a, b = _split_args(expr[i + 3:j - 1])
        expr = expr[:i] + f"(({a}) if ({c}) else ({b}))" + expr[j:]
    return expr


class SheetEval:
    def __init__(self, ws):
        self.ws = ws
        self.cache: dict[str, object] = {}

    def value(self, ref: str):
        if ref in self.cache:
            return self.cache[ref]
        v = self.ws[ref].value
        if isinstance(v, str) and v.startswith("="):
            v = self.evaluate(v[1:])
        self.cache[ref] = v
        return v

    def evaluate(self, expr: str):
        py = _convert_if(expr).replace("^", "**").replace("<>", "!=")
        py = re.sub(r"(?<![<>=!])=(?!=)", "==", py)
        py = _REF.sub(r'val("\1")', py)
        return eval(py, {"__builtins__": {}}, {"val": self.value, "ABS": abs, "EXP": math.exp, "SQRT": math.sqrt, "MAX": max, "MIN": min})   # noqa: S307
