"""
Text to speech providers: plugins that turn a message into audio
(Home Assistant's TTS through the Announcements plugin, a local Piper, a
cloud voice). Every announcement with text uses them (core/announce.py).

    self.audio.register_tts("piper", "Piper (local)", self.speak, ready=lambda: True)

    async def speak(self, message, language=None, voice=None, options=None) -> bytes:
        return wav_or_mp3_bytes

The first provider registered is the default; a request may name one.
ready (optional) says whether it can speak now: Home Assistant's needs
Home Assistant linked. Without it, an announcement plays only its chime.
"""
from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable, Optional

PROVIDER_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SPEAK_TIMEOUT_SECONDS = 60


class TtsError(RuntimeError):
    """No provider, or the provider failed (the message is shown to the caller)."""


class TtsProviders:
    def __init__(self):
        self._providers: dict[str, dict] = {}

    def register(self, owner: str, provider_id: str, title: str, speak: Callable[..., Awaitable[bytes]],
                 ready: Optional[Callable[[], bool]] = None) -> None:
        if not PROVIDER_ID.match(str(provider_id)):
            raise ValueError("A provider id is 1-64 letters, digits, '_', '-' or '.'")
        self._providers[provider_id] = {"owner": owner, "title": str(title or provider_id), "speak": speak,
                                        "ready": ready}

    def ready(self, provider: Optional[str] = None) -> bool:
        """Whether the provider (default: the first) can speak now."""
        if provider is None:
            provider = next(iter(self._providers), None)
        entry = self._providers.get(provider) if provider is not None else None
        if entry is None:
            return False
        try:
            return entry["ready"] is None or bool(entry["ready"]())
        except Exception:
            return False

    def unregister_owner(self, owner: str) -> None:
        for pid in [p for p, v in self._providers.items() if v["owner"] == owner]:
            self._providers.pop(pid, None)

    def providers(self) -> list[dict]:
        return [{"id": pid, "title": v["title"], "plugin_id": v["owner"]} for pid, v in self._providers.items()]

    async def speak(self, message: str, provider: Optional[str] = None, **options) -> bytes:
        if not self._providers:
            raise TtsError("No text to speech is available (the Announcements plugin provides Home Assistant's)")
        if provider is None:
            provider = next(iter(self._providers))
        entry = self._providers.get(provider)
        if entry is None:
            raise TtsError(f"No text to speech provider '{provider}'")
        try:
            return await asyncio.wait_for(entry["speak"](message, **options), SPEAK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise TtsError("Text to speech took too long")
        except TtsError:
            raise
        except Exception as e:
            raise TtsError(f"Text to speech failed: {getattr(e, 'message', None) or e}")
