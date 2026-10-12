"""
Access tokens for webhook triggers and the event stream: the core token
service (sonorium/auth/tokens.py) with one token per scope, kept in
plugin_secrets.json as before.

Scopes: "plugin:<id>" (every trigger of that plugin), "trigger:<id>/<trigger>"
(one trigger), "events" (GET /api/events/stream). Keys made on Settings >
Keys (auth/keys.py) with "Run triggers" or "Read events" work too.
"""

from __future__ import annotations

from typing import Optional

from sonorium.auth.tokens import (  # noqa: F401  (imported from here before)
    TOKEN_BYTES, RedactTokens, TokenService, install_log_redaction, redact, token_from_request,
)


class TokenStore:
    """The plugin framework's view of the token service: one token per scope."""

    def __init__(self, store):
        self.store = store
        self.service = TokenService(store.secrets)

    def create(self, scope: str) -> str:
        """Make (or replace) the scope's token. Returns it; it can't be read back later."""
        return self.service.create_scoped(scope)

    def revoke(self, scope: str) -> bool:
        return self.service.revoke_scope(scope)

    def revoke_prefix(self, prefix: str) -> int:
        return self.service.revoke_scope_prefix(prefix)

    def info(self, scope: str) -> Optional[dict]:
        """{"created"} if the scope has a token, else None (never the hash)."""
        return self.service.scope_info(scope)

    def check(self, scope: str, token: Optional[str]) -> bool:
        """Whether token is the scope's token (constant time)."""
        return self.service.check_scoped(scope, token)
