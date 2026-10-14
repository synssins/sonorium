"""
Channels that were playing when a restart was asked for (Settings, the web UI:
sonorium/host.py) play again after it.

Right before the clean shutdown of a requested restart, remember() writes the
playing channels (theme, preset, sequence, playlist and volume) next to
state.json. The next start takes the file (take() always deletes it) and
resume() starts those channels again. A crash or a stop never writes it, so
channels stay stopped then.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from sonorium.obs import logger

RESUME_FILE = "resume_after_restart.json"
# A file older than this is from a restart that never came back (ignored)
MAX_AGE_SECONDS = 600.0
# The channel's settings put back before it plays again
FIELDS = ("theme_id", "preset_id", "sequence_id", "playlist_id", "playlist_position", "playlist_order", "volume")
# Speakers found on the network may take a moment to show up after a start
RETRY_SECONDS = 5.0
ATTEMPTS = 6


def _path(folder) -> Path:
    return Path(folder) / RESUME_FILE


def remember(sessions, folder) -> int:
    """Write the playing channels (not personal ones) to the resume file. Returns how many."""
    playing = [{"id": s.id, **{name: getattr(s, name) for name in FIELDS}}
               for s in sessions if s.is_playing and not getattr(s, "owner", None)]
    if not playing:
        clear(folder)
        return 0
    try:
        _path(folder).write_text(json.dumps({"saved_at": time.time(), "channels": playing}), encoding="utf-8")
    except OSError as e:
        logger.warning(f"Could not remember the playing channels for the restart: {e}")
        return 0
    return len(playing)


def clear(folder) -> None:
    try:
        _path(folder).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(f"Could not remove {RESUME_FILE}: {e}")


def take(folder, now: Optional[float] = None) -> list[dict]:
    """The channels to play again (and the file is deleted); [] without a recent file."""
    path = _path(folder)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"Could not read {RESUME_FILE}: {e}")
        data = {}
    clear(folder)
    if not isinstance(data, dict):
        return []
    saved_at = data.get("saved_at")
    now = time.time() if now is None else now
    if not isinstance(saved_at, (int, float)) or not 0 <= now - saved_at <= MAX_AGE_SECONDS:
        return []
    return [entry for entry in data.get("channels") or [] if isinstance(entry, dict) and entry.get("id")]


def _apply(session, entry: dict) -> None:
    for name in FIELDS:
        if name in entry:
            setattr(session, name, entry[name])


async def resume(session_manager, entries: list[dict],
                 on_started: Optional[Callable[[object], Awaitable]] = None,
                 retry_seconds: float = RETRY_SECONDS, attempts: int = ATTEMPTS) -> list[str]:
    """
    Play the remembered channels again, each with its saved theme, preset,
    sequence, playlist and volume. A channel that can't start yet (its
    speakers not found yet) is tried again a few times. Returns the started ids.
    """
    waiting = []
    for entry in entries:
        session = session_manager.state.sessions.get(entry["id"])
        if session is None:
            continue
        _apply(session, entry)
        waiting.append(session)
    if waiting:
        session_manager.state.save()
        logger.info(f"Playing {len(waiting)} channel(s) again after the restart")
    started = []
    for attempt in range(max(1, attempts)):
        if attempt:
            await asyncio.sleep(retry_seconds)
        left = []
        for session in waiting:
            if session.is_playing:  # started meanwhile (the UI, Home Assistant)
                continue
            try:
                ok = await session_manager.play(session.id)
            except Exception as e:
                logger.warning(f"  '{session.name}' could not play again after the restart: {e}")
                ok = False
            if not ok:
                left.append(session)
                continue
            started.append(session.id)
            if on_started is not None:
                try:
                    await on_started(session)
                except Exception as e:
                    logger.debug(f"  After playing '{session.name}' again: {e}")
        waiting = left
        if not waiting:
            break
    for session in waiting:
        logger.warning(f"  '{session.name}' did not play again after the restart")
    return started
