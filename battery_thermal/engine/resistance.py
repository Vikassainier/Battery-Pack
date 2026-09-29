"""Cell resistance models.

    Level 1  R = R_dc                                  (constant)
    Level 2  R = f(SOC)                                (datasheet R-vs-SOC table, at its reference temperature)
    Level 3  R = f(T)                                  (datasheet R-vs-T table, at its reference SOC)
    Level 4  R = f(SOC, T)                             (bilinear R-map, or the separable form
                                                        f(SOC)·f(T)/f(T_ref) when only 1-D tables exist)

Tables are interpolated linearly; queries outside the tabulated range are **blocked** unless extrapolation is
explicitly enabled (``clamp`` / ``linear``). The model states which level was used and how each table was used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .interp import Interp1D, Interp2D, OutOfRangeError, RangeUse
from .schemas import CellSpec, ResistanceSettings

LEVEL_NAMES = {1: "Level 1 - constant resistance", 2: "Level 2 - SOC-dependent resistance R(SOC)",
               3: "Level 3 - temperature-dependent resistance R(T)", 4: "Level 4 - SOC + temperature dependent resistance R(SOC,T)"}


class ResistanceModelError(ValueError):
    pass


@dataclass
class ResistanceModel:
    level: int
    description: str
    fn: Callable[[float, float], float]                 # (soc_pct, t_c) -> mOhm at reference direction (discharge)
    scale: float = 1.0
    charge_factor: float = 1.0
    policy: str = "block"
    depends_on: tuple[str, ...] = ()
    tables: list = field(default_factory=list)
    floor_mohm: float = 1e-6
    n_floored: int = 0
    separable: bool = False

    @property
    def name(self) -> str:
        return LEVEL_NAMES[self.level] + (" (separable approximation)" if self.separable else "")

    def r_ohm(self, soc_pct: float, t_c: float, charging: bool = False) -> float:
        """Resistance [Ω] at the given state. Raises OutOfRangeError when extrapolation is blocked."""
        r = self.fn(soc_pct, t_c) * self.scale
        if charging:
            r *= self.charge_factor
        if r < self.floor_mohm:                          # linear extrapolation must never produce R <= 0
            r = self.floor_mohm
            self.n_floored += 1
        return r * 1e-3

    def usage(self) -> list[dict]:
        out = []
        for tb in self.tables:
            for u in ([tb.use] if hasattr(tb, "use") else [tb.use_x, tb.use_y]):
                out.append(u.to_dict())
        return out

    def describe(self) -> dict:
        return {"level": self.level, "name": self.name, "description": self.description, "depends_on": list(self.depends_on),
                "extrapolation_policy": self.policy, "scale": self.scale, "charge_factor": self.charge_factor,
                "separable": self.separable}


def available_levels(cell: CellSpec) -> list[int]:
    lv = []
    if cell.r_dc_mohm is not None:
        lv.append(1)
    if cell.r_vs_soc is not None or cell.r_map is not None:
        lv.append(2)
    if cell.r_vs_temp is not None or cell.r_map is not None:
        lv.append(3)
    if cell.r_map is not None or (cell.r_vs_soc is not None and cell.r_vs_temp is not None):
        lv.append(4)
    return lv


def build_resistance_model(cell: CellSpec, settings: ResistanceSettings) -> ResistanceModel:
    pol = settings.extrapolation
    avail = available_levels(cell)
    if not avail:
        raise ResistanceModelError("No resistance data available: provide a DC resistance, an R-vs-SOC/T table or an R-map.")
    level = max(avail) if settings.level == "auto" else int(settings.level)
    if level not in avail:
        need = {1: "a DC internal resistance value", 2: "an R-vs-SOC table (or R-map)", 3: "an R-vs-temperature table (or R-map)",
                4: "an R-map, or both R-vs-SOC and R-vs-temperature tables"}[level]
        raise ResistanceModelError(f"Resistance model level {level} needs {need}; the confirmed cell data supports level(s) {avail}.")

    common = dict(scale=settings.scale, charge_factor=settings.charge_factor, policy=pol)
    if level == 1:
        r = float(cell.r_dc_mohm)
        cond = []
        if cell.r_ref_soc_pct is not None:
            cond.append(f"{cell.r_ref_soc_pct:g} % SOC")
        if cell.r_ref_temp_c is not None:
            cond.append(f"{cell.r_ref_temp_c:g} °C")
        return ResistanceModel(1, f"R = {r:g} mΩ constant" + (f" (datasheet condition: {', '.join(cond)})" if cond else ""),
                               lambda soc, t, r=r: r, **common)
    if level == 2:
        if cell.r_vs_soc is not None:
            f = Interp1D(cell.r_vs_soc.x, cell.r_vs_soc.y, "R-vs-SOC table", "SOC [%]", pol)
            return _mk(2, "R interpolated linearly from the R-vs-SOC table (temperature dependence not modelled)",
                       lambda soc, t, f=f: f(soc), ("SOC",), [f], f, common)
        g = _map(cell, pol)
        t_ref = cell.r_ref_temp_c
        if t_ref is None:
            raise ResistanceModelError("Level 2 from an R-map needs the reference temperature (cell.r_ref_temp_c) at which to slice the map.")
        return _mk(2, f"R-map sliced at T = {t_ref:g} °C", lambda soc, t, g=g, tr=t_ref: g(soc, tr), ("SOC",), [g], None, common, floor_from=g)
    if level == 3:
        if cell.r_vs_temp is not None:
            f = Interp1D(cell.r_vs_temp.x, cell.r_vs_temp.y, "R-vs-T table", "T [°C]", pol)
            return _mk(3, "R interpolated linearly from the R-vs-temperature table (SOC dependence not modelled)",
                       lambda soc, t, f=f: f(t), ("T",), [f], f, common)
        g = _map(cell, pol)
        s_ref = cell.r_ref_soc_pct
        if s_ref is None:
            raise ResistanceModelError("Level 3 from an R-map needs the reference SOC (cell.r_ref_soc_pct) at which to slice the map.")
        return _mk(3, f"R-map sliced at SOC = {s_ref:g} %", lambda soc, t, g=g, sr=s_ref: g(sr, t), ("T",), [g], None, common, floor_from=g)
    # level 4
    if cell.r_map is not None:
        g = _map(cell, pol)
        return _mk(4, "R interpolated bilinearly from the SOC × temperature resistance map", lambda soc, t, g=g: g(soc, t),
                   ("SOC", "T"), [g], None, common, floor_from=g)
    if cell.r_ref_temp_c is None:
        raise ResistanceModelError("The separable Level 4 model needs the reference temperature (cell.r_ref_temp_c) of the R-vs-SOC table.")
    fs = Interp1D(cell.r_vs_soc.x, cell.r_vs_soc.y, "R-vs-SOC table", "SOC [%]", pol)
    ft = Interp1D(cell.r_vs_temp.x, cell.r_vs_temp.y, "R-vs-T table", "T [°C]", "block")
    t_ref = float(cell.r_ref_temp_c)
    try:
        r_t_ref = ft(t_ref)
    except OutOfRangeError as exc:
        raise ResistanceModelError(f"Reference temperature {t_ref:g} °C of the R-vs-SOC table lies outside the R-vs-T table range: {exc}") from exc
    ft.policy = pol
    ft.use = RangeUse(ft.name, ft.axis, *ft.range)         # do not count the reference evaluation as a use
    m = _mk(4, f"R(SOC,T) = R_SOC(SOC) · R_T(T) / R_T({t_ref:g} °C): separable combination of the 1-D tables "
               f"(assumes SOC and temperature effects are multiplicative)", lambda soc, t, fs=fs, ft=ft, r0=r_t_ref: fs(soc) * ft(t) / r0,
            ("SOC", "T"), [fs, ft], None, common, floor_from=None)
    m.separable = True
    m.floor_mohm = 0.1 * min(min(cell.r_vs_soc.y), 1e9)
    return m


def _map(cell: CellSpec, pol: str) -> Interp2D:
    return Interp2D(cell.r_map.x, cell.r_map.y, cell.r_map.z, "R-map", "SOC [%]", "T [°C]", pol)


def _mk(level, desc, fn, deps, tables, f, common, floor_from=None) -> ResistanceModel:
    m = ResistanceModel(level, desc, fn, depends_on=deps, tables=tables, **common)
    if f is not None:
        m.floor_mohm = 0.1 * min(f._y)
    elif floor_from is not None:
        m.floor_mohm = 0.1 * min(min(r) for r in floor_from._z)
    return m
