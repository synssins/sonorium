"""
The server's name: Settings > Advanced > Server name (SonoriumSettings.server_name).

GET /api/info "name" and the mDNS TXT name (auth/announce.py) read it through
get(); empty means "Sonorium". set_source() points this module at the settings
store (api.py, web/api_v2.py); without a source (tests, code without a store)
the name is "Sonorium".

No dependencies on the rest of the package.
"""
from __future__ import annotations

import unicodedata
from typing import Callable, Optional

DEFAULT = "Sonorium"  # the name when none is set
MAX_LENGTH = 40       # the longest name PUT /api/settings takes

_source: Optional[Callable[[], str]] = None


def set_source(source: Optional[Callable[[], str]]) -> None:
    """Where the stored name is: a function returning it (None: always the default)."""
    global _source
    _source = source


def _has_control(text: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" for ch in text)


def clean(value) -> str:
    """A name as it's stored: trimmed ("" = the default). ValueError when it isn't text,
    is longer than MAX_LENGTH or has control characters (line breaks, tabs, ...)."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Server name must be text")
    name = value.strip()
    if _has_control(name):
        raise ValueError("Server name can't contain line breaks, tabs or other control characters")
    if len(name) > MAX_LENGTH:
        raise ValueError(f"Server name can be at most {MAX_LENGTH} characters")
    return name


def get() -> str:
    """The server's name: the stored one, or "Sonorium" when it's empty or can't be read."""
    try:
        stored = _source() if _source is not None else ""
    except Exception:
        stored = ""
    if not isinstance(stored, str):
        return DEFAULT
    # A hand-edited state.json: drop control characters rather than announce them
    name = "".join(ch for ch in stored if unicodedata.category(ch) != "Cc").strip()
    return name or DEFAULT
