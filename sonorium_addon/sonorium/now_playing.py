"""
Now playing (the Status page): what each track of a playing mix is doing
right now, read from the values the mixer already keeps (recording.py,
theme.py). Nothing here runs per chunk: a snapshot is a few attribute reads
per track, taken when the page asks (GET /api/now-playing).

A track row:
    {"type": "track", "key": track key, "name": shown name,
     "state": "on" | "fade" | "wait" | "rest" | "off",
     "mode": "Background" | "Intermittent" | "Ebb & Flow",
     "now": text, with "{t}" where the countdown goes (e.g. "Next in {t}"),
     "seconds": the countdown in seconds (None: none),
     "level": the track's level now, 0..1 (None: silent),
     "band": [low, high] of an Ebb & Flow track's volume, 0..1 (else None)}
A group row is the same with "type": "group", the group's mode ("One at a
time", "Every file at once", "Merry-go-round") and "tracks": its tracks.
States: on Playing, fade Fading, wait Waiting, rest Paused, off Muted.
"""
from __future__ import annotations

from sonorium.recording import (
    SAMPLE_RATE,
    CrossfadeRecordingStream,
    MerryGoRoundStream,
    PresenceMixingStream,
    HardLoopStream,
    SparsePlaybackStream,
)

SECTIONS = ("Background", "Intermittent", "Ebb & Flow")
GROUP_MODES = {"Background": "Merry-go-round", "Intermittent": "One at a time", "Ebb & Flow": "Every file at once"}

# How far behind the mixer each kind of speaker plays (seconds): its own
# buffer of the stream, estimated. "As heard" on the Status page shows the
# mix this long ago, the longest of a channel's speakers.
SPEAKER_BUFFER_SECONDS = {
    "cast": 5.0,
    "sonos": 3.0,
    "airplay": 2.0,
    "dlna": 4.0,
    "linkplay": 3.0,
    "heos": 4.0,
    "denon": 4.0,
    "esphome": 1.5,
}
DEFAULT_SPEAKER_BUFFER_SECONDS = 3.0


def speaker_buffer_seconds(speaker_types) -> float:
    """The longest estimated buffer among these speaker types (0 with none)."""
    return max((SPEAKER_BUFFER_SECONDS.get(t or "", DEFAULT_SPEAKER_BUFFER_SECONDS) for t in speaker_types), default=0.0)


def display_name(key: str) -> str:
    """A track's name without its group folder."""
    return key.split("/", 1)[1] if "/" in key else key


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _secs(samples) -> float | None:
    return None if samples is None else round(max(0.0, samples / SAMPLE_RATE), 1)


def _row(key, name, state, mode, now, seconds=None, level=None, band=None) -> dict:
    return {"type": "track", "key": key, "name": name, "state": state, "mode": mode, "now": now,
            "seconds": None if seconds is None else round(max(0.0, seconds), 1),
            "level": None if level is None else round(min(1.0, max(0.0, level)), 3),
            "band": band}


def section_of(stream) -> str | None:
    """The section a stream plays in (None: not played, e.g. Unassigned)."""
    if isinstance(stream, SparsePlaybackStream):
        return "Intermittent"
    if isinstance(stream, PresenceMixingStream):
        return "Ebb & Flow"
    if isinstance(stream, (CrossfadeRecordingStream, HardLoopStream, MerryGoRoundStream)):
        return "Background"
    return None


def _track(mix, stream, mode: str) -> dict:
    """One track's row (not a merry-go-round's file)."""
    view = stream.instance
    key = view.name
    name = display_name(key)
    volume = _num(getattr(view, "volume", 0.0))
    enabled = bool(getattr(view, "is_enabled", True))
    gain = getattr(mix, "_toggle_gain", {}).get(key, 1.0 if enabled else 0.0)
    band = None
    if isinstance(stream, PresenceMixingStream) and stream.exclusion_coordinator is None:
        top = stream._top()
        band = [round(stream._ebb_low() * top, 3), round(top, 3)]

    transition = getattr(mix, "_active", {}).get(key)
    if transition is not None:
        coming = transition.side == "in"
        return _row(key, name, "fade", mode, "Fading in" if coming else "Fading out",
                    level=transition.level, band=band)
    if gain <= 0.0 and not enabled:
        return _row(key, name, "off", mode, "Muted", band=band)
    if 0.0 < gain < 1.0:
        return _row(key, name, "fade", mode, "Fading in" if enabled else "Fading out",
                    level=volume * gain * _inner_level(stream), band=band)

    if isinstance(stream, SparsePlaybackStream):
        phase = stream.phase
        if phase == "play":
            return _row(key, name, "on", mode, "Playing", level=volume * gain)
        if phase == "wait" and stream.phase_end is not None:
            return _row(key, name, "wait", mode, "Next in {t}", seconds=_secs(stream.phase_end - stream.clock))
        return _row(key, name, "wait", mode, "Waiting")

    if isinstance(stream, PresenceMixingStream):
        if stream.exclusion_coordinator is not None:
            # A group of the old Exclusive switch: fades in on its turn
            g, target = stream.turn_gain, stream.turn_target
            if g <= 0.0 and target <= 0.0:
                return _row(key, name, "wait", mode, "Waiting")
            if g != target:
                return _row(key, name, "fade", mode, "Fading in" if target > g else "Fading out",
                            level=volume * gain * g)
            return _row(key, name, "on", mode, "Playing", level=volume * gain * g)
        until = stream.ebb_until
        if stream.ebb_state == "playing" and stream.ebb_fade_at is not None:
            until = stream.ebb_fade_at if until is None else min(until, stream.ebb_fade_at)  # the file ends first
        left = None if until is None else _secs(until - stream.clock)
        if stream.ebb_state == "paused":
            return _row(key, name, "rest", mode, "Paused, back in {t}", seconds=left, band=band)
        level = volume * gain * stream.ebb_level
        if stream.ebb_state == "ending":
            return _row(key, name, "fade", mode, "Fading out", level=level, band=band)
        text = {"rise": "Rising, {t}", "fall": "Falling, {t}"}.get(stream.ebb_move, "Holding, {t}")
        return _row(key, name, "on", mode, text, seconds=left, level=level, band=band)

    return _row(key, name, "on", mode, "Playing", level=volume * gain)


def _inner_level(stream) -> float:
    """What the stream itself plays at (an Ebb & Flow track's drift), 1 for the rest."""
    if isinstance(stream, PresenceMixingStream):
        if stream.exclusion_coordinator is not None:
            return stream.turn_gain
        return stream.ebb_level
    return 1.0


def _group(name: str, mode: str, tracks: list) -> dict:
    return {"type": "group", "key": name, "name": name, "state": "wait", "mode": GROUP_MODES[mode],
            "now": "", "seconds": None, "level": None, "band": None, "tracks": tracks}


def _turns_group(name: str, coordinator, tracks: list) -> dict:
    """An Intermittent group: its tracks take turns; the row counts down to the next turn."""
    group = _group(name, "Intermittent", tracks)
    if tracks and all(t["state"] == "off" for t in tracks):
        group.update(state="off", now="Muted")
        return group
    playing, next_in = coordinator.turn_now() if coordinator is not None else (None, None)
    holder = next((t for t in tracks if t["key"] == playing), None) if playing else None
    if holder is not None:
        group.update(state="on" if holder["state"] != "off" else "off",
                     now=f'{holder["name"]} playing', level=holder["level"])
    elif next_in:
        group.update(now="Next in {t}", seconds=round(next_in, 1))
    else:
        group.update(now="Picking next")
    return group


def _ebb_group(name: str, tracks: list) -> dict:
    """An Ebb & Flow group: every file at once; the row counts the ones sounding."""
    group = _group(name, "Ebb & Flow", tracks)
    if tracks and all(t["state"] == "off" for t in tracks):
        group.update(state="off", now="Muted")
        return group
    sounding = sum(1 for t in tracks if t["state"] in ("on", "fade"))
    group.update(state="on" if sounding else "rest", now=f"{sounding} playing")
    return group


def _bed(bed: MerryGoRoundStream) -> dict:
    """A Merry-go-round group: one file plays, crossfading into the next."""
    voices = list(bed._voices)
    by_name = {v.name: v for v in voices}
    tracks = []
    for view in list(bed._members()):
        key = view.name
        name = display_name(key)
        voice = by_name.get(key)
        if voice is None:
            if not view.is_enabled:
                tracks.append(_row(key, name, "off", "Background", "Muted"))
            else:
                tracks.append(_row(key, name, "wait", "Background", "Waiting"))
            continue
        level = _num(voice.level, _num(view.volume))
        if voice.fade_out and voice.end is not None and voice.pos >= voice.end - voice.fade_out:
            share = max(0.0, (voice.end - voice.pos) / voice.fade_out)
            tracks.append(_row(key, name, "fade", "Background", "Fading out", level=level * share))
        elif voice.fade_in and voice.pos < voice.fade_in:
            share = voice.pos / voice.fade_in
            tracks.append(_row(key, name, "fade", "Background", "Fading in", level=level * share))
        else:
            tracks.append(_row(key, name, "on", "Background", "Playing", level=level))
    group = _group(bed.group, "Background", tracks)
    if tracks and all(t["state"] == "off" for t in tracks):
        group.update(state="off", now="Muted")
        return group
    names = [display_name(v.name) for v in voices]
    if len(names) >= 2:
        group.update(state="on", now=f"{names[0]}, crossfading to {names[-1]}")
    elif names:
        group.update(state="on", now=f"{names[0]} playing")
    else:
        group.update(now="Waiting")
    levels = [t["level"] for t in tracks if t["level"] is not None]
    group["level"] = round(max(levels), 3) if levels else None
    return group


def mix_now(mix) -> list[dict]:
    """
    A playing mix (theme.ThemeMix) as sections of rows, in the order of
    the Theme Editor: Background, Intermittent, Ebb & Flow; in each, the
    groups first, then the tracks outside groups. Empty sections are left out.
    """
    sections: dict[str, list] = {s: [] for s in SECTIONS}
    groups: dict[tuple, dict] = {}
    for stream in list(getattr(mix, "sources", ()) or ()):
        section = section_of(stream)
        if section is None:
            continue
        if isinstance(stream, MerryGoRoundStream):
            groups[("bed", stream.group)] = {"section": section, "row": _bed(stream)}
            continue
        row = _track(mix, stream, section)
        view = stream.instance
        turns = getattr(view, "exclusion_group", None) if getattr(stream, "exclusion_coordinator", None) is not None else None
        ebb = getattr(view, "ebb_group_name", None) if section == "Ebb & Flow" else None
        if turns:
            entry = groups.setdefault(("turns", turns), {"section": "Intermittent", "name": turns, "tracks": [],
                                                         "coordinator": stream.exclusion_coordinator})
            entry["tracks"].append(row)
        elif ebb:
            entry = groups.setdefault(("ebb", ebb), {"section": section, "name": ebb, "tracks": []})
            entry["tracks"].append(row)
        else:
            sections[section].append(row)
    for (kind, _), entry in groups.items():
        if kind == "bed":
            row = entry["row"]
        elif kind == "turns":
            row = _turns_group(entry["name"], entry["coordinator"], entry["tracks"])
        else:
            row = _ebb_group(entry["name"], entry["tracks"])
        entry["row"] = row
    out = []
    for section in SECTIONS:
        rows = [e["row"] for e in groups.values() if e["section"] == section] + sections[section]
        if rows:
            out.append({"title": section, "rows": rows})
    return out


def _speaker_type(session_manager, speaker_id: str) -> str | None:
    speaker = None
    try:
        speaker = session_manager.registry.get_speaker(speaker_id)
    except Exception:
        pass
    if speaker is not None:
        return getattr(speaker, "type", None)
    if str(speaker_id).startswith("net:"):
        return str(speaker_id).split(":")[1]  # a network speaker, "net:<type>:<id>"
    return None


def channel_now(session_manager, session) -> dict | None:
    """A playing channel (its speakers play, or a browser listens) for the Status page; None when idle."""
    listening = session_manager.is_listened(session)
    if not (session.is_playing or listening):
        return None
    channel = session_manager._channel_for(session)
    mix = getattr(channel, "_theme_stream", None) if channel is not None else None
    if mix is None:
        return None
    speakers = session_manager.get_resolved_speakers(session) if session.is_playing else []
    buffer = 0.0
    if speakers and not session.speakers_muted:
        buffer = speaker_buffer_seconds(_speaker_type(session_manager, s) for s in speakers)
    try:
        playback = session_manager.playback_status(session)
    except Exception:
        playback = {}
    sequence = playback.get("sequence") or None
    theme_name = None
    try:
        metadata = session_manager.theme_metadata_manager.get_metadata(session.theme_id)
        theme_name = getattr(metadata, "name", None)
    except Exception:
        pass
    return {
        "id": session.id,
        "name": session.name,
        "theme_id": session.theme_id,
        "theme_name": (theme_name if isinstance(theme_name, str) and theme_name else None)
        or getattr(channel, "current_theme_name", None) or session.theme_id,
        "preset_name": session_manager.preset_name(session.theme_id, session.preset_id),
        "sequence": {"name": sequence.get("name"), "step": sequence.get("step"), "steps": sequence.get("steps")}
        if sequence else None,
        "speakers_playing": bool(session.is_playing),
        "speakers": session_manager.get_speaker_summary(session) if session.is_playing else None,
        "speakers_muted": bool(session.speakers_muted),
        "listening": bool(listening),
        "buffer_seconds": buffer,
        "sections": mix_now(mix),
    }
