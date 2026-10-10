import re
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

        # Track key -> its TrackView, and the streams in mixing order
        self._views: dict[str, object] = {}
        self.recording_streams = []
        for instance in theme_def.instances:
            self.recording_streams.append(self._new_stream(instance))

    def _new_stream(self, instance):
        from sonorium.recording import TrackView, group_gap_range

        track = TrackView(instance, self.overrides)
        group = track.exclusion_group
        if group and group not in self.exclusion_coordinators:
            # Read the group's gap each time (from the theme this stream plays
            # now), so a change in the editor applies at once
            gap = lambda g=group: group_gap_range((getattr(self.theme_def, "groups", None) or {}).get(g))
            self.exclusion_coordinators[group] = ExclusionGroupCoordinator(gap, clock=lambda: self.audio_seconds)
        coordinator = self.exclusion_coordinators.get(group) if group else None
        self._views[instance.name] = track
        return track.get_stream(exclusion_coordinator=coordinator)

    def adopt(self, theme_def: ThemeDefinition):
        """
        The theme was rescanned (a file uploaded, moved or deleted): follow the
        new definition without restarting. Tracks still there keep playing
        where they are and now read the new settings; new tracks join at once
        with their saved settings (in a group, with no drag, so they're a
        likely early pick); removed tracks leave.
        """
        old = {stream.instance.name: stream for stream in self.recording_streams}
        streams = []
        added = []
        for instance in theme_def.instances:
            stream = old.pop(instance.name, None)
            if stream is not None:
                self._views[instance.name]._instance = instance  # same track: new settings, same place
                streams.append(stream)
            else:
                added.append(instance.name)
        self.theme_def = theme_def
        for name in added:
            instance = next(i for i in theme_def.instances if i.name == name)
            streams.append(self._new_stream(instance))
        for name in old:
            self._views.pop(name, None)
        self.recording_streams = streams  # one assignment: the mixer sees the old list or the new one
        if added or old:
            logger.info(f'ThemeStream "{theme_def.name}": {len(added)} track(s) joined, {len(old)} left')

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
