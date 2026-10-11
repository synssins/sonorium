"""
Routes the Sonorium app needs (docs/APP_API.md, section 13), kept apart from
api_v2.py. add_app_routes() adds them to the /api router that
create_api_router() builds; each has its permission in
sonorium/auth/permissions.py.
"""
from __future__ import annotations

import math
from typing import Optional

from fastapi import HTTPException, Request


async def _json_or_empty(request: Request) -> dict:
    """The JSON object sent with the request, {} when there is no body. 400 for anything else."""
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        import json
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    return data


async def hold_seconds_of(request: Request) -> Optional[float]:
    """
    POST /sessions/{id}/listen's optional hold: {"hold_seconds": n} or
    ?hold_seconds=n. None when not given; 400 when not a number (the range is
    checked by SessionManager.listen).
    """
    value = request.query_params.get("hold_seconds")
    if value is None:
        value = (await _json_or_empty(request)).get("hold_seconds")
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = math.nan
    if isinstance(value, bool) or not math.isfinite(seconds):
        raise HTTPException(status_code=400, detail="hold_seconds must be a number of seconds")
    return seconds
