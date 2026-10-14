"""
Playing sounds over channels: announcements, alerts and sound effects
(the mixing itself is core/overlay.py).

    service.play(["kitchen", "office"], "https://.../doorbell.mp3",
                 volume=80, duck={"db": 18}, policy="queue", priority=5,
                 start_stopped=True)

- The sound is decoded once (PyAV), cut at the maximum length (Settings >
  Advanced > Longest sound, or less per call) and added to each channel's
  mix.
- A channel that plays (on its speakers, a browser, an output) gets it at
  once. A stopped channel is skipped, unless start_stopped: then its stream
  runs silent, its speakers are started, the sound plays once they are
  connected (Advanced: "Wait for speakers" at most, then "Lead"), and after it
  (and "Tail") the speakers are stopped and the channel is stopped again. The
  channel never shows as playing for this.
- Speakers muted is respected: a muted channel's speakers don't hear the
  sound (a browser listening does), and a stopped muted channel isn't woken.
- Gates (add_gate) may refuse a sound on a channel, e.g. a zone's quiet hours
  (core/zones.py). A sound with override=True (an admin, Home Assistant,
  MQTT) skips them.

Events: sound.started and sound.ended for each channel (core events
catalogue). Network speakers buffer a few seconds of audio, so what they
play is that much behind: sounds are exact to the stream, not to the room.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from typing import Callable, Optional

from sonorium import playback_values
from sonorium.core import decode
from sonorium.core.overlay import POLICIES, ChannelOverlay, Duck, OverlayError, Sound, cap
from sonorium.obs import logger

MAX_CHANNELS_PER_SOUND = 50
MAX_VOLUME = 200
WAKE_POLL_SECONDS = 0.1


def _now() -> float:
    return time.monotonic()


ANNOUNCEMENTS_OFF = "Announcements are off"

class OverlayService:
    def __init__(self, session_manager, publish: Optional[Callable] = None, rate: int = 44100):
        self.sessions = session_manager
        self.publish = publish or (lambda *a, **k: None)
        self.rate = rate
        self._gates: dict[str, Callable] = {}
        self._waking: dict[str, dict] = {}      # session id -> {"ready": bool, "pending": [Sound]}
        self._tasks: set = set()
        if session_manager is not None:
            session_manager.add_mix_hold(lambda sid: sid in self._waking)

    # --- Settings (Settings > Advanced) ---

    @staticmethod
    def default_duck() -> Duck:
        return Duck(playback_values.get("duck_amount"), playback_values.get("duck_attack"),
                    playback_values.get("duck_release"))

    @staticmethod
    def max_length() -> float:
        return playback_values.get("sound_max_length")

    # --- Gates (zones, quiet hours) ---

    def add_gate(self, owner: str, gate: Callable) -> None:
        """gate(session, request) -> None to allow, or the reason it's refused (text)."""
        self._gates[owner] = gate

    def remove_gate(self, owner: str) -> None:
        self._gates.pop(owner, None)

    def _refused(self, session, request: dict) -> Optional[str]:
        for owner, gate in list(self._gates.items()):
            try:
                reason = gate(session, request)
            except Exception as e:
                logger.warning(f"Sound gate of {owner} failed: {e}")
                continue
            if reason:
                return str(reason)
        return None

    # --- A channel's overlay ---

    def overlay_of(self, channel, session) -> ChannelOverlay:
        overlay = channel.overlay
        if overlay is None:
            overlay = channel.overlay = ChannelOverlay(self.rate)
        overlay.on_event = self._on_event
        return overlay

    def _on_event(self, kind: str, sound: Sound, reason: str) -> None:
        data = {"sound_id": sound.sound_id, "channel_id": sound.meta.get("channel_id"),
                "name": sound.meta.get("name"), "label": sound.meta.get("label", ""),
                "origin": sound.meta.get("origin", "sonorium")}
        if kind == "ended":
            data.update(reason=reason, capped=sound.capped, played=round(sound.pos / self.rate, 3))
        owner = sound.meta.get("owner")
        if isinstance(owner, str) and owner:  # a personal channel's: only its account's event streams
            data["personal"] = True
            try:
                self.publish(f"sound.{kind}", data, audience=owner)
            except TypeError:  # a publish without audiences: keep it private
                pass
            return
        self.publish(f"sound.{kind}", data)

    # --- Playing ---

    async def prepare(self, source=None, *, samples=None, sample_rate: Optional[int] = None,
                      max_seconds: Optional[float] = None):
        """(samples, capped, length in seconds): the sound decoded and cut to the maximum length."""
        limit = self.max_length()
        if max_seconds is not None:
            if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or max_seconds <= 0:
                raise OverlayError("'max_seconds' must be a number above 0")
            limit = min(limit, max_seconds) if limit > 0 else max_seconds
        if samples is not None:
            pcm = await asyncio.to_thread(decode.from_samples, samples, sample_rate, self.rate, limit)
        elif source is not None:
            pcm = await asyncio.to_thread(decode.decode, source, self.rate, limit)
        else:
            raise OverlayError("Give a file, a URL, bytes or samples")
        if pcm.shape[0] == 0:
            raise OverlayError("The sound is empty")
        pcm, capped = cap(pcm, int(limit * self.rate) if limit > 0 else 0, self.rate)
        return pcm, capped, pcm.shape[0] / self.rate

    async def play(self, channels, source=None, *, samples=None, sample_rate: Optional[int] = None,
                   volume: float = 100, duck=True, policy: str = "queue", priority: int = 0,
                   max_seconds: Optional[float] = None, start_stopped: bool = False, override: bool = False,
                   label: str = "", origin: str = "sonorium", announcement: bool = False) -> dict:
        """
        Play a sound on channels (session ids). Returns {"sound_id", "length",
        "capped", "channels": {id: {"state", "message"}}}; state is playing,
        queued, starting (a stopped channel being started for it), or skipped
        (with the reason). An announcement skips channels with announcements
        off (ANNOUNCEMENTS_OFF). OverlayError / decode.DecodeError for a bad request.
        """
        if announcement:
            from sonorium.core.announce import check_on
            check_on()  # Settings > Advanced > Announcements off: AnnouncementsOff
        if isinstance(channels, str):
            channels = [channels]
        if not isinstance(channels, (list, tuple)) or not channels or len(channels) > MAX_CHANNELS_PER_SOUND:
            raise OverlayError(f"'channels' must list 1 to {MAX_CHANNELS_PER_SOUND} channel ids")
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not 0 <= volume <= MAX_VOLUME:
            raise OverlayError(f"'volume' must be 0-{MAX_VOLUME}")
        if policy not in POLICIES:
            raise OverlayError(f"'policy' must be one of {', '.join(POLICIES)}")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise OverlayError("'priority' must be a whole number")
        duck = Duck.check(duck, self.default_duck())
        pcm, capped, length = await self.prepare(source, samples=samples, sample_rate=sample_rate,
                                                 max_seconds=max_seconds)
        sound_id = "s_" + secrets.token_hex(6)
        request = {"sound_id": sound_id, "label": label, "origin": origin, "override": override,
                   "priority": priority, "start_stopped": start_stopped, "announcement": bool(announcement)}
        results = {}
        for channel_id in dict.fromkeys(str(c) for c in channels):
            results[channel_id] = self._play_on(channel_id, pcm, capped, volume / 100.0, duck, policy, priority,
                                                start_stopped, override, request)
        played = [c for c, r in results.items() if r["state"] != "skipped"]
        logger.info(f"Sound {sound_id} ({label or 'no label'}, {length:.1f}s{', capped' if capped else ''}): "
                    f"{len(played)} of {len(results)} channel(s)")
        return {"sound_id": sound_id, "length": round(length, 3), "capped": capped, "channels": results}

    def _play_on(self, channel_id, pcm, capped, gain, duck, policy, priority, start_stopped, override, request) -> dict:
        sessions = self.sessions
        find = getattr(sessions, "get_any", None) if request.get("announcement") else None
        find = find if callable(find) else getattr(sessions, "get", None)  # a personal channel: announcements only
        session = find(channel_id) if find is not None else None
        if session is None:
            return {"state": "skipped", "message": "No such channel"}
        if request.get("announcement") and not getattr(session, "announcements", True):
            logger.info(f"  '{session.name}': announcements are off, skipped")
            return {"state": "skipped", "message": ANNOUNCEMENTS_OFF}
        if not override:
            reason = self._refused(session, request)
            if reason:
                return {"state": "skipped", "message": reason}
        sound = Sound(sound_id=request["sound_id"], pcm=pcm, volume=gain, duck=duck, policy=policy,
                      priority=priority, capped=capped,
                      meta={"channel_id": session.id, "name": session.name, "label": request["label"],
                            "origin": request["origin"], "owner": getattr(session, "owner", None)})
        muted = bool(getattr(session, "speakers_muted", False))
        waking = self._waking.get(session.id)
        if waking is not None and not waking["ready"]:
            waking["pending"].append(sound)
            return {"state": "starting", "message": ""}
        channel = sessions.channel_of(session.id)
        from sonorium.core.channel import ChannelState
        if channel is not None and channel.state == ChannelState.PLAYING:
            state = self.overlay_of(channel, session).add(sound)
            return {"state": "playing" if state == "waiting" else "queued",
                    "message": "Speakers muted" if muted else ""}
        if not start_stopped:
            return {"state": "skipped", "message": "The channel is stopped"}
        if muted:
            return {"state": "skipped", "message": "Speakers muted"}
        if not sessions.get_resolved_speakers(session):
            return {"state": "skipped", "message": "The channel has no speakers"}
        if getattr(sessions, "media_controller", None) is None:
            return {"state": "skipped", "message": "Speakers aren't available"}
        self._waking[session.id] = {"ready": False, "pending": [sound]}
        self._spawn(self._wake(session))
        return {"state": "starting", "message": ""}

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _wake(self, session) -> None:
        """Start a stopped channel's speakers for its sounds, then stop them again."""
        sessions = self.sessions
        sid = session.id
        state = self._waking[sid]
        channel = None
        speakers: list = []
        try:
            channel = sessions.claim_channel(sid)
            if channel is None:
                for sound in state["pending"]:
                    self._on_event("ended", sound, "no free channel")
                return
            channel.run_silent()
            overlay = self.overlay_of(channel, session)
            speakers = list(sessions.get_resolved_speakers(session))
            if hasattr(sessions, "media_speakers"):  # an audio output device isn't woken for a sound
                speakers = sessions.media_speakers(speakers)
            controller = sessions.media_controller
            logger.info(f"  '{session.name}': starting {len(speakers)} speaker(s) for a sound")
            await controller.play_media_multi(speakers, sessions.get_stream_url(session))
            await controller.set_volume_multi(speakers, session.volume / 100.0)
            give_up = _now() + playback_values.get("wake_wait")
            while channel.speaker_clients < len(speakers) and _now() < give_up and not session.is_playing:
                await asyncio.sleep(WAKE_POLL_SECONDS)
            if channel.speaker_clients < len(speakers):
                logger.info(f"  '{session.name}': {channel.speaker_clients} of {len(speakers)} speaker(s) "
                            f"connected; playing the sound anyway")
            await asyncio.sleep(playback_values.get("wake_lead"))
            state["ready"] = True
            for sound in state["pending"]:
                overlay.add(sound)
            state["pending"] = []
            while overlay.busy and not session.is_playing and sessions.channel_of(sid) is channel:
                await asyncio.sleep(WAKE_POLL_SECONDS)
            if not session.is_playing:
                await asyncio.sleep(playback_values.get("wake_tail"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"  '{session.name}': a sound could not be played: {e}")
            for sound in state.get("pending") or []:
                self._on_event("ended", sound, "error")
        finally:
            self._waking.pop(sid, None)
            if not session.is_playing and channel is not None and sessions.channel_of(sid) is channel:
                if speakers and sessions.media_controller is not None:
                    try:
                        await sessions.media_controller.stop_multi(speakers)
                    except Exception as e:
                        logger.debug(f"  Stopping speakers after a sound: {e}")
                await sessions.release_unused(sid, "its sound finished")

    # --- Reading and cancelling ---

    def sounds(self) -> list[dict]:
        """Every sound playing, waiting or queued, by channel."""
        result = []
        sessions = self.sessions
        for session in (sessions.list() if sessions is not None else []):
            channel = sessions.channel_of(session.id)
            overlay = getattr(channel, "overlay", None) if channel is not None else None
            for item in (overlay.sounds() if overlay is not None else []):
                result.append({**item, "channel_id": session.id, "name": session.name})
            for sound in (self._waking.get(session.id) or {}).get("pending", []):
                result.append({**sound.to_dict(self.rate), "state": "starting", "channel_id": session.id,
                               "name": session.name})
        return result

    def cancel(self, sound_id: str, channel_id: Optional[str] = None) -> bool:
        """Stop a sound (on one channel, or all it plays on). False if it isn't playing or queued."""
        found = False
        sessions = self.sessions
        for session in (sessions.list() if sessions is not None else []):
            if channel_id is not None and session.id != channel_id:
                continue
            waking = self._waking.get(session.id)
            if waking is not None:
                for sound in [s for s in waking["pending"] if s.sound_id == sound_id]:
                    waking["pending"].remove(sound)
                    self._on_event("ended", sound, "cancelled")
                    found = True
            channel = sessions.channel_of(session.id)
            overlay = getattr(channel, "overlay", None) if channel is not None else None
            if overlay is not None and overlay.cancel(sound_id):
                found = True
        return found

    async def wait_idle(self) -> None:
        """For tests: until every stopped channel started for a sound is stopped again."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
