"""One-at-a-time sensitivity analysis.

Every parameter is perturbed around the base case (relative or absolute steps), the full analysis is re-run
(``mode='scalars'``), and the effect on four outputs is recorded:

    maximum heat generation [kW] · required cooling capacity [kW] · required coolant flow [L/min] · maximum cell temperature [°C]

Perturbed cases that fail validation are reported as such (never silently dropped).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .pipeline import run_analysis
from .schemas import AnalysisRequest

OUTPUTS = [("max_heat_kw", "Maximum heat generation", "kW"), ("q_required_kw", "Required cooling capacity", "kW"),
           ("flow_lpm", "Required coolant flow", "L/min"), ("t_hot_max_c", "Maximum cell temperature", "°C")]


def metrics(res: dict) -> dict:
    if res.get("status") not in ("ok", "completed_with_errors"):
        return {}
    return {"max_heat_kw": res["heat"]["max_pack_heat_kw"], "q_required_kw": res["design"]["q_required_w"] / 1e3,
            "flow_lpm": res["cooling"]["pack"]["lpm"], "t_hot_max_c": res["thermal"]["t_hot_max_c"]}


@dataclass
class Param:
    key: str
    label: str
    unit: str
    mode: str                                   # rel | abs
    levels: list[float]
    apply: Callable[[AnalysisRequest, float, dict], str | None]      # returns a reason string if not applicable
    base: Callable[[AnalysisRequest, dict], float | None]
    needs_plate: bool = False


def _need_plate(req: AnalysisRequest):
    return None if req.cold_plate is not None else "needs a cold plate definition"


def _resist(req, f, base):
    req.resistance.scale = req.resistance.scale * (1 + f)


def _ambient(req, d, base):
    req.pack.t_ambient_c += d


def _inlet(req, d, base):
    req.coolant.inlet_c += d
    if req.coolant.max_outlet_c is not None:
        req.coolant.max_outlet_c += d


def _flow(req, f, base):
    b = base["sizing"]["flow"]["actual_lpm"] or base["sizing"]["flow"]["required_lpm"]
    req.cold_plate.flow_lpm = b * (1 + f)


def _plate_attr(attr):
    def fn(req, f, base):
        setattr(req.cold_plate, attr, getattr(req.cold_plate, attr) * (1 + f))
    return fn


def _nch(req, f, base):
    req.cold_plate.n_channels = max(1, round(req.cold_plate.n_channels * (1 + f)))


def _crate(req, f, base):
    req.cycle_options.load_scale = req.cycle_options.load_scale * (1 + f)


def _soc(req, d, base):
    if req.cycle is not None and req.cycle.soc_pct is not None:
        req.cycle.soc_pct = [min(100.0, max(0.0, x + d)) for x in req.cycle.soc_pct]
    else:
        v = req.pack.soc_initial_pct + d
        if not (0 <= v <= 100):
            return f"initial SOC {v:g} % is outside 0-100 %"
        req.pack.soc_initial_pct = v


def _tcell(req, d, base):
    req.pack.t_initial_c += d


def parameters() -> list[Param]:
    return [
        Param("resistance", "Cell resistance (scale)", "%", "rel", [-0.2, -0.1, 0.1, 0.2], _resist, lambda r, b: r.resistance.scale),
        Param("ambient", "Ambient temperature", "K", "abs", [-10, -5, 5, 10], _ambient, lambda r, b: r.pack.t_ambient_c),
        Param("coolant_inlet", "Coolant inlet temperature", "K", "abs", [-5, -2.5, 2.5, 5], _inlet, lambda r, b: r.coolant.inlet_c),
        Param("coolant_flow", "Coolant flow rate", "%", "rel", [-0.25, -0.1, 0.1, 0.25], _flow,
              lambda r, b: b["sizing"]["flow"]["actual_lpm"] or b["sizing"]["flow"]["required_lpm"], needs_plate=True),
        Param("tim_thickness", "TIM thickness", "%", "rel", [-0.5, -0.25, 0.25, 0.5], _plate_attr("tim_thickness_mm"), lambda r, b: r.cold_plate.tim_thickness_mm, True),
        Param("tim_k", "TIM conductivity", "%", "rel", [-0.5, -0.25, 0.25, 0.5], _plate_attr("tim_k_w_mk"), lambda r, b: r.cold_plate.tim_k_w_mk, True),
        Param("c_rate", "C-rate (load scale)", "%", "rel", [-0.2, -0.1, 0.1, 0.2], _crate, lambda r, b: r.cycle_options.load_scale),
        Param("soc", "SOC", "% pts", "abs", [-20, -10, 10, 20], _soc, lambda r, b: r.pack.soc_initial_pct),
        Param("cell_temperature", "Initial cell temperature", "K", "abs", [-10, -5, 5, 10], _tcell, lambda r, b: r.pack.t_initial_c),
        Param("channel_width", "Channel width", "%", "rel", [-0.25, -0.1, 0.1, 0.25], _plate_attr("channel_width_mm"), lambda r, b: r.cold_plate.channel_width_mm, True),
        Param("channel_height", "Channel height", "%", "rel", [-0.25, -0.1, 0.1, 0.25], _plate_attr("channel_height_mm"), lambda r, b: r.cold_plate.channel_height_mm, True),
        Param("channel_count", "Number of channels", "%", "rel", [-0.5, -0.25, 0.25, 0.5], _nch, lambda r, b: r.cold_plate.n_channels, True),
    ]


def sensitivity_analysis(req: AnalysisRequest, only: list[str] | None = None) -> dict:
    base_res = run_analysis(req, mode="scalars")
    if base_res.get("status") not in ("ok", "completed_with_errors"):
        return {"ok": False, "issues": base_res.get("issues", []), "message": "The base case does not run - fix the errors first."}
    base_m = metrics(base_res)
    rows = []
    for p in parameters():
        if only and p.key not in only:
            continue
        row = {"key": p.key, "label": p.label, "unit": p.unit, "mode": p.mode, "base_value": p.base(req, base_res) if not (p.needs_plate and req.cold_plate is None) else None,
               "levels": [], "skipped": None}
        if p.needs_plate and req.cold_plate is None:
            row["skipped"] = "needs a cold plate definition"
            rows.append(row)
            continue
        for lv in p.levels:
            r2 = req.model_copy(deep=True)
            reason = p.apply(r2, lv, base_res)
            entry = {"level": lv, "label": (f"{lv * 100:+.0f} %" if p.mode == "rel" else f"{lv:+g} {p.unit}")}
            if reason:
                entry.update(status="skipped", reason=reason)
            else:
                res = run_analysis(r2, mode="scalars")
                if res.get("status") in ("ok", "completed_with_errors"):
                    m = metrics(res)
                    entry.update(status="ok", metrics=m,
                                 delta={k: (m[k] - base_m[k]) if (m.get(k) is not None and base_m.get(k) is not None) else None for k in base_m},
                                 delta_pct={k: ((m[k] - base_m[k]) / abs(base_m[k]) * 100.0) if (m.get(k) is not None and base_m.get(k) not in (None, 0)) else None for k in base_m})
                    b = p.base(r2, base_res) if p.key != "coolant_flow" else r2.cold_plate.flow_lpm
                    entry["value"] = b
                else:
                    entry.update(status="blocked", reason="; ".join(i["message"] for i in res.get("issues", []) if i["severity"] == "error")[:200])
            row["levels"].append(entry)
        rows.append(row)
    tornado = {}
    for key, label, unit in OUTPUTS:
        items = []
        for row in rows:
            ds = [lv["delta"][key] for lv in row["levels"] if lv.get("status") == "ok" and lv["delta"].get(key) is not None]
            if ds:
                items.append({"key": row["key"], "label": row["label"], "low": min(ds + [0.0]), "high": max(ds + [0.0])})
        items.sort(key=lambda x: -(x["high"] - x["low"]))
        tornado[key] = items
    return {"ok": True, "base": base_m, "outputs": [{"key": k, "label": l, "unit": u} for k, l, u in OUTPUTS], "params": rows, "tornado": tornado}
