"""Ad-hoc OpenAPI diff endpoint (no database, no GitHub credentials needed)."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from ...core.diff import diff_specs
from ...core.openapi import SpecError, parse_spec
from ...schemas import DiffRequest, DiffResponse

router = APIRouter(prefix="/api/v1", tags=["diff"])


@router.post("/diff", response_model=DiffResponse)
def diff_endpoint(payload: DiffRequest) -> DiffResponse:
    try:
        before = parse_spec(json.dumps(payload.before))
        after = parse_spec(json.dumps(payload.after))
    except SpecError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = diff_specs(before, after)
    markdown = result.to_markdown(
        spec_name=payload.spec_name,
        repository=payload.repository,
    )
    return DiffResponse(
        counts=result.counts(),
        has_breaking=result.has_breaking,
        fingerprint=result.fingerprint(),
        changes=[change.to_dict() for change in result.changes],
        markdown=markdown,
    )