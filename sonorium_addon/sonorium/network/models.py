"""Network speaker data model (ported from the Windows app's network_speakers.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SpeakerType(str, Enum):
    CHROMECAST = "chromecast"
    SONOS = "sonos"
    DLNA = "dlna"
    AIRPLAY = "airplay"
    HEOS = "heos"


# Shown after the speaker name in the UI, e.g. "Kitchen (Sonos)"
TYPE_LABELS = {
    SpeakerType.CHROMECAST: "Cast",
    SpeakerType.SONOS: "Sonos",
    SpeakerType.DLNA: "DLNA",
    SpeakerType.AIRPLAY: "AirPlay",
    SpeakerType.HEOS: "HEOS",
}

# When one device answers to several protocols (a Sonos is also a DLNA
# renderer and an AirPlay target), only the first type in this order is kept.
# DLNA ranks above HEOS (beta) and AirPlay (RAOP push needs Sonorium to stay
# connected; DLNA lets the speaker pull the stream itself).
TYPE_PRIORITY = [
    SpeakerType.SONOS,
    SpeakerType.CHROMECAST,
    SpeakerType.DLNA,
    SpeakerType.HEOS,
    SpeakerType.AIRPLAY,
]


class SpeakerStatus(str, Enum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass
class NetworkSpeaker:
    """A speaker found on the network. `id` is the Sonorium ID (net:<type>:<device id>)."""
    id: str
    name: str
    speaker_type: SpeakerType
    host: str
    port: int = 0
    model: Optional[str] = None
    manufacturer: Optional[str] = None
    is_group: bool = False
    extra: dict = field(default_factory=dict)
    # Unique device identifier (survives IP changes) when the protocol has one
    uuid: Optional[str] = None
    status: SpeakerStatus = SpeakerStatus.UNKNOWN
    last_seen: Optional[str] = None  # ISO timestamp

    @property
    def display_name(self) -> str:
        return f"{self.name} ({TYPE_LABELS.get(self.speaker_type, self.speaker_type.value)})"

    @property
    def available(self) -> bool:
        return self.status == SpeakerStatus.AVAILABLE

    def to_dict(self) -> dict:
        """For API responses."""
        data = self.to_storage_dict()
        data["status"] = self.status.value
        data["available"] = self.available
        data["display_name"] = self.display_name
        return data

    def to_storage_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.speaker_type.value,
            "host": self.host,
            "port": self.port,
            "model": self.model,
            "manufacturer": self.manufacturer,
            "is_group": self.is_group,
            "uuid": self.uuid,
            "extra": self.extra,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_storage_dict(cls, data: dict) -> "NetworkSpeaker":
        return cls(
            id=data["id"],
            name=data["name"],
            speaker_type=SpeakerType(data["type"]),
            host=data["host"],
            port=data.get("port") or 0,
            model=data.get("model"),
            manufacturer=data.get("manufacturer"),
            is_group=bool(data.get("is_group", False)),
            extra=data.get("extra") or {},
            uuid=data.get("uuid"),
            status=SpeakerStatus.UNKNOWN,
            last_seen=data.get("last_seen"),
        )
