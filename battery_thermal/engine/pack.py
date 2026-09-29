"""Battery pack configuration: derives voltage, capacity, energy, module structure and current splits.

Engineering logic
-----------------
Cells in a *parallel group* share the string current (assumed uniform); groups in *series* carry the
same string current. Therefore

    I_cell   = I_pack / Np
    I_module = I_pack / Mp          (Mp = number of modules connected in parallel)
    V_pack   = Ns · V_cell          C_pack = Np · C_cell          E_pack = Ns · Np · C_cell · V_nom
    C-rate   = I_cell / C_cell      (1C = nominal capacity in one hour)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from .schemas import CellSpec, PackConfig
from .trace import TraceLog, fmt


class ConfigError(ValueError):
    pass


@dataclass
class PackDerived:
    ns: int
    np: int
    n_cells: int
    n_modules: int
    cells_per_module: int
    modules_in_series: int
    modules_in_parallel: int
    ns_per_module: int
    np_per_module: int
    v_nom: float
    v_max: float | None
    v_min: float | None
    capacity_ah: float
    energy_kwh: float
    cell_capacity_ah: float

    def to_dict(self) -> dict:
        return asdict(self)

    # -- current splits ------------------------------------------------------------------------
    def cell_current(self, i_pack):
        return i_pack / self.np

    def module_current(self, i_pack):
        return i_pack / self.modules_in_parallel

    def pack_current_from_cell(self, i_cell):
        return i_cell * self.np

    def c_rate(self, i_cell):
        return i_cell / self.cell_capacity_ah


def module_topology(pack: PackConfig) -> tuple[int, int]:
    """Return (modules_in_series, modules_in_parallel)."""
    if pack.module_arrangement == "series":
        return pack.n_modules, 1
    if pack.module_arrangement == "parallel":
        return 1, pack.n_modules
    ms = pack.modules_in_series or 0
    if ms < 1 or pack.n_modules % ms:
        raise ConfigError("series_parallel arrangement needs 'modules_in_series' that divides the module count")
    return ms, pack.n_modules // ms


def derive_pack(cell: CellSpec, pack: PackConfig, tr: TraceLog | None = None) -> PackDerived:
    if cell.capacity_ah is None or cell.v_nom is None:
        raise ConfigError("cell capacity and nominal voltage are required to derive pack quantities")
    n_cells = pack.ns * pack.np
    if n_cells != pack.n_modules * pack.cells_per_module:
        raise ConfigError("Ns×Np does not equal modules × cells per module")
    ms, mp = module_topology(pack)
    if pack.ns % ms or pack.np % mp:
        raise ConfigError("module topology does not divide the series/parallel counts")

    v_nom = pack.ns * cell.v_nom
    v_max = pack.ns * cell.v_max if cell.v_max is not None else None
    v_min = pack.ns * cell.v_min if cell.v_min is not None else None
    cap = pack.np * cell.capacity_ah
    energy = n_cells * cell.capacity_ah * cell.v_nom / 1000.0

    d = PackDerived(pack.ns, pack.np, n_cells, pack.n_modules, pack.cells_per_module, ms, mp,
                    pack.ns // ms, pack.np // mp, v_nom, v_max, v_min, cap, energy, cell.capacity_ah)

    if tr is not None:
        tr.input("in.ns", "Cells in series Ns", pack.ns, "-", "user")
        tr.input("in.np", "Cells in parallel Np", pack.np, "-", "user")
        tr.input("in.cell_vnom", "Cell nominal voltage", cell.v_nom, "V", "datasheet")
        tr.input("in.cell_cap", "Cell nominal capacity", cell.capacity_ah, "Ah", "datasheet")
        tr.calc("pack.n_cells", "Total cells", n_cells, "-", "N = Ns · Np", f"{pack.ns} × {pack.np}", ["in.ns", "in.np"])
        tr.result("pack.voltage", "Pack nominal voltage", v_nom, "V", "V_pack = Ns · V_cell",
                  f"{pack.ns} × {fmt(cell.v_nom)}", ["in.ns", "in.cell_vnom"])
        tr.result("pack.capacity", "Pack capacity", cap, "Ah", "C_pack = Np · C_cell",
                  f"{pack.np} × {fmt(cell.capacity_ah)}", ["in.np", "in.cell_cap"])
        tr.result("pack.energy", "Pack nominal energy", energy, "kWh", "E = Ns · Np · C_cell · V_nom / 1000",
                  f"{pack.ns} × {pack.np} × {fmt(cell.capacity_ah)} × {fmt(cell.v_nom)} / 1000",
                  ["pack.n_cells", "in.cell_cap", "in.cell_vnom"])
    return d
