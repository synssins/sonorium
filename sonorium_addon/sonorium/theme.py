import re
import threading
import time
import weakref
from functools import cached_property

import av
import numpy as np

from sonorium.obs import logger
from sonorium.recording import LOG_THRESHOLD, SAMPLE_RATE, ExclusionGroupCoordinator
from sonorium.utils import IndexList


def sanitize(text: str) -> str:
    """Sanitize a string to be safe for use as an ID/filename."""
    # Replace spaces and special chars with underscores
    text = re.sub(r'[^\w\-]', '_', text.lower())
    # Remove consecutive underscores
    text = re.sub(r'_+', '_', text)
    # Strip leading/trailing underscores
    return text.strip('_')

# Default output gain multiplier (now controlled via device.master_volume)
DEFAULT_OUTPUT_GAIN = 6.0

# Default threshold for short file detection (seconds)
DEFAULT_SHORT_FILE_THRESHOLD = 15.0


class ThemeDefinition:
    """

    Run-time only. A ephemeral mix defined by the user.

    ThemeDefinition: What recordings are involved, volumes. User defines these via the UI, then selects a media player entity to stream from it.
    ThemeStream: One instance per client/connection. Has a RecordingStream for each recording in the ThemeDefinition.

    When a user selectes a media player for this theme, then clicks play, HA tells the player to play URL /theme/name.
     - On the API side, the ThemeDefinition with ID "name" is selected, and a new ThemeStream initialized.

    When a user modifies a themeDefinition, like change recording volume, all live ThemeStreams are updated.

    Every ThemeDefinition needs an inited RecordingStream for each recording. That way we can have per-theme, per-recording state (volume, playing, etc).
    Not really. Cos each connection needs its own Stream.


    recording (immutable, one per-path) -> recording_instance (mutable, contains addition vol, is_enabled, etc) -> recording_stream (one per-connection)

    """

    def __init__(self, sonorium, name, theme_id: str = None):
        self.sonorium = sonorium
        self.name = name
        # Use provided UUID, or fall back to sanitized folder name for backwards compatibility
        self._theme_id = theme_id

        # Short file threshold (seconds) - files shorter than this use sparse playback
        # Can be customized per theme via metadata.json
        self.short_file_threshold = DEFAULT_SHORT_FILE_THRESHOLD

        # Group settings by group name (metadata.json "groups"), read by grouped tracks
        self.groups: dict[str, dict] = {}

        # Use theme-specific recordings instead of all recordings
        if name in self.sonorium.theme_metas:
            theme_metas = self.sonorium.theme_metas[name]
        else:
            # Fallback to all recordings for backwards compatibility
            theme_metas = self.sonorium.metas

        # Pass theme reference to instances so they can access threshold
        self.instances = IndexList(meta.get_instance(theme=self) for meta in theme_metas)

        # Mixes of this theme that are still in use. Weak references: a mix is
        # freed (with its decoders and buffers) once its channel or listener
        # drops it. A plain list kept every mix ever started, so memory grew
        # with each play and theme change.
        self.streams: weakref.WeakSet = weakref.WeakSet()

    @cached_property
    def url(self) -> str:
        from sonorium.settings import settings
        return f'{settings.stream_url}/stream/{self.id}'

    @property
    def id(self):
        """Return the theme UUID from metadata.json, or sanitized name as fallback."""
        return self._theme_id if self._theme_id else sanitize(self.name)


    def get_stream(self, overrides: dict | None = None):
        """A new mix of this theme. `overrides`: the channel's preset values (see TrackView)."""
        theme = ThemeStream(self, overrides)
        self.streams.add(theme)
        logger.debug(f'ThemeDefinition {self.name}: Created new ThemeStream (total: {len(self.streams)} streams)')
        return theme


class ThemeStream:
    """

    Run-time only. A ephemeral mix defined by the user.

    ThemeDefinition: What recordings are involved, volumes. User defines these via the UI, then selects a media player entity to stream from it.
    ThemeStream: One instance per client/connection. Has a RecordingStream for each recording in the ThemeDefinition.

    When a user selectes a media player for this theme, then clicks play, HA tells the player to play URL /theme/name.
     - On the API side, the ThemeDefinition with ID "name" is selected, and a new ThemeStream initialized.

    When a user modifies a themeDefinition, like change recording volume, all live ThemeStreams are updated.

    """

    def __init__(self, theme_def: ThemeDefinition, overrides: dict | None = None):
        self.theme_def = theme_def
        # This stream's preset values; changed in place when the channel's preset changes
        self.overrides = overrides if overrides is not None else {}

        # One coordinator per named group: only one track of a group plays at a
        # time, and groups don't wait for each other (Lute and Bar chatter can overlap)
        self.exclusion_coordinators: dict[str, ExclusionGroupCoordinator] = {}
        # Seconds of audio mixed so far: groups time their turns by this, not
        # the wall clock, so a stall can't let two tracks of a group overlap
        self.audio_seconds = 0.0

        # Create streams, passing the exclusion coordinator
        from sonorium.mixing import MixLevel
        from sonorium.recording import RecordingThemeStream
        self.mix_level = MixLevel(SAMPLE_RATE, RecordingThemeStream.CHUNK_SIZE)

        # Track key -> its TrackView, and the streams in mixing order: one per
        # track, except a Merry-go-round group, which plays as one stream (its bed)
        self._views: dict[str, object] = {}
        self.recording_streams = []
        self._beds: tuple = ()  # the groups playing as a merry-go-round
        self._lock = threading.Lock()  # adopt (API thread) and mode changes (mixing thread)
        self._build(theme_def)

    def _new_stream(self, track):
        from sonorium.recording import group_gap_range

        group = track.exclusion_group
        if group and group not in self.exclusion_coordinators:
            # Read the group's gap each time (from the theme this stream plays
            # now), so a change in the editor applies at once
            gap = lambda g=group: group_gap_range((getattr(self.theme_def, "groups", None) or {}).get(g))
            self.exclusion_coordinators[group] = ExclusionGroupCoordinator(gap, clock=lambda: self.audio_seconds)
        coordinator = self.exclusion_coordinators.get(group) if group else None
        return track.get_stream(exclusion_coordinator=coordinator)

    def _bed_groups(self, theme_def=None) -> tuple:
        """The groups set to Merry-go-round now."""
        from sonorium.recording import GROUP_MODE_MERRY_GO_ROUND, group_mode
        groups = getattr(theme_def or self.theme_def, "groups", None) or {}
        return tuple(sorted(g for g, s in list(groups.items()) if group_mode(s) == GROUP_MODE_MERRY_GO_ROUND))

    def _new_bed(self, group: str):
        """A Merry-go-round group's stream. It reads the group's files and settings from this stream's theme now."""
        from sonorium.recording import MerryGoRoundStream

        def members():
            views = (self._views.get(i.name) for i in list(self.theme_def.instances)
                     if getattr(i.meta, "group", None) == group)
            return [v for v in views if v is not None]

        def settings():
            return (getattr(self.theme_def, "groups", None) or {}).get(group)

        logger.info(f'ThemeStream "{self.theme_def.name}": group "{group}" plays as a merry-go-round')
        return MerryGoRoundStream(group, members, settings)

    def _build(self, theme_def):
        """
        Streams for `theme_def`, keeping the ones still right: a track still
        there keeps playing where it is and reads the new settings; a new track
        gets its stream; a Merry-go-round group keeps its bed (new files join
        its pool, removed ones finish first); a group whose mode changed gets
        new streams. Returns (tracks joined, tracks left).
        """
        from sonorium.recording import MerryGoRoundStream, TrackView

        beds = self._bed_groups(theme_def)
        old_tracks, old_beds = {}, {}
        for stream in self.recording_streams:
            if isinstance(stream, MerryGoRoundStream):
                old_beds[stream.group] = stream
            else:
                old_tracks[stream.instance.name] = stream
        for group in beds:
            self.exclusion_coordinators.pop(group, None)  # turns start over if it goes back to Intermittent
        self.theme_def = theme_def
        before = set(self._views)
        streams, seen = [], set()
        for instance in theme_def.instances:
            view = self._views.get(instance.name)
            if view is None:
                view = self._views[instance.name] = TrackView(instance, self.overrides)
            else:
                view._instance = instance  # same track: new settings, same place
            group = getattr(instance.meta, "group", None)
            if group in beds:
                old_tracks.pop(instance.name, None)  # its own stream (Intermittent before) stops
                if group not in seen:
                    seen.add(group)
                    streams.append(old_beds.pop(group, None) or self._new_bed(group))
                continue
            stream = old_tracks.pop(instance.name, None)
            streams.append(stream if stream is not None else self._new_stream(view))
        names = {instance.name for instance in theme_def.instances}
        for name in [n for n in list(self._views) if n not in names]:
            self._views.pop(name, None)
        self.recording_streams = streams  # one assignment: the mixer sees the old list or the new one
        self._beds = beds
        return sorted(names - before), sorted(before - names)

    def adopt(self, theme_def: ThemeDefinition):
        """
        The theme was rescanned (a file uploaded, moved or deleted): follow the
        new definition without restarting. Tracks still there keep playing
        where they are and now read the new settings; new tracks join at once
        with their saved settings (in a group, with no drag, so they're a
        likely early pick); removed tracks leave.
        """
        with self._lock:
            added, left = self._build(theme_def)
        if added or left:
            logger.info(f'ThemeStream "{theme_def.name}": {len(added)} track(s) joined, {len(left)} left')

    def _follow_group_modes(self):
        """A group switched between Intermittent and Merry-go-round: rebuild its streams."""
        if self._bed_groups() == self._beds:
            return
        with self._lock:
            if self._bed_groups() != self._beds:
                self._build(self.theme_def)
                logger.info(f'ThemeStream "{self.theme_def.name}": group modes changed '
                            f'(merry-go-round: {", ".join(self._beds) or "none"})')

    @cached_property
    def chunk_silence(self):
        from sonorium.recording import RecordingThemeStream
        data = np.zeros((1, RecordingThemeStream.CHUNK_SIZE), np.int16)
        return data

    @staticmethod
    def _holds_turn(stream) -> bool:
        coordinator = getattr(stream, "exclusion_coordinator", None)
        return coordinator is not None and coordinator.is_playing(stream.instance.name)

    def iter_chunks(self):
        from sonorium.recording import RecordingThemeStream

        while True:
            self._follow_group_modes()
            data_recs = []
            for stream in list(self.recording_streams):
                if stream.instance.is_enabled:
                    data_recs.append(next(stream))
                elif self._holds_turn(stream):
                    next(stream)  # muted mid-play: runs on silently, so it ends on time
            self.audio_seconds += RecordingThemeStream.CHUNK_SIZE / SAMPLE_RATE
            if not data_recs:
                # logger.debug(f'Theme "{self.theme_def.name}" has no enabled recordings. Streaming silence...')
                data_recs.append(self.chunk_silence)
            
            # Sum the tracks with a smoothed level (sonorium/mixing.py)
            output_gain = getattr(self.theme_def.sonorium, 'master_volume', DEFAULT_OUTPUT_GAIN)
            data = self.mix_level.mix(data_recs, output_gain)
            
            yield data

    def __iter__(self):
        output = av.open(file='.mp3', mode="w")
        bitrate = 128_000
        out_stream = output.add_stream(codec_name='mp3', rate=44100, bit_rate=bitrate)
        iter_chunks = self.iter_chunks()

        start_time = time.time()
        audio_time = 0.0  # total audio duration sent

        try:
            while True:
                for i, data in enumerate(iter_chunks):
                    frame = av.AudioFrame.from_ndarray(data, format='s16', layout='mono')
                    frame.rate = 44100

                    frame_duration = frame.samples / frame.rate
                    audio_time += frame_duration

                    for packet in out_stream.encode(frame):
                        packet_bytes = bytes(packet)
                        yield packet_bytes

                    # Only sleep if we are ahead of real-time
                    now = time.time()
                    ahead = audio_time - (now - start_time)
                    if ahead > 0:
                        time.sleep(ahead)

                    if i % LOG_THRESHOLD == 0:
                        logger.debug(f'Waiting {ahead:.5f} seconds to maintain real-time pacing {audio_time=}...')


        finally:
            logger.info('Closing transcoder...')
            iter_chunks.close()
            output.close()
