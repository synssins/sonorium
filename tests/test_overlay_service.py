"""
Sounds over channels (core/overlay_service.py) and ramps on channels
(core/ramp_service.py), with real Channels and a small stand-in for the
session manager: playing channels get the sound at once, a stopped channel
is started for it and stopped again, gates refuse, overrides skip them,
muted channels aren't woken, events are published.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")

from playback_helpers import RATE, addon_modules  # noqa: E402

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.overlay_service", "sonorium.core.ramp_service", "sonorium.core.channel",
                       "sonorium.playback_values", "sonorium.core.announce") as mods:
        m.__dict__.update(mods.__dict__)
        m.playback_values.set_source(lambda: {"wake_wait": 2.0, "wake_lead": 0.05, "wake_tail": 0.05,
                                              "duck_attack": 0.05, "duck_release": 0.05})
        yield
        m.playback_values.set_source(None)


class Sessions:
    """What the services use of the session manager."""

    def __init__(self):
        self.sessions = {}
        self.channels = {}
        self.holds = []
        self.released = []
        self.media_controller = SimpleNamespace(
            play_media_multi=AsyncMock(side_effect=self._speakers_connect),
            set_volume_multi=AsyncMock(), stop_multi=AsyncMock())

    async def _speakers_connect(self, speakers, url):
        for sid, ch in self.channels.items():
            for _ in speakers:
                ch.client_connected()
        return {s: True for s in speakers}

    def add(self, sid, playing=False, muted=False, speakers=("media_player.a",)):
        session = SimpleNamespace(id=sid, name=sid.title(), is_playing=playing, speakers_muted=muted,
                                  volume=40, speakers=list(speakers))
        self.sessions[sid] = session
        if playing:
            ch = m.channel.Channel(id=len(self.channels) + 1)
            ch.run_silent()
            self.channels[sid] = ch
        return session

    def get(self, sid):
        return self.sessions.get(sid)

    def list(self):
        return list(self.sessions.values())

    def channel_of(self, sid):
        return self.channels.get(sid)

    def claim_channel(self, sid):
        if sid not in self.channels:
            self.channels[sid] = m.channel.Channel(id=len(self.channels) + 1)
        return self.channels[sid]

    def get_resolved_speakers(self, session):
        return session.speakers

    def get_stream_url(self, session):
        return "http://test/stream/channel1"

    def add_mix_hold(self, predicate):
        self.holds.append(predicate)

    async def release_unused(self, sid, why):
        if any(h(sid) for h in self.holds):
            return
        self.released.append(sid)
        self.channels.pop(sid).stop()

    async def play(self, sid):
        self.sessions[sid].is_playing = True
        ch = self.claim_channel(sid)
        ch.run_silent()
        return True

    async def stop(self, sid):
        self.sessions[sid].is_playing = False
        ch = self.channels.pop(sid, None)
        if ch:
            ch.stop()
        return True

    pause = stop


def service():
    events = []
    sessions = Sessions()
    svc = m.overlay_service.OverlayService(sessions, publish=lambda t, d: events.append((t, d)), rate=RATE)
    return svc, sessions, events


def beep(seconds=0.3):
    return np.sin(np.linspace(0, 2 * np.pi * 440 * seconds, int(RATE * seconds)))


def kinds(events, sound_id=None):
    return [(t, d["channel_id"], d.get("reason")) for t, d in events
            if t.startswith("sound.") and (sound_id is None or d["sound_id"] == sound_id)]


async def until(predicate, seconds=5.0):
    end = asyncio.get_running_loop().time() + seconds
    while not predicate() and asyncio.get_running_loop().time() < end:
        await asyncio.sleep(0.02)


def test_playing_channel_gets_it_stopped_one_is_skipped():
    async def main():
        svc, sessions, events = service()
        sessions.add("kitchen", playing=True)
        sessions.add("office")
        result = await svc.play(["kitchen", "office"], samples=beep(), sample_rate=RATE, label="bell")
        assert result["channels"]["kitchen"]["state"] == "playing"
        assert result["channels"]["office"] == {"state": "skipped", "message": "The channel is stopped"}
        await until(lambda: ("sound.ended", "kitchen", "finished") in kinds(events))
        assert kinds(events) == [("sound.started", "kitchen", None), ("sound.ended", "kitchen", "finished")]
        assert events[0][1]["label"] == "bell"
        sessions.channels["kitchen"].stop()
    asyncio.run(main())


def test_stopped_channel_is_started_for_the_sound_then_stopped():
    async def main():
        svc, sessions, events = service()
        sessions.add("bedroom")
        result = await svc.play("bedroom", samples=beep(0.2), sample_rate=RATE, start_stopped=True)
        assert result["channels"]["bedroom"]["state"] == "starting"
        assert any(h("bedroom") for h in sessions.holds)   # the mix is held while it plays
        await svc.wait_idle()
        controller = sessions.media_controller
        controller.play_media_multi.assert_awaited_once()
        controller.stop_multi.assert_awaited_once_with(["media_player.a"])
        assert sessions.released == ["bedroom"]
        assert not sessions.sessions["bedroom"].is_playing   # never shown as playing
        assert kinds(events) == [("sound.started", "bedroom", None), ("sound.ended", "bedroom", "finished")]
    asyncio.run(main())


def test_muted_stopped_channel_is_not_woken_gates_and_override():
    async def main():
        svc, sessions, events = service()
        sessions.add("nursery", muted=True)
        sessions.add("den", playing=True)
        result = await svc.play(["nursery"], samples=beep(), sample_rate=RATE, start_stopped=True)
        assert result["channels"]["nursery"] == {"state": "skipped", "message": "Speakers muted"}
        svc.add_gate("users", lambda session, req: "Quiet hours" if session.id == "den" else None)
        result = await svc.play(["den"], samples=beep(), sample_rate=RATE)
        assert result["channels"]["den"] == {"state": "skipped", "message": "Quiet hours"}
        result = await svc.play(["den"], samples=beep(0.1), sample_rate=RATE, override=True)
        assert result["channels"]["den"]["state"] == "playing"
        svc.remove_gate("users")
        sessions.channels["den"].stop()
    asyncio.run(main())


def test_announcements_off_skips_announcements_only():
    async def main():
        svc, sessions, events = service()
        sessions.add("kids", playing=True).announcements = False
        result = await svc.play(["kids"], samples=beep(0.1), sample_rate=RATE, override=True, announcement=True)
        assert result["channels"]["kids"] == {"state": "skipped", "message": m.overlay_service.ANNOUNCEMENTS_OFF}
        result = await svc.play(["kids"], samples=beep(0.1), sample_rate=RATE)  # a sound that isn't one plays
        assert result["channels"]["kids"]["state"] == "playing"
        await svc.wait_idle()
        sessions.channels["kids"].stop()
    asyncio.run(main())


def test_max_length_caps_and_per_call_lowers_it():
    async def main():
        svc, sessions, events = service()
        sessions.add("hall", playing=True)
        m.playback_values.set_source(lambda: {"sound_max_length": 0.5})
        result = await svc.play("hall", samples=beep(2.0), sample_rate=RATE)
        assert result["capped"] and result["length"] == 0.5
        result = await svc.play("hall", samples=beep(2.0), sample_rate=RATE, max_seconds=0.2)
        assert result["length"] == 0.2
        assert svc.cancel(result["sound_id"])
        sessions.channels["hall"].stop()
        m.playback_values.set_source(lambda: {"wake_wait": 2.0, "wake_lead": 0.05, "wake_tail": 0.05})
    asyncio.run(main())


def test_bad_requests_refused():
    async def main():
        svc, sessions, _ = service()
        sessions.add("hall", playing=True)
        for kwargs in ({"volume": 500}, {"policy": "later"}, {"priority": 1.5}, {"duck": "loud"}):
            with pytest.raises(m.overlay_service.OverlayError):
                await svc.play("hall", samples=beep(), sample_rate=RATE, **kwargs)
        with pytest.raises(m.overlay_service.OverlayError):
            await svc.play([], samples=beep(), sample_rate=RATE)
        sessions.channels["hall"].stop()
    asyncio.run(main())


def test_ramp_service_runs_and_stops_the_channel_after():
    async def main():
        events = []
        sessions = Sessions()
        sessions.add("bedroom", playing=True)
        ramps = m.ramp_service.RampService(sessions, publish=lambda t, d: events.append((t, d)), rate=RATE)
        result = await ramps.start("bedroom", 0.0, 0.3, from_level=1.0, curve="linear", then="stop")
        assert result["then"] == "stop"
        assert ramps.status("bedroom")["ramp"]["to"] == 0.0
        await until(lambda: not sessions.sessions["bedroom"].is_playing)
        assert [t for t, _ in events] == ["ramp.started", "ramp.finished"]
        assert not sessions.sessions["bedroom"].is_playing

        with pytest.raises(m.ramp_service.RampError):
            await ramps.start("bedroom", 1.0, 10)  # not playing
        await ramps.start("bedroom", 1.0, 30, from_level=0.0, start=True)  # played first
        assert sessions.sessions["bedroom"].is_playing
        cancelled = ramps.cancel("bedroom")
        assert cancelled["level"] < 0.2 and events[-1][0] == "ramp.cancelled"
        await sessions.stop("bedroom")
    asyncio.run(main())


# --- Announcements (core/announce.py) over the real overlay ---------------------------------

def wav_bytes(seconds=0.2):
    import io
    import wave
    pcm = (beep(seconds) * 8000).astype(np.int16)
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(pcm.tobytes())
    return out.getvalue()


class FakeTts:
    def __init__(self, ready=True):
        self.is_ready, self.said = ready, []

    def providers(self):
        return [{"id": "t"}]

    def ready(self, provider=None):
        return self.is_ready

    async def speak(self, text, provider=None, **options):
        self.said.append(text)
        return wav_bytes()


def test_announcement_starts_a_stopped_channel_plays_then_stops_it():
    async def main():
        svc, sessions, events = service()
        sessions.add("bedroom")  # stopped
        result = await m.announce.announce(svc, FakeTts(), ["bedroom"], text="Dinner is ready")
        assert result["played"] == ["bedroom"] and result["skipped"] == []
        assert result["channels"]["bedroom"]["state"] == "starting"
        await svc.wait_idle()
        sessions.media_controller.stop_multi.assert_awaited_once_with(["media_player.a"])
        assert sessions.released == ["bedroom"] and not sessions.sessions["bedroom"].is_playing
    asyncio.run(main())


def test_announcements_off_is_the_channels_own_flag_listed_or_not():
    """A channel the lists don't show (e.g. a personal one) still has its own Announcements setting."""
    async def main():
        svc, sessions, events = service()
        sessions.add("kitchen", playing=True)
        hidden = sessions.add("hidden", playing=True)
        hidden.announcements = False
        sessions.list = lambda: [sessions.sessions["kitchen"]]  # "hidden" isn't listed
        tts = FakeTts()
        assert m.announce.resolve_channels(sessions, "all") == ["kitchen"]
        assert m.announce.resolve_channels(sessions, ["hidden"]) == ["hidden"]
        result = await m.announce.announce(svc, tts, ["hidden"], text="Hello")
        assert result["played"] == [] and result["skipped"] == ["hidden"] and tts.said == []  # nothing spoken
        result = await m.announce.announce(svc, tts, ["kitchen", "hidden"], text="Hello")
        assert result["played"] == ["kitchen"] and result["skipped"] == ["hidden"]
        assert result["channels"]["hidden"] == {"state": "skipped", "message": "Announcements are off"}
        await svc.wait_idle()
        for ch in sessions.channels.values():
            ch.stop()
    asyncio.run(main())


def test_without_text_to_speech_the_chime_plays_and_text_alone_is_refused():
    async def main():
        svc, sessions, events = service()
        sessions.add("kitchen", playing=True)
        tts = FakeTts(ready=False)
        result = await m.announce.announce(svc, tts, ["kitchen"], text="Dinner", sound=wav_bytes())
        assert result["played"] == ["kitchen"] and result["chime"] is not None and result["speech"] is None
        assert "text to speech" in result["notice"] and tts.said == []
        with pytest.raises(m.announce.TtsUnavailable):
            await m.announce.announce(svc, tts, ["kitchen"], text="Dinner")
        await svc.wait_idle()
        sessions.channels["kitchen"].stop()
    asyncio.run(main())


def test_channels_by_name_slug_or_all_and_duck_in_db():
    svc, sessions, events = service()
    sessions.add("kids_room").get_entity_slug = lambda: "kids_room"
    sessions.sessions["kids_room"].name = "Kids Room"
    sessions.add("office")
    assert m.announce.resolve_channels(sessions, "kids room") == ["kids_room"]
    assert m.announce.resolve_channels(sessions, "KIDS_ROOM, Office") == ["kids_room", "office"]
    assert m.announce.resolve_channels(sessions, "*") == ["kids_room", "office"]
    with pytest.raises(LookupError):
        m.announce.resolve_channels(sessions, ["Garage"])
    with pytest.raises(ValueError):
        m.announce.resolve_channels(sessions, 5)
    assert m.announce.parse_duck(None) is True and m.announce.parse_duck(12) == {"db": 12}
    with pytest.raises(ValueError):
        m.announce.parse_duck("loud")
