"""
Sonorium's own floors and areas (Settings > Floors & Areas), for installs
without Home Assistant or with spaces Home Assistant doesn't have.

Stored in settings.local_spaces as
    {"floors": [{"id": "floor_upstairs", "name": "Upstairs"}],
     "areas":  [{"id": "area_den", "name": "Den", "floor_id": "floor_upstairs"}]}

The registry merges them with Home Assistant's by name (see
HARegistry._merge_spaces). No network or Home Assistant calls here.
"""
import re
from typing import Optional


class SpaceError(ValueError):
    """A change that can't be made; the message is shown to the user."""


def _clean_name(name) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise SpaceError("Enter a name")
    if len(name) > 60:
        raise SpaceError("Names can be up to 60 characters")
    return name


def _new_id(prefix: str, name: str, taken: set[str]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or "space"
    candidate, n = f"{prefix}_{slug}", 2
    while candidate in taken:
        candidate, n = f"{prefix}_{slug}_{n}", n + 1
    return candidate


def _spaces(local: Optional[dict]) -> dict:
    local = local if isinstance(local, dict) else {}
    local.setdefault("floors", [])
    local.setdefault("areas", [])
    return local


def _find(items: list[dict], space_id: str) -> dict:
    for item in items:
        if item.get("id") == space_id:
            return item
    raise SpaceError("Not found, or managed in Home Assistant")


def _check_unique(items: list[dict], name: str, except_id: Optional[str] = None):
    if any(i.get("name", "").casefold() == name.casefold() and i.get("id") != except_id for i in items):
        raise SpaceError(f"'{name}' already exists")


def add_floor(local: dict, name) -> dict:
    local, name = _spaces(local), _clean_name(name)
    _check_unique(local["floors"], name)
    floor = {"id": _new_id("floor", name, {f["id"] for f in local["floors"]}), "name": name}
    local["floors"].append(floor)
    return floor


def add_area(local: dict, name, floor_id: Optional[str] = None) -> dict:
    local, name = _spaces(local), _clean_name(name)
    _check_unique(local["areas"], name)
    area = {"id": _new_id("area", name, {a["id"] for a in local["areas"]}), "name": name, "floor_id": floor_id or None}
    local["areas"].append(area)
    return area


def rename(local: dict, kind: str, space_id: str, name) -> dict:
    local, name = _spaces(local), _clean_name(name)
    items = local["floors" if kind == "floor" else "areas"]
    item = _find(items, space_id)
    _check_unique(items, name, except_id=space_id)
    item["name"] = name
    return item


def move_area(local: dict, area_id: str, floor_id: Optional[str]) -> dict:
    area = _find(_spaces(local)["areas"], area_id)
    area["floor_id"] = floor_id or None
    return area


def delete_floor(local: dict, floor_id: str) -> None:
    """Remove a floor; its areas stay, without a floor."""
    local = _spaces(local)
    floor = _find(local["floors"], floor_id)
    local["floors"].remove(floor)
    for area in local["areas"]:
        if area.get("floor_id") == floor_id:
            area["floor_id"] = None


def delete_area(local: dict, area_id: str, speaker_settings: dict) -> None:
    """Remove an area; speakers placed in it go back to no room."""
    local = _spaces(local)
    local["areas"].remove(_find(local["areas"], area_id))
    for speaker_id, settings in list(speaker_settings.items()):
        if settings.get("room") == area_id:
            settings.pop("room")
            if not settings:
                speaker_settings.pop(speaker_id)
