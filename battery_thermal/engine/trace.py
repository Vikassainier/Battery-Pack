"""Calculation traceability.

Every important number registers a :class:`TraceNode` that records
Input -> Formula -> Substitution -> Result, and which other nodes it depends on.
The UI/report can then walk the dependency graph from any output back to the
raw user / datasheet / assumed inputs. Nothing in the engine is a black box.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable


def _clean(v: Any):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f if math.isfinite(f) else None


@dataclass
class TraceNode:
    id: str
    label: str
    value: Any
    unit: str = ""
    kind: str = "intermediate"      # input | assumption | intermediate | result
    formula: str = ""
    substitution: str = ""
    inputs: list[str] = field(default_factory=list)
    source: str = ""                # user | datasheet | assumed | calculated
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class TraceLog:
    """Ordered collection of trace nodes (last write wins for a repeated id)."""

    def __init__(self) -> None:
        self.nodes: dict[str, TraceNode] = {}

    # -- recording ----------------------------------------------------------------------------
    def input(self, id: str, label: str, value: Any, unit: str = "", source: str = "user", note: str = "") -> str:
        kind = "assumption" if source == "assumed" else "input"
        self.nodes[id] = TraceNode(id, label, _clean(value), unit, kind, source=source, note=note)
        return id

    def calc(self, id: str, label: str, value: Any, unit: str, formula: str, substitution: str = "",
             inputs: Iterable[str] = (), note: str = "", kind: str = "intermediate") -> str:
        self.nodes[id] = TraceNode(id, label, _clean(value), unit, kind, formula, substitution,
                                   [i for i in inputs if i], "calculated", note)
        return id

    def result(self, id: str, label: str, value: Any, unit: str, formula: str, substitution: str = "",
               inputs: Iterable[str] = (), note: str = "") -> str:
        return self.calc(id, label, value, unit, formula, substitution, inputs, note, kind="result")

    def set_source(self, id: str, source: str) -> None:
        """Re-label the provenance of an existing input node (user | datasheet | assumed | calculated)."""
        n = self.nodes.get(id)
        if n is not None and n.kind in ("input", "assumption"):
            n.source = source
            n.kind = "assumption" if source == "assumed" else "input"

    # -- querying -----------------------------------------------------------------------------
    def get(self, id: str) -> TraceNode | None:
        return self.nodes.get(id)

    def chain(self, id: str) -> list[TraceNode]:
        """All nodes the given node depends on (depth-first, dependencies before dependents)."""
        seen: set[str] = set()
        order: list[TraceNode] = []

        def visit(nid: str) -> None:
            if nid in seen or nid not in self.nodes:
                return
            seen.add(nid)
            for dep in self.nodes[nid].inputs:
                visit(dep)
            order.append(self.nodes[nid])

        visit(id)
        return order

    def to_dict(self) -> dict[str, dict]:
        return {k: v.to_dict() for k, v in self.nodes.items()}


def fmt(v: float, digits: int = 4) -> str:
    """Compact number formatting for substitution strings."""
    if v is None:
        return "n/a"
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e5 or a < 1e-3:
        return f"{v:.{digits - 1}e}"
    return f"{v:.{digits}g}"
