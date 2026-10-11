"""
Access tokens for webhook triggers and the event stream.

- A token is 32 random bytes (secrets.token_urlsafe). It is shown once, when
  it is made; only its SHA-256 hash is stored (plugin_secrets.json), so a
  copy of the file doesn't give the token away. Random tokens this long need
  no slow password hash.
- Checking compares hashes with hmac.compare_digest (constant time).
- Tokens never go into logs: install_log_redaction() masks "token=..." in
  the web server's access log (GET triggers carry it in the URL).

Scopes: "plugin:<id>" (every trigger of that plugin), "trigger:<id>/<trigger>"
(one trigger), "events" (GET /api/events/stream).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Optional

TOKEN_BYTES = 32
_REDACT = re.compile(r"((?:^|[?&])token=)[^&\s\"']+", re.IGNORECASE)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenStore:
    """Token hashes in the secrets file's "tokens" section: scope -> {"hash", "created"}."""

    def __init__(self, store):
        self.store = store

    def _tokens(self) -> dict:
        return self.store.secrets.section("tokens")

    def create(self, scope: str) -> str:
        """Make (or replace) the scope's token. Returns it; it can't be read back later."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self._tokens()[scope] = {"hash": _hash(token), "created": datetime.now(timezone.utc).isoformat()}
        self.store.secrets.save()
        return token

    def revoke(self, scope: str) -> bool:
        if self._tokens().pop(scope, None) is None:
            return False
        self.store.secrets.save()
        return True

    def revoke_prefix(self, prefix: str) -> int:
        scopes = [s for s in self._tokens() if s.startswith(prefix)]
        for scope in scopes:
            self._tokens().pop(scope, None)
        if scopes:
            self.store.secrets.save()
        return len(scopes)

    def info(self, scope: str) -> Optional[dict]:
        """{"created"} if the scope has a token, else None (never the hash)."""
        entry = self._tokens().get(scope)
        return {"created": entry.get("created")} if entry else None

    def check(self, scope: str, token: Optional[str]) -> bool:
        """Whether token is the scope's token (constant time)."""
        entry = self._tokens().get(scope)
        given = _hash(token) if isinstance(token, str) and token else ""
        expected = entry.get("hash", "") if entry else ""
        # Always compare, so a missing token takes as long as a wrong one
        same = hmac.compare_digest(given.encode(), (expected or "0" * 64).encode())
        return bool(entry) and bool(given) and same


def token_from_request(request) -> Optional[str]:
    """The token a request carries: Authorization: Bearer, X-Sonorium-Token, or ?token=."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    header = request.headers.get("x-sonorium-token")
    if header:
        return header.strip()
    return request.query_params.get("token") or None


def redact(text: str) -> str:
    return _REDACT.sub(r"\1***", text)


class RedactTokens(logging.Filter):
    """Masks token=... in log records (e.g. uvicorn's access log lines)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.args, tuple):
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
            if isinstance(record.msg, str) and "token=" in record.msg.lower():
                record.msg = redact(record.msg)
        except Exception:
            pass
        return True


def install_log_redaction() -> None:
    """Mask tokens in the web server's logs (safe to call more than once)."""
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        log = logging.getLogger(name)
        if not any(isinstance(f, RedactTokens) for f in log.filters):
            log.addFilter(RedactTokens())
