"""Battery heat-generation model.

    Q_joule    = I²·R                          (irreversible, always ≥ 0)
    Q_entropic = −I·T·dU/dT                    (reversible; sign follows the current and dU/dT; T in kelvin)
    Q_cell     = Q_joule + Q_entropic
    Q_module   = cells_per_module · Q_cell
    Q_pack     = Ns·Np·Q_cell  =  Ns·Np·I_cell²·R  (+ entropic)

Entropic heat is **never ignored silently**: the model always carries a status. If dU/dT is unknown, the user
must either supply an estimate or explicitly exclude it; the result then reports the exclusion and a bounding
estimate of what was left out.
"""
from __future__ import annotations

from dataclasses import dataclass

from .interp import Interp1D, Interp2D
from .schemas import CellSpec, EntropicSettings
from .units import KELVIN

INDICATIVE_MV_PER_K = 0.2          # typical magnitude of |dU/dT| for Li-ion cells - used ONLY to bound an excluded term


def joule_heat_w(i_cell: float, r_ohm: float) -> float:
    return i_cell * i_cell * r_ohm


def entropic_heat_w(i_cell: float, t_c: float, dudt_v_per_k: float) -> float:
    return -i_cell * (t_c + KELVIN) * dudt_v_per_k


class EntropicModelError(ValueError):
    pass


@dataclass
class EntropicModel:
    mode: str                          # table | map | constant | excluded
    status: str                        # human-readable status shown in the results
    included: bool
    _table: Interp1D | None = None
    _map: Interp2D | None = None
    _const_v_per_k: float = 0.0
    source: str = ""

    def dudt_v_per_k(self, soc_pct: float, t_c: float) -> float:
        if self.mode == "table":
            return self._table(soc_pct) * 1e-3
        if self.mode == "map":
            return self._map.ddy(soc_pct, t_c)
        if self.mode == "constant":
            return self._const_v_per_k
        return 0.0

    @property
    def tables(self) -> list:
        return [x for x in (self._table, self._map) if x is not None]

    def bound_w_per_cell(self, i_cell_max: float, t_c: float) -> float:
        """Indicative magnitude of the reversible heat at the peak current if dU/dT were ±0.2 mV/K."""
        return abs(i_cell_max) * (t_c + KELVIN) * INDICATIVE_MV_PER_K * 1e-3

    def describe(self) -> dict:
        return {"mode": self.mode, "status": self.status, "included": self.included, "source": self.source}


def build_entropic_model(cell: CellSpec, settings: EntropicSettings, ocv_map_policy: str = "block") -> EntropicModel:
    mode = settings.mode
    have_table = cell.dudt_vs_soc is not None
    have_map = cell.ocv_map is not None and len(cell.ocv_map.y) >= 2
    const = settings.constant_mv_per_k

    if mode == "auto":
        if have_table:
            mode = "table"
        elif have_map:
            mode = "map"
        elif const is not None:
            mode = "constant"
        else:
            mode = "excluded_no_data"
    if mode == "table":
        if not have_table:
            raise EntropicModelError("Entropic mode 'table' needs a dU/dT-vs-SOC table on the cell.")
        return EntropicModel("table", "Included: dU/dT interpolated from the datasheet table vs SOC", True,
                             _table=Interp1D(cell.dudt_vs_soc.x, cell.dudt_vs_soc.y, "dU/dT-vs-SOC table", "SOC [%]", ocv_map_policy),
                             source="datasheet table (mV/K)")
    if mode == "map":
        if not have_map:
            raise EntropicModelError("Entropic mode 'map' needs an OCV map at two or more temperatures.")
        return EntropicModel("map", "Included: dU/dT derived from the OCV(SOC,T) map by finite difference in temperature", True,
                             _map=Interp2D(cell.ocv_map.x, cell.ocv_map.y, cell.ocv_map.z, "OCV map (for dU/dT)", "SOC [%]", "T [°C]", ocv_map_policy),
                             source="OCV map, finite difference")
    if mode == "constant":
        if const is None:
            raise EntropicModelError("Entropic mode 'constant' needs a dU/dT estimate [mV/K].")
        return EntropicModel("constant", f"Included using a USER ESTIMATE of {const:g} mV/K (not measured) - low confidence", True,
                             _const_v_per_k=const * 1e-3, source=f"user estimate {const:g} mV/K")
    if mode == "excluded":
        return EntropicModel("excluded", "EXCLUDED by the user - reversible (entropic) heat is not part of the result", False, source="excluded by user")
    return EntropicModel("excluded", "EXCLUDED because dU/dT data are unavailable and no estimate was entered - reversible heat is NOT included; "
                                     "enter an estimated coefficient to include it", False, source="no data")
