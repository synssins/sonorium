"""
The console credential: a token for the program that runs Sonorium (its
host's console or tray app), switched on by SONORIUM_CONSOLE_TOKEN_FILE.

    SONORIUM_CONSOLE_TOKEN_FILE=<path>

At start (ensure()), if the file is missing or its token isn't the one whose
hash is stored, a new random "sc_..." token is made: its SHA-256 replaces the
old one in security.json (token kind "console") and the token itself is
written to the file, atomically, as one line of UTF-8 with nothing else. So
deleting the file and restarting makes a new one. The host protects the file
(its folder's permissions).

The token is accepted only from this computer (a loopback address: 127.0.0.1,
::1, ::ffff:127.0.0.1; the real peer, never a forwarded header) as
"Authorization: Bearer sc_...", with an admin's rights (Principal kind
"console"). It is not a key: no API lists or revokes it, and switching keys
off doesn't affect it. Without the variable, an "sc_" token is refused.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Optional, Union

from sonorium.auth import permissions as P
from sonorium.auth.principal import Principal, Reject
from sonorium.obs import logger

ENV = "SONORIUM_CONSOLE_TOKEN_FILE"
PREFIX = "sc_"
KIND = "console"


def token_file() -> Optional[Path]:
    value = (os.environ.get(ENV) or "").strip()
    return Path(value) if value else None


def is_loopback(client: str) -> bool:
    """A connection from this computer (an IPv4-mapped IPv6 loopback address too)."""
    try:
        address = ipaddress.ip_address((client or "").split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(address.is_loopback or (mapped is not None and mapped.is_loopback))


def _read(path: Path) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text if text.startswith(PREFIX) else None


def _write(path: Path, token: str) -> None:
    """One line, UTF-8, nothing else: a temp file in the same folder, then replaced."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(token)
    os.replace(tmp, path)


def ensure(service) -> Optional[str]:
    """
    Start-up: keep the file's token if it is the stored one, else make a new
    one. Returns the token's record id, or None when the variable isn't set.
    """
    path = token_file()
    if path is None:
        return None
    tokens = service.tokens
    current = _read(path)
    if current:
        found = tokens.verify(current, KIND, touch=False)
        if found:
            return found[0]
    tokens.revoke_where(lambda r: r.get("kind") == KIND)
    token_id, token = tokens.issue(KIND, prefix=PREFIX, name="Console", owner="host")
    try:
        _write(path, token)
    except OSError as e:
        tokens.revoke(token_id)
        logger.error(f"Console credential: could not write {path}: {e}")
        return None
    logger.info(f"Console credential: a new one was written to {path}")
    return token_id


def resolve(service, client: str, listener: str, token: str) -> Union[Principal, Reject]:
    """The caller for an "sc_" bearer token: the console, or a refusal (401)."""
    refused = Reject(401, {"detail": "This console credential is not valid here."})
    if token_file() is None or listener == "ingress" or not is_loopback(client):
        return refused
    if not service.tokens.verify(token, KIND, touch=False):
        return refused
    return Principal("console", P.ALL_PERMS, listener, role="admin", token_given=True)
