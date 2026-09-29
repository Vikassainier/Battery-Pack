"""FastAPI application - a thin layer over the calculation engine.

All engineering lives in ``battery_thermal.engine``; this module only parses requests, calls the
engine and serialises results. Run with::

    uvicorn battery_thermal.api.main:app --reload
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..engine.assumptions import assumed_default_paths, catalog_dict
from ..engine.pack import ConfigError, derive_pack
from ..engine.schemas import (
    CellSpec, ColdPlateSpec, CoolantSpec, CycleOptions, EntropicSettings, LimitSettings, PackConfig,
    PumpSpec, RadiatorSpec, ResistanceSettings, ThermalSettings,
)
from ..engine.validation import has_errors, validate_cell, validate_pack

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="EV Battery Thermal Analysis Tool", version="0.1.0",
              description="Traceable Li-ion pack heat-generation, thermal and cooling-system sizing engine.")


class ConfigCheck(BaseModel):
    cell: CellSpec
    pack: PackConfig
    require_cell_confirmation: bool = False
    probe_pack_current_a: float | None = None     # optional: convert a pack current into cell/module current + C-rate
    probe_c_rate: float | None = None             # optional: convert a cell C-rate into currents


@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version}


@app.post("/api/config/validate")
def config_validate(body: ConfigCheck):
    """Phase 1: derive pack quantities and flag configuration inconsistencies."""
    issues = validate_cell(body.cell, body.require_cell_confirmation) + validate_pack(body.cell, body.pack)
    derived = None
    try:
        derived = derive_pack(body.cell, body.pack).to_dict()
    except ConfigError:
        pass
    probe = None
    if derived is not None and (body.probe_pack_current_a is not None or body.probe_c_rate is not None):
        d = derive_pack(body.cell, body.pack)
        if body.probe_pack_current_a is not None:
            i_cell = d.cell_current(body.probe_pack_current_a)
        else:
            i_cell = body.probe_c_rate * d.cell_capacity_ah
        i_pack = d.pack_current_from_cell(i_cell)
        probe = {"i_pack_a": i_pack, "i_module_a": d.module_current(i_pack), "i_cell_a": i_cell,
                 "c_rate": d.c_rate(i_cell)}
    return {"ok": not has_errors(issues), "derived": derived, "probe": probe,
            "issues": [i.to_dict() for i in issues]}


@app.get("/api/defaults")
def defaults():
    """Generic engineering defaults (never cell data). ``assumed_paths`` are flagged as assumptions in the UI."""
    return {
        "groups": {
            "cycle_options": CycleOptions().model_dump(),
            "resistance": ResistanceSettings().model_dump(),
            "entropic": EntropicSettings().model_dump(),
            "thermal": ThermalSettings().model_dump(),
            "coolant": CoolantSpec().model_dump(),
            "pump": PumpSpec().model_dump(),
            "radiator": RadiatorSpec().model_dump(),
            "limits": LimitSettings().model_dump(),
        },
        "cold_plate": ColdPlateSpec().model_dump(),
        "assumed_paths": assumed_default_paths(),
        "catalog": catalog_dict(),
    }


@app.get("/vendor/plotly.min.js")
def plotly_js():
    """Serve Plotly.js from the installed python package so the UI works offline (no CDN)."""
    import plotly
    p = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
    if not p.exists():
        raise HTTPException(404, "plotly.min.js not found in the plotly package")
    return FileResponse(p, media_type="application/javascript")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
