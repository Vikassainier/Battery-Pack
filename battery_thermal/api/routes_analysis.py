"""Analysis endpoints: load preview (Phase 3); analysis / sensitivity / optimisation / reports are added in later phases."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..engine.load import LoadError, build_load, load_summary
from ..engine.pack import ConfigError, derive_pack
from ..engine.schemas import (
    AnalysisRequest, CRateLimits, CRateProfile, CellSpec, CycleOptions, DriveCycle, PackConfig, VehicleParams,
)
from ..engine.validation import has_errors, validate_load

router = APIRouter(prefix="/api", tags=["analysis"])

_PLACEHOLDER_PACK = PackConfig(ns=1, np=1, n_modules=1, cells_per_module=1)


class LoadPreviewRequest(BaseModel):
    """Everything needed to construct the electrical load; cell/pack may still be incomplete while the user works."""
    model_config = ConfigDict(extra="ignore")
    cell: CellSpec = Field(default_factory=CellSpec)
    pack: PackConfig | None = None
    cycle: DriveCycle | None = None
    cycle_options: CycleOptions = Field(default_factory=CycleOptions)
    vehicle: VehicleParams | None = None
    crate_limits: CRateLimits = Field(default_factory=CRateLimits)
    crate_profile: CRateProfile | None = None


def _issue(code: str, msg: str) -> dict:
    return {"code": code, "severity": "error", "field": "", "message": msg, "hint": ""}


@router.post("/load/preview")
def load_preview(body: LoadPreviewRequest):
    """Construct the electrical load exactly as the analysis will (cycle -> battery power/current) and describe it."""
    req = AnalysisRequest(cell=body.cell, pack=body.pack or _PLACEHOLDER_PACK, cycle=body.cycle, cycle_options=body.cycle_options,
                          vehicle=body.vehicle, crate_limits=body.crate_limits, crate_profile=body.crate_profile)
    issues = validate_load(req)
    if has_errors(issues):
        return {"ok": False, "issues": [i.to_dict() for i in issues]}
    pack = None
    if body.pack is not None:
        try:
            pack = derive_pack(body.cell, body.pack)
        except ConfigError:
            pack = None
    try:
        if req.cycle is None and pack is None:
            raise LoadError("A C-rate profile needs valid cell and pack data (capacity, Ns, Np, modules).")
        lp = build_load(req, pack)
    except LoadError as exc:
        return {"ok": False, "issues": [i.to_dict() for i in issues] + [_issue("LOAD_ERROR", str(exc))]}
    return {"ok": True, "issues": [i.to_dict() for i in issues], "load": load_summary(lp)}
