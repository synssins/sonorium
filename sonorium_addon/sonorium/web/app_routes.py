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
from pydantic import BaseModel, Field


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


class SpeakerVolumeRequest(BaseModel):
    volume: int = Field(ge=0, le=100)


class SpeakerMutedRequest(BaseModel):
    muted: bool


def add_app_routes(router, *, session_manager, ha_registry=None, speaker_live=None) -> None:
    """The app's routes, on create_api_router()'s /api router."""

    def session_or_404(session_id: str):
        session = session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    # --- Outputs: "Cast device X plays channel N" (G2, core/outputs.py) ---

    def output_answer(output, channel_id) -> dict:
        from sonorium.core.outputs import EXPIRE_SECONDS, HEARTBEAT_SECONDS
        body = output.to_dict(session_manager.outputs.clock())
        if channel_id is not None:
            path = f"/stream/channel{channel_id}"
            base = (getattr(session_manager, "stream_base_url", "") or "").rstrip("/")
            body.update(channel_id=channel_id, stream_path=path, stream_url=f"{base}{path}")
        body.update(heartbeat_seconds=HEARTBEAT_SECONDS, expire_seconds=EXPIRE_SECONDS)
        return body

    @router.get("/sessions/{session_id}/outputs")
    async def list_session_outputs(session_id: str) -> dict:
        """The devices the app reports playing this channel."""
        session_or_404(session_id)
        return {"outputs": session_manager.outputs.describe(session_id)}

    @router.post("/sessions/{session_id}/outputs", status_code=201)
    async def add_session_output(session_id: str, request: Request) -> dict:
        """
        The app tells Sonorium a device (a Cast device) plays this channel's
        stream: {"kind": "cast", "name", "address", "receiver_id"}. The mix runs
        while the output is reported (heartbeat: PUT .../outputs/{output_id}).
        Give the device stream_url as it is: the plain stream follows the
        channel's speakers-muted switch (?local=1 would not).
        """
        from sonorium.auth.middleware import principal_of
        from sonorium.core.outputs import OutputError
        data = await _json_or_empty(request)
        session_or_404(session_id)
        try:
            principal = principal_of(request)
        except Exception:
            principal = None
        try:
            output, channel = session_manager.add_output(
                session_id, data, device_id=getattr(principal, "device_id", None))
        except KeyError:
            raise HTTPException(status_code=404, detail="Session not found")
        except OutputError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ValueError as e:  # no theme, or it isn't found
            raise HTTPException(status_code=400, detail=str(e))
        if channel is None:
            raise HTTPException(status_code=409, detail="No free channel")
        return output_answer(output, channel.id)

    @router.put("/sessions/{session_id}/outputs/{output_id}")
    async def heartbeat_session_output(session_id: str, output_id: str) -> dict:
        """Heartbeat: the device still plays the channel. 404: it was dropped (report it again)."""
        session_or_404(session_id)
        output = session_manager.touch_output(session_id, output_id)
        if output is None:
            raise HTTPException(status_code=404, detail="Output not found")
        return output_answer(output, session_manager.get_session_channel(session_id))

    @router.delete("/sessions/{session_id}/outputs/{output_id}")
    async def remove_session_output(session_id: str, output_id: str) -> dict:
        """The device stopped playing the channel (the mix stops too when nothing else keeps it)."""
        session_or_404(session_id)
        if session_manager.remove_output(session_id, output_id) is None:
            raise HTTPException(status_code=404, detail="Output not found")
        return {"removed": True}

    # --- A speaker's own volume and mute (G6, core/speaker_live.py) ---

    def speaker_or_404(speaker_id: str):
        if ha_registry is None or ha_registry.get_speaker(speaker_id) is None:
            raise HTTPException(status_code=404, detail="Speaker not found")

    @router.put("/speakers/{speaker_id}/volume")
    async def set_speaker_volume(speaker_id: str, request: SpeakerVolumeRequest) -> dict:
        """Set one speaker's volume now (0-100). Channels' volumes are unchanged."""
        speaker_or_404(speaker_id)
        if not await speaker_live.set_volume(speaker_id, request.volume):
            raise HTTPException(status_code=502, detail="The speaker didn't take the change")
        return {"speaker_id": speaker_id, "volume": request.volume}

    @router.put("/speakers/{speaker_id}/muted")
    async def set_speaker_muted(speaker_id: str, request: SpeakerMutedRequest) -> dict:
        """Mute or unmute one speaker now. Channels' speakers-muted switches are unchanged."""
        speaker_or_404(speaker_id)
        if not await speaker_live.set_muted(speaker_id, request.muted):
            raise HTTPException(status_code=502, detail="The speaker didn't take the change")
        return {"speaker_id": speaker_id, "muted": request.muted}


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
