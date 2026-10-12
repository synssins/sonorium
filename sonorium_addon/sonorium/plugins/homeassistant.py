"""
Home Assistant for plugins: its REST API through the connection Sonorium
already has (the Supervisor's proxy on the Home Assistant app, the saved URL
and token on Docker and Windows), and its text to speech.

    ha = self.homeassistant
    if ha.available:
        status, states = await ha.request("GET", "/api/states")
        audio = await ha.tts("Dinner is ready", engine="tts.piper")

Paths are Home Assistant API paths ("/api/..."). The token never reaches the
plugin, and requests go only to Home Assistant.
"""
from __future__ import annotations

from typing import Callable, Optional

from sonorium.obs import logger

TIMEOUT_SECONDS = 20
MAX_AUDIO_BYTES = 32 * 1024 * 1024


class HomeAssistantError(RuntimeError):
    """Home Assistant couldn't be reached or refused (the message is shown to the caller)."""


class HomeAssistant:
    """
    config(): (api base, token), e.g. ("http://supervisor/core/api", "...");
    the base ends in /api. Read on every call, so a connection saved later works.
    """

    def __init__(self, config: Callable[[], tuple[str, str]], configured: Callable[[], bool] = lambda: True,
                 client_factory=None):
        self._config = config
        self._configured = configured
        self._client_factory = client_factory  # tests: an httpx.AsyncClient stand-in

    @property
    def available(self) -> bool:
        try:
            base, token = self._config()
        except Exception:
            return False
        return bool(self._configured() and base and token)

    def _root(self) -> tuple[str, str]:
        if not self.available:
            raise HomeAssistantError("Home Assistant isn't connected")
        base, token = self._config()
        base = base.rstrip("/")
        return (base[:-4] if base.endswith("/api") else base), token

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory()
        import httpx
        return httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False)

    @staticmethod
    def _check_path(path: str) -> str:
        if not isinstance(path, str) or not path.startswith("/api/") or ".." in path or "://" in path:
            raise HomeAssistantError("Only Home Assistant API paths (/api/...) can be called")
        return path

    async def request(self, method: str, path: str, json=None) -> tuple[int, object]:
        """(status, JSON or text) of a call to Home Assistant's API."""
        root, token = self._root()
        path = self._check_path(path)
        async with self._client() as client:
            try:
                response = await client.request(method.upper(), root + path, json=json,
                                                headers={"Authorization": f"Bearer {token}"})
            except Exception as e:
                raise HomeAssistantError(f"Home Assistant didn't answer: {e}")
        try:
            body = response.json()
        except Exception:
            body = response.text
        return response.status_code, body

    async def fetch(self, path: str, limit: int = MAX_AUDIO_BYTES) -> bytes:
        """The bytes at a Home Assistant path (e.g. /api/tts_proxy/<file>)."""
        root, token = self._root()
        path = self._check_path(path)
        async with self._client() as client:
            try:
                response = await client.get(root + path, headers={"Authorization": f"Bearer {token}"})
            except Exception as e:
                raise HomeAssistantError(f"Home Assistant didn't answer: {e}")
        if response.status_code != 200:
            raise HomeAssistantError(f"Home Assistant answered {response.status_code} for {path.split('?')[0]}")
        data = response.content
        if len(data) > limit:
            raise HomeAssistantError("The file from Home Assistant is too large")
        return data

    async def tts_engines(self) -> list[str]:
        """The tts.* entities Home Assistant has (its text to speech engines)."""
        status, states = await self.request("GET", "/api/states")
        if status != 200 or not isinstance(states, list):
            return []
        return sorted(s["entity_id"] for s in states
                      if isinstance(s, dict) and str(s.get("entity_id", "")).startswith("tts."))

    async def tts(self, message: str, engine: Optional[str] = None, language: Optional[str] = None,
                  options: Optional[dict] = None) -> bytes:
        """
        Speak a message with Home Assistant's text to speech; returns the audio
        file (usually MP3). engine: a tts.* entity, or an older integration's
        platform name ("google_translate"); none: the first tts.* entity.
        """
        if not isinstance(message, str) or not message.strip():
            raise HomeAssistantError("Nothing to say")
        if not engine:
            engines = await self.tts_engines()
            if not engines:
                raise HomeAssistantError("Home Assistant has no text to speech engine")
            engine = engines[0]
        body = {"message": message.strip()[:2000]}
        body["engine_id" if engine.startswith("tts.") else "platform"] = engine
        if language:
            body["language"] = language
        if options:
            body["options"] = options
        status, answer = await self.request("POST", "/api/tts_get_url", json=body)
        if status != 200 or not isinstance(answer, dict):
            raise HomeAssistantError(f"Text to speech failed ({status}): {str(answer)[:200]}")
        path = answer.get("path")
        if not path:
            # Older answers only have the full URL: keep its path, fetched through Sonorium's own connection
            from urllib.parse import urlsplit
            parts = urlsplit(answer.get("url") or "")
            path = parts.path + (f"?{parts.query}" if parts.query else "")
        logger.debug(f"TTS: {engine} -> {path}")
        return await self.fetch(path)
