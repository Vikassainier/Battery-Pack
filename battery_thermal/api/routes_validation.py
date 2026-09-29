"""Built-in validation cases (Phase 10): hand calculations versus the complete calculation chain."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..validation_cases.cases import CASE_BUILDERS, list_cases, run_all

router = APIRouter(prefix="/api/validation", tags=["validation"])


class RunRequest(BaseModel):
    ids: list[str] | None = None          # None / empty -> every case


@router.get("/cases")
def cases():
    return list_cases()


@router.post("/run")
def run(body: RunRequest):
    unknown = [i for i in (body.ids or []) if i not in CASE_BUILDERS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown validation case(s): {', '.join(unknown)}. Available: {', '.join(CASE_BUILDERS)}")
    return run_all(body.ids or None)
