"""
Plugin API versions and capabilities: whether a plugin can run on this
Sonorium, decided from its manifest before any of its code is loaded.

    "api_version": "1.1"                  the plugin API it was written for
    "requires": ["audio.sounds", "mqtt"]  services it can't work without

The plugin API is versioned MAJOR.MINOR:

- MINOR goes up when services or fields are added. A plugin written for 1.0
  runs on 1.1 and every later 1.x: nothing it uses is renamed or removed.
- MAJOR goes up only if something has to change in a way old plugins would
  notice. A plugin for another major version is refused with a message saying
  which one it needs.

A manifest without api_version is a 1.0 plugin (everything before
versioning). A plugin that needs a newer minor version, or a capability this
Sonorium doesn't have (or this install doesn't offer), is refused with a
clear message; it shows in Settings > Plugins with that message, and none of
its code runs.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

API_VERSION = "1.2"
API_MAJOR, API_MINOR = (int(p) for p in API_VERSION.split("."))

# capability -> (the API version it came with, what it is)
CAPABILITIES: dict[str, tuple[str, str]] = {
    "events": ("1.0", "Follow and publish events"),
    "actions": ("1.0", "Control channels, themes and speakers"),
    "scheduler": ("1.0", "Persistent scheduled jobs"),
    "triggers": ("1.0", "Webhook triggers with tokens"),
    "pages": ("1.0", "Left-menu pages drawn from a schema"),
    "connections": ("1.0", "Sections in Settings > Connection"),
    "audio.sounds": ("1.1", "Play sounds over channels, with ducking"),
    "audio.ramps": ("1.1", "Gradual volume ramps on channels"),
    "audio.outputs": ("1.1", "Audio output types; in 1.2 their devices are speakers, local types"),
    "audio.tts": ("1.1", "Text to speech providers"),
    "commands": ("1.1", "Command maps for remotes and other inputs"),
    "mqtt": ("1.1", "Publish and subscribe on Sonorium's MQTT broker"),
    "homeassistant": ("1.1", "Call Home Assistant's API"),
    "packages.pip": ("1.1", "Install Python packages (Settings > Advanced > Plugin packages)"),
    "packages.binaries": ("1.1", "Ship programs per platform (e.g. ffmpeg)"),
    "attributes": ("1.2", "Declared attributes on themes, tracks, files, presets, channels, speakers and users"),
    "ui": ("1.2", "Add fields, buttons, menu items, badges, columns and pages to any screen (GET /api/ui/targets)"),
    "timeline": ("1.2", "Cues: which sound is heard on which speaker and when, ahead of time"),
    "audio.tap": ("1.2", "Read-only copies of what a channel plays, or of one track, as it plays"),
    "audio.decode": ("1.2", "A theme's files decoded by Sonorium, for analysis"),
    "automation": ("1.2", "\"Do\" actions and \"only if\" conditions for command maps"),
    "permissions": ("1.2", "A plugin lists what it uses; the user approves it; the rest is refused"),
    "http": ("1.2", "HTTP routes under /api/plugins/<id>/http/, an ability each"),
    "ha.entities": ("1.2", "Home Assistant entities declared as data, published by Sonorium"),
    "devices": ("1.2", "Devices of any kind (lights, displays, ...): listed, named, placed and assigned by Sonorium"),
}

_VERSION = re.compile(r"^\s*(\d+)\.(\d+)\s*$")


def parse(version) -> Optional[tuple[int, int]]:
    if version is None:
        return (1, 0)
    match = _VERSION.match(str(version))
    return (int(match.group(1)), int(match.group(2))) if match else None


def check(manifest: dict, available: Optional[Iterable[str]] = None) -> Optional[str]:
    """None if the plugin can run here, else why not (a sentence for the user)."""
    name = manifest.get("name") or manifest.get("id") or "This plugin"
    wanted = parse(manifest.get("api_version"))
    if wanted is None:
        return f"{name} has an api_version Sonorium can't read ({manifest.get('api_version')!r}; e.g. \"1.1\")."
    major, minor = wanted
    if major > API_MAJOR or (major == API_MAJOR and minor > API_MINOR):
        return (f"{name} needs plugin API {major}.{minor}; this Sonorium has {API_VERSION}. "
                f"Update Sonorium to use it.")
    if major < API_MAJOR:
        return (f"{name} was made for plugin API {major}.x, which this Sonorium ({API_VERSION}) no longer runs. "
                f"Look for a newer version of the plugin.")
    requires = manifest.get("requires") or []
    if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires):
        return f"{name}: 'requires' must be a list of capability names."
    unknown = [r for r in requires if r not in CAPABILITIES]
    if unknown:
        return (f"{name} needs {', '.join(unknown)}, which this Sonorium ({API_VERSION}) doesn't have. "
                f"Update Sonorium to use it.")
    if available is not None:
        have = set(available)
        missing = [r for r in requires if r not in have]
        if missing:
            return (f"{name} needs {', '.join(CAPABILITIES[r][1].lower() for r in missing)}, "
                    f"which this install doesn't offer.")
    return None


def describe() -> dict:
    """For GET /api/plugin-framework: the API version and every capability."""
    return {"api_version": API_VERSION,
            "capabilities": {k: {"since": v[0], "description": v[1]} for k, v in CAPABILITIES.items()}}
