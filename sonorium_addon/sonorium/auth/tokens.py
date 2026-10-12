"""
Tokens: one implementation for every secret Sonorium hands out.

    linked phones      "sd_..."   (core pairing)
    browser sessions   "ss_..."   (the Users plugin)
    keys               "sk_..."   (the Integration plugin)
    plugin triggers and the event stream: older tokens without a prefix,
                       one per scope ("plugin:<id>", "trigger:<id>/<trigger>", "events")

- A token is 32 random bytes (secrets.token_urlsafe) after its prefix. It is
  given out once; only its SHA-256 is stored (no slow hash is needed for
  random values this long), so a copy of the file gives no token away.
- Lookups are by hash; scope checks compare hashes in constant time.
- A record can expire (an absolute time) and lapse when unused for
  `idle` seconds; "last used" is kept, written at most once a minute.
- Revoking deletes the record: the next request with the token fails.
- Tokens never go into logs: install_log_redaction() masks "token=..." in the
  web server's log lines (simple devices put a key in the URL).

Records live in a JsonFile's "tokens" section: {id: {kind, hash, scope,
user_id, name, data, owner, created, expires, idle, last_used}}. Older files
kept {scope: {hash, created}}; they're read as scope records.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

TOKEN_BYTES = 32
TOUCH_SAVE_SECONDS = 60
_REDACT = re.compile(r"((?:^|[?&])token=)[^&\s\"']+", re.IGNORECASE)
_REDACT_PATH = re.compile(r"(/api/notify/)[A-Za-z0-9_-]+")  # a notification rule's webhook secret


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def ts(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


class TokenService:
    def __init__(self, file, section: str = "tokens", clock: Callable[[], float] = time.time):
        """file: a JsonFile (auth/secrets.py), private."""
        self.file = file
        self.section_name = section
        self.clock = clock
        self._last_save = 0.0
        self._dirty = False
        self._migrate()
        self._reindex()

    # ------------------------------------------------------------ storage

    @property
    def records(self) -> dict:
        return self.file.section(self.section_name)

    def _migrate(self) -> None:
        """Older files: {scope: {"hash", "created"}} -> scope records."""
        records = self.records
        old = [k for k, v in records.items() if isinstance(v, dict) and "kind" not in v and "hash" in v]
        for scope in old:
            entry = records.pop(scope)
            records["t_" + secrets.token_hex(8)] = {"kind": "scope", "scope": scope, "hash": entry["hash"],
                                                     "created": entry.get("created"), "owner": "plugins"}
        if old:
            self.save()

    def _reindex(self) -> None:
        self._by_hash = {r["hash"]: i for i, r in self.records.items() if isinstance(r, dict) and r.get("hash")}

    def save(self) -> None:
        self.file.save()
        self._last_save = self.clock()
        self._dirty = False

    def flush(self) -> None:
        if self._dirty:
            self.save()

    def _touched(self) -> None:
        self._dirty = True
        if self.clock() - self._last_save >= TOUCH_SAVE_SECONDS:
            self.save()

    # ------------------------------------------------------------ records

    def issue(self, kind: str, *, prefix: str = "", scope: Optional[str] = None, user_id: Optional[str] = None,
              name: str = "", data: Optional[dict] = None, owner: str = "core", expires: Optional[float] = None,
              idle: Optional[int] = None) -> tuple[str, str]:
        """A new token. Returns (record id, token); the token can't be read back later."""
        token = prefix + secrets.token_urlsafe(TOKEN_BYTES)
        token_id = "t_" + secrets.token_hex(8)
        now = self.clock()
        self.records[token_id] = {
            "kind": kind, "hash": hash_token(token), "scope": scope, "user_id": user_id, "name": name,
            "data": dict(data or {}), "owner": owner, "created": iso(now), "expires": iso(expires),
            "idle": idle, "last_used": iso(now) if idle else None,
        }
        self._by_hash[self.records[token_id]["hash"]] = token_id
        self.save()
        return token_id, token

    def _expired(self, record: dict, now: float) -> bool:
        expires = ts(record.get("expires"))
        if expires is not None and now >= expires:
            return True
        idle = record.get("idle")
        return bool(idle) and now - (ts(record.get("last_used")) or now) > idle

    def verify(self, token: Optional[str], kind: Optional[str] = None, *, touch: bool = True) -> Optional[tuple[str, dict]]:
        """(id, record) for a valid token (of that kind), else None. Expired records are removed."""
        if not isinstance(token, str) or not token:
            return None
        token_id = self._by_hash.get(hash_token(token))
        record = self.records.get(token_id) if token_id else None
        if record is None or (kind is not None and record.get("kind") != kind):
            return None
        now = self.clock()
        if self._expired(record, now):
            self.revoke(token_id)
            return None
        if touch and now - (ts(record.get("last_used")) or 0) > 30:
            record["last_used"] = iso(now)
            self._touched()
        return token_id, record

    def get(self, token_id: str) -> Optional[dict]:
        return self.records.get(token_id)

    def update(self, token_id: str, **fields) -> Optional[dict]:
        record = self.records.get(token_id)
        if record is None:
            return None
        for key, value in fields.items():
            if key in ("hash", "kind", "created"):
                continue
            record[key] = iso(value) if key == "expires" and isinstance(value, (int, float)) else value
        self.save()
        return record

    def revoke(self, token_id: str) -> bool:
        record = self.records.pop(token_id, None)
        if record is None:
            return False
        self._by_hash.pop(record.get("hash"), None)
        self.save()
        return True

    def revoke_where(self, test: Callable[[dict], bool]) -> int:
        ids = [i for i, r in self.records.items() if isinstance(r, dict) and test(r)]
        for token_id in ids:
            record = self.records.pop(token_id)
            self._by_hash.pop(record.get("hash"), None)
        if ids:
            self.save()
        return len(ids)

    def list(self, kind: Optional[str] = None, user_id: Optional[str] = None) -> Iterable[tuple[str, dict]]:
        now = self.clock()
        for token_id, record in list(self.records.items()):
            if not isinstance(record, dict) or (kind and record.get("kind") != kind):
                continue
            if user_id is not None and record.get("user_id") != user_id:
                continue
            if self._expired(record, now):
                continue
            yield token_id, record

    # ------------------------------------------------------------ one token per scope

    def _scope_id(self, scope: str) -> Optional[str]:
        return next((i for i, r in self.records.items()
                     if isinstance(r, dict) and r.get("kind") == "scope" and r.get("scope") == scope), None)

    def create_scoped(self, scope: str, owner: str = "plugins") -> str:
        """Make (or replace) the scope's token. Returns it; it can't be read back later."""
        old = self._scope_id(scope)
        if old:
            self.records.pop(old, None)
        _, token = self.issue("scope", scope=scope, owner=owner)
        self._reindex()
        return token

    def revoke_scope(self, scope: str) -> bool:
        token_id = self._scope_id(scope)
        return self.revoke(token_id) if token_id else False

    def revoke_scope_prefix(self, prefix: str) -> int:
        return self.revoke_where(lambda r: r.get("kind") == "scope" and str(r.get("scope", "")).startswith(prefix))

    def scope_info(self, scope: str) -> Optional[dict]:
        """{"created"} if the scope has a token, else None (never the hash)."""
        token_id = self._scope_id(scope)
        return {"created": self.records[token_id].get("created")} if token_id else None

    def check_scoped(self, scope: str, token: Optional[str]) -> bool:
        """Whether token is the scope's token (constant time)."""
        token_id = self._scope_id(scope)
        expected = self.records[token_id]["hash"] if token_id else ""
        given = hash_token(token) if isinstance(token, str) and token else ""
        # Always compare, so a missing token takes as long as a wrong one
        same = hmac.compare_digest(given.encode(), (expected or "0" * 64).encode())
        ok = bool(token_id) and bool(given) and same
        if ok:
            now = self.clock()
            record = self.records[token_id]
            if now - (ts(record.get("last_used")) or 0) > 30:
                record["last_used"] = iso(now)
                self._touched()
        return ok


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
    return _REDACT_PATH.sub(r"\1***", _REDACT.sub(r"\1***", text))


class RedactTokens(logging.Filter):
    """Masks token=... in log records (e.g. uvicorn's access log lines)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.args, tuple):
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
            if isinstance(record.msg, str) and ("token=" in record.msg.lower() or "/api/notify/" in record.msg):
                record.msg = redact(record.msg)
        except Exception:
            pass
        return True


def install_log_redaction() -> None:
    """Mask tokens in the web server's logs (safe to call more than once)."""
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        log = logging.getLogger(name)
        if not any(isinstance(f, RedactTokens) for f in log.filters):
            log.addFilter(RedactTokens())
