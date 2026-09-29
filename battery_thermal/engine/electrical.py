"""Cell electrical model: OCV, terminal voltage, power <-> current conversion, SOC.

Engineering logic
-----------------
Equivalent circuit (per cell):  V_t = OCV(SOC,T) − I·R          (I > 0 discharge)

A load specified as *terminal power* P_batt (positive = discharge) is shared equally between the N = Ns·Np cells:

    p = P_batt / N = V_t · I = (OCV − I·R)·I        ⇒   R·I² − OCV·I + p = 0
    I = [OCV − √(OCV² − 4·R·p)] / (2R) = 2p / (OCV + √(OCV² − 4Rp))            (numerically stable form)

The root exists only while OCV² ≥ 4Rp (maximum power transfer); beyond that the requested power cannot be
delivered by the pack and the current is limited to OCV/(2R) and flagged as *infeasible*.

Energy bookkeeping (kept separate on purpose - electrical energy is NOT heat):
    internal (chemical) power  P_chem = OCV·I·N = P_batt + I²R·N
    heat generated             Q      = I²R·N  (irreversible)  −  I·T·dU/dT·N  (reversible)
"""
from __future__ import annotations

import math

from .interp import Interp1D, Interp2D
from .schemas import CellSpec


class OcvModel:
    """OCV(SOC, T). Uses the OCV map, else the OCV-vs-SOC curve, else the nominal voltage (a constant, flagged)."""

    def __init__(self, cell: CellSpec, policy: str = "block"):
        self.kind = "constant"
        self._const = float(cell.v_nom)
        self._curve = self._map = None
        if cell.ocv_map is not None:
            self._map = Interp2D(cell.ocv_map.x, cell.ocv_map.y, cell.ocv_map.z, "OCV map", "SOC [%]", "T [°C]", policy)
            self.kind = "map"
        elif cell.ocv_vs_soc is not None:
            self._curve = Interp1D(cell.ocv_vs_soc.x, cell.ocv_vs_soc.y, "OCV-vs-SOC table", "SOC [%]", policy)
            self.kind = "curve"

    def ocv(self, soc_pct: float, t_c: float) -> float:
        if self._map is not None:
            return self._map(soc_pct, t_c)
        if self._curve is not None:
            return self._curve(soc_pct)
        return self._const

    @property
    def tables(self) -> list:
        return [x for x in (self._map, self._curve) if x is not None]

    def describe(self) -> dict:
        txt = {"map": "OCV interpolated from the SOC × temperature OCV map",
               "curve": "OCV interpolated from the OCV-vs-SOC curve (temperature dependence of OCV not modelled)",
               "constant": f"OCV held constant at the nominal voltage {self._const:g} V - no OCV curve available (approximation)"}[self.kind]
        return {"kind": self.kind, "description": txt}


def cell_current_from_power(p_cell_w: float, ocv: float, r_ohm: float) -> tuple[float, bool]:
    """Solve (OCV − I·R)·I = p for the cell current. Returns (I, feasible)."""
    disc = ocv * ocv - 4.0 * r_ohm * p_cell_w
    if disc >= 0.0:
        return 2.0 * p_cell_w / (ocv + math.sqrt(disc)), True
    return ocv / (2.0 * r_ohm), False          # maximum-power point: the request cannot be met


def terminal_voltage(ocv: float, i_cell: float, r_ohm: float) -> float:
    return ocv - i_cell * r_ohm


def soc_step(soc_pct: float, i_cell: float, dt_s: float, capacity_ah: float) -> float:
    """Coulomb counting, sample-and-hold current over the step."""
    return soc_pct - i_cell * dt_s / (3600.0 * capacity_ah) * 100.0
