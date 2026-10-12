"""
Sonorium Core Module

Contains data models, state management, and managers.

The names below are imported when first used, so importing one module of the
package (core.transitions, core.theme_presets, ... for the playback engine,
sonorium/engine) doesn't load the session manager and everything it needs.
"""

_LAZY = {
    "NameSource": "sonorium.core.state",
    "SonoriumSettings": "sonorium.core.state",
    "SpeakerSelection": "sonorium.core.state",
    "SpeakerGroup": "sonorium.core.state",
    "Session": "sonorium.core.state",
    "SonoriumState": "sonorium.core.state",
    "StateStore": "sonorium.core.state",
    "SessionManager": "sonorium.core.session_manager",
    "GroupManager": "sonorium.core.group_manager",
}


def __getattr__(name):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


__all__ = [
    # Data models
    "NameSource",
    "SonoriumSettings",
    "SpeakerSelection",
    "SpeakerGroup",
    "Session",
    "SonoriumState",
    "StateStore",
    # Managers
    "SessionManager",
    "GroupManager",
]
