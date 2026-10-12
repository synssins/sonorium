"""
Outputs the app reports: "Cast device X plays channel N" (docs/APP_API.md, G2).

The phone hands a Cast device (or anything else it controls) the channel's
plain stream URL, then tells Sonorium about it with POST
/api/sessions/{id}/outputs. While an output is reported:

- the channel's mix runs, with or without its speakers, as if someone
  listened (it isn't stopped when the phone's own stream goes);
- the device plays the speakers' stream, so the channel's speakers-muted
  switch silences it like any speaker;
- the phone sends a heartbeat (PUT .../outputs/{output_id}) every
  HEARTBEAT_SECONDS; an output not heard from for EXPIRE_SECONDS is dropped;
- stopping or pausing the channel, deleting it, or the channel going idle
  (nothing pulled its audio for 90 s) drops its outputs too.

Every add and drop is published: channel.output_added / channel.output_removed.
Outputs live in memory only: a restart forgets them (the app sees a new
boot_id and reports again).
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

HEARTBEAT_SECONDS = 60
EXPIRE_SECONDS = 3 * HEARTBEAT_SECONDS  # three missed heartbeats
MAX_PER_CHANNEL = 10

KIND = re.compile(r"^[a-z][a-z0-9_-]{0,19}$")
NAME_MAX = 60
FIELD_MAX = 100


class OutputError(ValueError):
    """A bad output report (the message is shown to the caller)."""


def clean_report(data: dict) -> dict:
    """The fields of a POST .../outputs body, checked: {kind, name, address, receiver_id}."""
    kind = data.get("kind", "cast")
    if not isinstance(kind, str) or not KIND.match(kind.strip().lower()):
        raise OutputError("kind must be a short word such as \"cast\"")
    out = {"kind": kind.strip().lower()}
    name = data.get("name")
    if name is not None and not isinstance(name, str):
        raise OutputError("name must be text")
    out["name"] = (name or "").strip()[:NAME_MAX] or ("Cast device" if out["kind"] == "cast" else "Device")
    for key in ("address", "receiver_id"):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            raise OutputError(f"{key} must be text")
        value = (value or "").strip()
        if len(value) > FIELD_MAX:
            raise OutputError(f"{key} is too long")
        out[key] = value or None
    return out


@dataclass
class Output:
    output_id: str
    session_id: str
    kind: str
    name: str
    address: Optional[str] = None
    receiver_id: Optional[str] = None
    device_id: Optional[str] = None
    device_name: Optional[str] = None  # the linked phone that reported it ("cast from Pixel 8")
    since: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen: float = 0.0  # the registry's clock

    def same_device(self, other: "Output") -> bool:
        """Another report of the same receiver (its id, else its kind and address)."""
        if self.receiver_id and other.receiver_id:
            return self.receiver_id == other.receiver_id
        return bool(self.address) and self.kind == other.kind and self.address == other.address

    def to_dict(self, now: float) -> dict:
        return {"output_id": self.output_id, "kind": self.kind, "name": self.name, "address": self.address,
                "receiver_id": self.receiver_id, "since": self.since, "by_device_id": self.device_id,
                "by_device_name": self.device_name,
                "expires_in": max(0, int(round(self.last_seen + EXPIRE_SECONDS - now)))}


class OutputRegistry:
    """The reported outputs of every channel (session id -> {output id: Output})."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._by_session: dict[str, dict[str, Output]] = {}

    def add(self, session_id: str, report: dict, device_id: Optional[str] = None) -> tuple[Output, list[Output]]:
        """
        Record an output (report: clean_report()'s fields). The same receiver
        reported again replaces its earlier report, on this channel or another
        (a device plays one stream). Returns (the output, the reports it replaced).
        """
        new = Output(output_id="o_" + secrets.token_hex(6), session_id=session_id, device_id=device_id,
                     last_seen=self.clock(), **report)
        replaced = [o for outputs in self._by_session.values() for o in outputs.values() if o.same_device(new)]
        for old in replaced:
            self.remove(old.session_id, old.output_id)
        outputs = self._by_session.setdefault(session_id, {})
        if len(outputs) >= MAX_PER_CHANNEL:
            raise OutputError(f"A channel can have at most {MAX_PER_CHANNEL} outputs")
        outputs[new.output_id] = new
        return new, replaced

    def get(self, session_id: str, output_id: str) -> Optional[Output]:
        return self._by_session.get(session_id, {}).get(output_id)

    def touch(self, session_id: str, output_id: str) -> Optional[Output]:
        """A heartbeat. None: no such output (it expired or was dropped)."""
        output = self.get(session_id, output_id)
        if output is not None:
            output.last_seen = self.clock()
        return output

    def remove(self, session_id: str, output_id: str) -> Optional[Output]:
        outputs = self._by_session.get(session_id)
        output = outputs.pop(output_id, None) if outputs else None
        if outputs is not None and not outputs:
            self._by_session.pop(session_id, None)
        return output

    def drop(self, session_id: str) -> list[Output]:
        """Every output of a channel, removed."""
        return list(self._by_session.pop(session_id, {}).values())

    def of(self, session_id: str) -> list[Output]:
        return sorted(self._by_session.get(session_id, {}).values(), key=lambda o: o.since)

    def held(self, session_id: str) -> bool:
        """The channel has an output (its mix must keep running)."""
        return bool(self._by_session.get(session_id))

    def expired(self) -> list[Output]:
        """Outputs not heard from for EXPIRE_SECONDS, removed."""
        now = self.clock()
        gone = [o for outputs in self._by_session.values() for o in outputs.values()
                if now - o.last_seen > EXPIRE_SECONDS]
        for output in gone:
            self.remove(output.session_id, output.output_id)
        return gone

    def describe(self, session_id: str) -> list[dict]:
        now = self.clock()
        return [o.to_dict(now) for o in self.of(session_id)]
