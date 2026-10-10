from collections import deque
from enum import Enum
import random
import threading
import time
import numpy as np

from sonorium.obs import logger
import av

LOG_THRESHOLD = 500


class ExclusionGroupCoordinator:
    """
    Takes turns for the tracks of one group (a folder in the theme, or the old
    "Exclusive" switch): only one plays at a time, never overlapping.

    - Nothing plays for INITIAL_DELAY after the stream starts.
    - After a track finishes, the group waits its gap (random in the group's
      range) before the next one.
    - Then the group picks the next track itself, by weight: a track that
      just played carries "drag" that lowers its chance; the drag wears off
      each time another track plays, faster for a higher Interval slider
      (the track's share). The same track never plays twice in a row unless
      no other track is available (muted, at 0%, or alone in the group).

    Tracks ask for the turn by calling is_blocked / try_start_playing with
    their share; for COLLECT_SECONDS after the gap the group gathers who is
    asking, then picks one of them.

    One coordinator per group per ThemeStream.
    """

    # Gap after a track finishes when the group doesn't set one (seconds)
    MIN_GAP_AFTER_EXCLUSIVE = 120.0
    # Nothing in a group plays this long after the stream starts
    INITIAL_DELAY = 60.0
    # After the gap, how long the group gathers the tracks asking for the turn
    COLLECT_SECONDS = 4.0
    # A picked track that doesn't take its turn this soon (muted meanwhile) loses it
    PICK_TIMEOUT = 10.0
    # A track's lowest share, so a track at 1% can still come up now and then
    MIN_SHARE = 0.05

    def _range(self) -> tuple[float, float] | None:
        """The gap range now: a fixed range, or a function giving the group's current one."""
        gap = self._gap_range() if callable(self._gap_range) else self._gap_range
        return gap or None

    def _next_gap(self) -> float:
        gap = self._range()
        if gap:
            return random.uniform(*gap)
        return self.MIN_GAP_AFTER_EXCLUSIVE

    def __init__(self, gap_range=None, clock=None):
        # The group's gap between plays (seconds, random in the range each time);
        # MIN_GAP_AFTER_EXCLUSIVE when the group doesn't set one
        self._gap_range = gap_range
        # Seconds of audio the theme has played (ThemeStream.audio_seconds), so a
        # stalled stream delays every track alike and turns can't overlap;
        # the wall clock when no audio clock is given
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._playing_track: str | None = None  # The track holding the turn
        self._play_end_time: float = 0  # When its turn ends at the latest
        self._last_played_track: str | None = None  # Track that played most recently
        self._cooldown_until: float = 0  # The group's gap: nothing starts before this
        self._registered_tracks: set[str] = set()
        self._start_time: float = self._clock()
        self._asked: dict[str, float] = {}  # track -> when it last asked for the turn
        self._shares: dict[str, float] = {}  # track -> its Interval slider (0-1) when it asked
        self._drag: dict[str, float] = {}  # track -> 1.0 just played, wearing off towards 0
        self._collect_from: float | None = None  # gathering askers since
        self._picked: str | None = None  # the track whose turn is next
        self._picked_at: float = 0

    def register_track(self, track_name: str):
        """Register a track of the group."""
        with self._lock:
            self._registered_tracks.add(track_name)
            logger.debug(f'ExclusionGroup: Registered track "{track_name}" ({len(self._registered_tracks)} total)')

    # --- turn-taking ---

    def _expire(self, now: float):
        """A turn that ran past its end is over: the gap starts."""
        if self._playing_track is not None and now >= self._play_end_time:
            self._last_played_track = self._playing_track
            self._playing_track = None
            self._cooldown_until = now + self._next_gap()
            self._collect_from = None
            logger.debug(f'ExclusionGroup: "{self._last_played_track}" finished, gap until +{self._cooldown_until - now:.0f}s')

    def _weight(self, track: str) -> float:
        return turn_weight(self._shares.get(track, 1.0), self._drag.get(track, 0.0))

    def _pick(self, candidates: list[str]) -> str:
        return weighted_choice(candidates, [self._weight(t) for t in candidates])

    def _may_start(self, track: str, share: float | None, now: float) -> bool:
        """Records the ask; True when it's this track's turn now."""
        self._asked[track] = now
        if share is not None:
            self._shares[track] = share
        if now < self._start_time + self.INITIAL_DELAY:
            return False
        self._expire(now)
        if self._playing_track is not None or now < self._cooldown_until:
            return False
        if self._picked is not None and now - self._picked_at > self.PICK_TIMEOUT:
            self._picked = None  # it didn't come for its turn: pick again
            self._collect_from = None
        if self._picked is None:
            if self._collect_from is None:
                self._collect_from = now
            if now - self._collect_from < self.COLLECT_SECONDS:
                return False
            asking = sorted(t for t, at in self._asked.items() if at >= self._collect_from)
            others = [t for t in asking if t != self._last_played_track]
            self._picked = self._pick(others or asking)
            self._picked_at = now
            logger.debug('ExclusionGroup: picked "%s" from %s', self._picked,
                         ", ".join(f"{t} {self._weight(t):.2f}" for t in (others or asking)))
        return self._picked == track

    def _start(self, track: str, duration_seconds: float, now: float):
        for other in self._registered_tracks | set(self._asked):
            if other != track and self._drag.get(other):
                self._drag[other] = worn_drag(self._drag[other], self._shares.get(other, 1.0))
        self._drag[track] = 1.0
        self._playing_track = track
        self._play_end_time = now + duration_seconds
        self._picked = None
        self._collect_from = None
        logger.debug(f'ExclusionGroup: "{track}" starting playback (duration: {duration_seconds:.1f}s)')

    def try_start_playing(self, track_name: str, duration_seconds: float, share: float | None = None) -> bool:
        """Take the group's turn if it's this track's; True when it may play now."""
        with self._lock:
            now = self._clock()
            if not self._may_start(track_name, share, now):
                return False
            self._start(track_name, duration_seconds, now)
            return True

    def is_blocked(self, track_name: str, share: float | None = None) -> bool:
        """Whether this track must wait (it also counts as asking for the turn)."""
        with self._lock:
            return not self._may_start(track_name, share, self._clock())

    def is_playing(self, track_name: str) -> bool:
        """Whether this track holds the group's turn now."""
        with self._lock:
            return self._playing_track == track_name and self._clock() < self._play_end_time

    def finish_playing(self, track_name: str):
        """The track finished: the group's gap starts."""
        with self._lock:
            if self._playing_track == track_name:
                now = self._clock()
                self._last_played_track = track_name
                self._playing_track = None
                self._play_end_time = 0
                self._cooldown_until = now + self._next_gap()
                self._collect_from = None
                logger.debug(f'ExclusionGroup: "{track_name}" finished, gap until +{self._cooldown_until - now:.0f}s')

    def get_wait_time(self) -> float:
        """Seconds until the group might start a track."""
        with self._lock:
            now = self._clock()
            if now < self._start_time + self.INITIAL_DELAY:
                return (self._start_time + self.INITIAL_DELAY) - now
            if self._playing_track is not None:
                remaining = self._play_end_time - now
                if remaining > 0:
                    return remaining + ((self._range() or (self.MIN_GAP_AFTER_EXCLUSIVE,))[0])
            if now < self._cooldown_until:
                return self._cooldown_until - now
            if self._picked is None and self._collect_from is not None:
                return max(0.0, self._collect_from + self.COLLECT_SECONDS - now)
            return 0

    def get_track_count(self) -> int:
        """Get number of registered tracks."""
        with self._lock:
            return len(self._registered_tracks)


# How a group picks its next track (both group modes use these)

def _clamp_share(share) -> float:
    return max(ExclusionGroupCoordinator.MIN_SHARE, min(1.0, 1.0 if share is None else float(share)))


def turn_weight(share, drag: float) -> float:
    """A track's chance to be picked: sqrt(share) x (1 - drag)^2 (share: its Interval, 0-1)."""
    return (_clamp_share(share) ** 0.5) * (1.0 - drag) ** 2


def worn_drag(drag: float, share) -> float:
    """A track's drag after another track played: it wears off faster at a high share."""
    return drag * (0.6 + 0.35 * (1.0 - _clamp_share(share)))


def weighted_choice(candidates: list, weights: list[float]):
    """One of the candidates, each as likely as its weight (all alike when every weight is 0)."""
    total = sum(weights)
    if total <= 0:
        return random.choice(candidates)
    point = random.uniform(0, total)
    for candidate, weight in zip(candidates, weights):
        point -= weight
        if point <= 0:
            return candidate
    return candidates[-1]


class PlaybackMode(str, Enum):
    """Playback mode for tracks.

    - CONTINUOUS: Track loops continuously with crossfade (default for long files)
    - SPARSE: Track plays once at full volume, then silence for an interval before repeating
    - PRESENCE: Track fades in/out of the mix based on presence value
    - AUTO: Automatically choose based on file length and presence setting
    """
    CONTINUOUS = "continuous"
    SPARSE = "sparse"
    PRESENCE = "presence"
    AUTO = "auto"

# Threshold for "short" audio files that get sparse playback
SHORT_FILE_THRESHOLD_SECONDS = 15.0
# Sparse playback interval range (seconds between plays)
SPARSE_MIN_INTERVAL = 180.0   # 3 minutes at 100% presence
SPARSE_MAX_INTERVAL = 1800.0  # 30 minutes at ~0% presence
# Variance applied to intervals (±30%)
SPARSE_INTERVAL_VARIANCE = 0.30

# Crossfade duration in seconds for loop transitions
LOOP_CROSSFADE_DURATION = 1.5
# Fade duration for tracks fading in/out of the mix
TRACK_FADE_DURATION = 6.0
GROUP_EDGE_FADE_SECONDS = 0.02  # grouped tracks: no audible fade, just no click
# Sample rate
SAMPLE_RATE = 44100
# Calculated sample counts
CROSSFADE_SAMPLES = int(LOOP_CROSSFADE_DURATION * SAMPLE_RATE)
TRACK_FADE_SAMPLES = int(TRACK_FADE_DURATION * SAMPLE_RATE)


def decode_mono(path):
    """
    A file's audio as float32 mono blocks at SAMPLE_RATE, whatever its rate,
    channels or format. The resampler is flushed at the end of the file.
    """
    resampler = av.AudioResampler(format='s16', layout='mono', rate=SAMPLE_RATE)
    container = av.open(path)
    if len(container.streams.audio) == 0:
        container.close()
        raise ValueError('No audio stream')
    stream = next(iter(container.streams.audio))

    def convert(frame_resamp):
        data = frame_resamp.to_ndarray()
        return data.mean(axis=0).astype(np.float32)  # downmix to mono

    def decode():
        try:
            for frame_orig in container.decode(stream):
                for frame_resamp in resampler.resample(frame_orig):
                    yield convert(frame_resamp)
            # Flush samples still held by the resampler at end of file
            for frame_resamp in resampler.resample(None):
                yield convert(frame_resamp)
        finally:
            container.close()

    return decode()


class RecordingMetadata:
    """
    Represents file, metadata, etc. The non-state stuff, on disk. One per file. Immutable
    """

    def __init__(self, path, theme_folder=None):
        self.path = path
        self._duration_samples = None
        # Track key and group (see sonorium/theme_files.py): "Fireplace", or
        # "Lute/Lute song 1" with group "Lute" for a file in a group folder
        if theme_folder is not None:
            from sonorium.theme_files import track_group, track_key
            self._key = track_key(theme_folder, path)
            self.group = track_group(theme_folder, path)
        else:
            self._key = path.stem
            self.group = None

    def get_instance(self, theme=None):
        return RecordingThemeInstance(self, theme=theme)

    @property
    def name(self):
        return self._key
    
    @property
    def duration_samples(self):
        """Get total duration in samples (cached)"""
        if self._duration_samples is None:
            try:
                container = av.open(self.path)
                stream = next(iter(container.streams.audio))
                # duration is in time_base units
                if stream.duration and stream.time_base:
                    duration_sec = float(stream.duration * stream.time_base)
                    self._duration_samples = int(duration_sec * SAMPLE_RATE)
                else:
                    # Fallback: decode to count (slower but accurate)
                    self._duration_samples = self._count_samples()
                container.close()
            except Exception as e:
                logger.warning(f'Could not get duration for {self.path}: {e}')
                self._duration_samples = SAMPLE_RATE * 60  # Assume 1 minute as fallback
        return self._duration_samples

    @property
    def duration_seconds(self):
        """Get total duration in seconds"""
        return self.duration_samples / SAMPLE_RATE

    def is_short_file(self, threshold: float = SHORT_FILE_THRESHOLD_SECONDS) -> bool:
        """Check if this is a short audio file that should use sparse playback"""
        return self.duration_seconds < threshold
    
    def _count_samples(self):
        """Fallback: decode entire file to count samples"""
        container = av.open(self.path)
        stream = next(iter(container.streams.audio))
        resampler = av.AudioResampler(format='s16', layout='mono', rate=SAMPLE_RATE)
        total = 0
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                total += resampled.samples
        container.close()
        return total


class RecordingThemeInstance:
    """
    Wraps the metadata, but with some extra state, to represent how that recording is set up within a given theme.
    Every theme gets one of these for each recording.
    """

    def __init__(self, meta: RecordingMetadata, theme=None):
        self.meta = meta
        self.theme = theme  # Reference to parent ThemeDefinition for threshold access
        self.volume = 1.0  # Amplitude multiplier (keep at 1.0 for now)
        self.presence = 1.0  # How often this track plays: 1.0 = always, 0.5 = half the time, 0 = never
        self.is_enabled = True  # Master enable/disable (mute)
        self.crossfade_enabled = True  # Enable crossfade looping by default
        self.playback_mode = PlaybackMode.AUTO  # How playback/looping is handled
        self.exclusive = False  # If True, only one exclusive track can play at a time

    @property
    def short_file_threshold(self) -> float:
        """Get the short file threshold from theme, or use default"""
        if self.theme is not None:
            return self.theme.short_file_threshold
        return SHORT_FILE_THRESHOLD_SECONDS

    def _resolve_playback_mode(self) -> PlaybackMode:
        """Resolve AUTO mode to an actual playback mode based on file characteristics."""
        if self.playback_mode != PlaybackMode.AUTO:
            return self.playback_mode

        # AUTO logic: short files use sparse, long files use presence (if < 1.0) or continuous
        if self.meta.is_short_file(self.short_file_threshold):
            return PlaybackMode.SPARSE if self.presence < 1.0 else PlaybackMode.CONTINUOUS
        else:
            return PlaybackMode.PRESENCE if self.presence < 1.0 else PlaybackMode.CONTINUOUS

    def get_stream(self, exclusion_coordinator: ExclusionGroupCoordinator = None):
        mode = self._resolve_playback_mode()

        # SPARSE: Play once at full volume, then silence for interval
        if mode == PlaybackMode.SPARSE:
            return SparsePlaybackStream(self, exclusion_coordinator)

        # CONTINUOUS or PRESENCE: Start with base looping stream
        if self.crossfade_enabled:
            base_stream = CrossfadeRecordingStream(self)
        else:
            base_stream = RecordingThemeStream(self)

        # PRESENCE: Wrap with fade in/out based on presence value. In a group the
        # wrapper is always used, so the track waits its turn even at 100%.
        if mode == PlaybackMode.PRESENCE and (self.presence < 1.0 or (self.exclusive and exclusion_coordinator)):
            return PresenceMixingStream(base_stream, self, exclusion_coordinator if self.exclusive else None)

        return base_stream

    @property
    def name(self):
        return self.meta.name


# Group for tracks marked exclusive in themes from before group folders
LEGACY_EXCLUSIVE_GROUP = "Exclusive"

# A group's master controls, as saved (metadata.json "groups", presets) -> the
# track attribute each one acts on. Volume and presence multiply the track's own
# value (track 50% x group 50% = 25%); muted mutes every track in the group.
# The gap between plays (gap_min/gap_max, seconds) is a real interval, never scaled.
GROUP_TRACK_SETTINGS = {"volume": "volume", "presence": "presence", "muted": "is_enabled"}
GROUP_KEY_PREFIX = "@group:"  # a channel's preset values for a group, in its overrides dict


def group_setting_value(setting: str, value):
    """A saved group/track setting as the track attribute value."""
    if setting == "muted":
        return not value
    if setting == "playback_mode":
        try:
            return PlaybackMode(value)
        except ValueError:
            return PlaybackMode.AUTO
    return value


def group_gap_range(group_settings: dict | None) -> tuple[float, float] | None:
    """A group's gap between plays in seconds (gap_min, gap_max), if it sets one."""
    if not group_settings:
        return None
    low, high = group_settings.get("gap_min"), group_settings.get("gap_max")
    if low is None and high is None:
        return None
    low = float(low if low is not None else high)
    high = float(high if high is not None else low)
    return (min(low, high), max(low, high))


# A group's mode (metadata.json groups[name]["mode"]; a theme setting, not in presets):
# Intermittent plays one track at a time with a gap between them;
# Merry-go-round plays a continuous bed, each file crossfading into the next.
GROUP_MODE_INTERMITTENT = "intermittent"
GROUP_MODE_MERRY_GO_ROUND = "merry_go_round"
GROUP_MODES = (GROUP_MODE_INTERMITTENT, GROUP_MODE_MERRY_GO_ROUND)
# Merry-go-round crossfade (groups[name]["crossfade"], seconds)
DEFAULT_GROUP_CROSSFADE = 10.0
MIN_GROUP_CROSSFADE = 1.0
MAX_GROUP_CROSSFADE = 60.0


def group_mode(group_settings: dict | None) -> str:
    """A group's mode; Intermittent when it sets none (or an unknown one)."""
    mode = (group_settings or {}).get("mode")
    return mode if mode in GROUP_MODES else GROUP_MODE_INTERMITTENT


def group_crossfade(group_settings: dict | None) -> float:
    """A merry-go-round group's crossfade in seconds (default 10, kept within 1-60)."""
    value = (group_settings or {}).get("crossfade")
    try:
        value = DEFAULT_GROUP_CROSSFADE if value is None else float(value)
    except (TypeError, ValueError):
        value = DEFAULT_GROUP_CROSSFADE
    if value != value:  # NaN
        value = DEFAULT_GROUP_CROSSFADE
    return max(MIN_GROUP_CROSSFADE, min(MAX_GROUP_CROSSFADE, value))

# Track settings a preset can set, as RecordingThemeInstance attribute names
PRESET_TRACK_FIELDS = ("volume", "presence", "is_enabled", "crossfade_enabled", "playback_mode", "exclusive")


def preset_track_overrides(preset_tracks: dict) -> dict:
    """
    A preset's saved track settings ({"Rain": {"volume": .8, "muted": false, ...}})
    as the attribute values a TrackView applies ({"Rain": {"volume": .8, "is_enabled": True, ...}}).

    Only settings the preset actually saved are set; anything it leaves out
    follows the theme's own value for that track.
    """
    overrides = {}
    for track_name, settings in (preset_tracks or {}).items():
        track = {}
        if "volume" in settings:
            track["volume"] = settings["volume"]
        if "presence" in settings:
            track["presence"] = settings["presence"]
        if "muted" in settings:
            track["is_enabled"] = not settings["muted"]
        if "seamless_loop" in settings:
            track["crossfade_enabled"] = not settings["seamless_loop"]
        if "playback_mode" in settings:
            try:
                track["playback_mode"] = PlaybackMode(settings["playback_mode"])
            except ValueError:
                track["playback_mode"] = PlaybackMode.AUTO
        if "exclusive" in settings:
            track["exclusive"] = settings["exclusive"]
        overrides[track_name] = track
    return overrides


def preset_group_overrides(preset_groups: dict) -> dict:
    """A preset's group master controls ({"Lute": {"presence": .4, "muted": false}}) as channel overrides."""
    overrides = {}
    for group, settings in (preset_groups or {}).items():
        overrides[GROUP_KEY_PREFIX + group] = {
            GROUP_TRACK_SETTINGS[key]: group_setting_value(key, value)
            for key, value in (settings or {}).items() if key in GROUP_TRACK_SETTINGS
        }
    return overrides


class TrackView:
    """
    A theme's track as one channel hears it: the channel's preset values where
    its preset sets them, otherwise the theme's own (live) settings.

    `overrides` is the channel's dict ({track name: {field: value}}), shared by
    reference, so changing it in place changes what the channel plays.
    """

    def __init__(self, instance: RecordingThemeInstance, overrides: dict):
        self._instance = instance
        self._overrides = overrides

    def __getattr__(self, name):
        value = self._track_value(name)
        group = getattr(self._instance.meta, "group", None)
        if group:
            return self._in_group(group, name, value)
        return value

    def _track_value(self, name):
        """The track's own value: the channel's preset for it, else the theme's."""
        if name in PRESET_TRACK_FIELDS:
            track = self._overrides.get(self._instance.name)
            if track is not None and name in track:
                return track[name]
        return getattr(self._instance, name)

    def _group_master(self, group: str, name: str):
        """A group's master control: the channel's preset for the group, else the group's saved setting."""
        preset_group = self._overrides.get(GROUP_KEY_PREFIX + group)
        if preset_group is not None and name in preset_group:
            return preset_group[name]
        setting = next((s for s, attr in GROUP_TRACK_SETTINGS.items() if attr == name), None)
        theme_groups = getattr(getattr(self._instance, "theme", None), "groups", None) or {}
        saved = theme_groups.get(group) or {}
        if setting in saved:
            return group_setting_value(setting, saved[setting])
        return None

    def _in_group(self, group: str, name: str, value):
        """A track in a group folder: the group's master controls act on the track's own value."""
        if name == "exclusive":
            return True  # one track of a group at a time
        if name in ("volume", "presence"):
            master = self._group_master(group, name)
            return value * master if master is not None else value
        if name == "is_enabled":
            master = self._group_master(group, name)
            return value and (master if master is not None else True)
        if name == "playback_mode":
            # A group plays events, one at a time: each track plays its whole
            # file once on its turn (Intermittent), whatever its own mode says.
            # Background and Ebb & Flow are for the ambience outside groups.
            return PlaybackMode.SPARSE
        return value

    @property
    def exclusion_group(self) -> str | None:
        """The named group this track plays in, one at a time: its folder, or "Exclusive" for the old flag."""
        group = getattr(self._instance.meta, "group", None)
        if group:
            return group
        return LEGACY_EXCLUSIVE_GROUP if self.exclusive else None

    # Same decisions as the theme's own track, made with the channel's values
    _resolve_playback_mode = RecordingThemeInstance._resolve_playback_mode
    get_stream = RecordingThemeInstance.get_stream


class RecordingThemeStream:
    """
    Basic recording stream without crossfade - loops with hard cut.
    """
    CHUNK_SIZE = 1_024

    def __init__(self, instance: RecordingThemeInstance):
        self.instance = instance
        self.resampler = av.AudioResampler(format='s16', layout='mono', rate=SAMPLE_RATE)
        self.gen = self._gen()

    def _gen(self):
        while True:
            container = av.open(self.instance.meta.path)

            if len(container.streams.audio) == 0:
                raise ValueError('No audio stream')
            stream = next(iter(container.streams.audio))

            buffer = np.empty((1, 0), dtype=np.int16)

            i = 0
            for frame_orig in container.decode(stream):
                for frame_resamp in self.resampler.resample(frame_orig):
                    data_resamp = frame_resamp.to_ndarray()
                    data_resamp = data_resamp.mean(axis=0).astype(data_resamp.dtype).reshape(data_resamp.shape)
                    data_resamp = (data_resamp * self.instance.volume).astype(data_resamp.dtype)

                    buffer = np.hstack((buffer, data_resamp))

                    while buffer.shape[1] >= self.CHUNK_SIZE:
                        data = buffer[:, :self.CHUNK_SIZE]
                        buffer = buffer[:, self.CHUNK_SIZE:]

                        yield data

                        if i % LOG_THRESHOLD == 0:
                            vol_mean = round(abs(data).mean())
                            logger.debug(f'{self.__class__.__name__} Yielding chunk #{i} {data.shape=}, {buffer.shape=}, {vol_mean=}')
                        i += 1

            container.close()

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.gen)


class CrossfadeRecordingStream:
    """
    Recording stream with crossfade looping - seamlessly blends end of track into beginning.
    
    When approaching the end of the current playback, starts a second decoder
    and crossfades between them using equal-power curves.
    """
    CHUNK_SIZE = 1_024

    def __init__(self, instance: RecordingThemeInstance):
        self.instance = instance
        self.gen = self._gen()

    def _create_decoder(self):
        """Create a new decoder generator for the audio file (the track's volume read per block)"""
        return (block * self.instance.volume for block in decode_mono(self.instance.meta.path))

    def _gen(self):
        """Main generator with crossfade logic"""
        
        # Get track duration for crossfade timing
        track_duration = self.instance.meta.duration_samples
        crossfade_start = max(0, track_duration - CROSSFADE_SAMPLES)
        
        logger.debug(f'CrossfadeStream: {self.instance.name} duration={track_duration} samples ({track_duration/SAMPLE_RATE:.1f}s), crossfade at {crossfade_start} ({crossfade_start/SAMPLE_RATE:.1f}s)')
        
        # Start first decoder
        current_decoder = self._create_decoder()
        next_decoder = None
        
        buffer = np.empty(0, dtype=np.float32)
        samples_played = 0
        chunk_count = 0
        in_crossfade = False
        crossfade_position = 0
        
        # Pre-generate crossfade curves (equal-power)
        fade_out = np.cos(np.linspace(0, np.pi/2, CROSSFADE_SAMPLES)).astype(np.float32)
        fade_in = np.sin(np.linspace(0, np.pi/2, CROSSFADE_SAMPLES)).astype(np.float32)
        
        next_buffer = np.empty(0, dtype=np.float32)
        current_done = False
        next_done = False

        while True:
            # Fill buffer from current decoder
            while not current_done and len(buffer) < self.CHUNK_SIZE * 2:
                try:
                    chunk = next(current_decoder)
                    buffer = np.concatenate([buffer, chunk.flatten()])
                except StopIteration:
                    # The track ends here. The crossfade runs a little past the
                    # end of the file (and metadata durations can be slightly
                    # off), so finish the fade over silence instead of
                    # restarting the track, which caused audible gaps (#38).
                    current_done = True
                    actual_duration = samples_played + len(buffer)
                    if actual_duration != track_duration:
                        logger.debug(f'CrossfadeStream: {self.instance.name} decoded {actual_duration} samples, expected {track_duration}')
                        track_duration = actual_duration
                        crossfade_start = max(0, track_duration - CROSSFADE_SAMPLES)

            if len(buffer) < self.CHUNK_SIZE:
                buffer = np.concatenate([buffer, np.zeros(self.CHUNK_SIZE - len(buffer), dtype=np.float32)])

            # Check if we should start crossfade
            if not in_crossfade and (samples_played >= crossfade_start or current_done):
                logger.debug(f'CrossfadeStream: Starting crossfade at sample {samples_played}')
                in_crossfade = True
                crossfade_position = 0
                next_decoder = self._create_decoder()
                next_buffer = np.empty(0, dtype=np.float32)
                next_done = False

            # If in crossfade, also fill next_buffer
            if in_crossfade:
                while not next_done and len(next_buffer) < self.CHUNK_SIZE * 2:
                    try:
                        chunk = next(next_decoder)
                        next_buffer = np.concatenate([next_buffer, chunk.flatten()])
                    except StopIteration:
                        next_done = True
                if len(next_buffer) < self.CHUNK_SIZE:
                    next_buffer = np.concatenate([next_buffer, np.zeros(self.CHUNK_SIZE - len(next_buffer), dtype=np.float32)])
            
            # Extract chunk
            output_chunk = buffer[:self.CHUNK_SIZE].copy()
            buffer = buffer[self.CHUNK_SIZE:]
            
            # Apply crossfade if active
            if in_crossfade and len(next_buffer) >= self.CHUNK_SIZE:
                next_chunk = next_buffer[:self.CHUNK_SIZE].copy()
                next_buffer = next_buffer[self.CHUNK_SIZE:]
                
                # Calculate fade positions for this chunk
                fade_start = crossfade_position
                fade_end = min(crossfade_position + self.CHUNK_SIZE, CROSSFADE_SAMPLES)
                chunk_fade_len = fade_end - fade_start
                
                if chunk_fade_len > 0 and fade_start < CROSSFADE_SAMPLES:
                    # Apply fades
                    fade_out_chunk = fade_out[fade_start:fade_end]
                    fade_in_chunk = fade_in[fade_start:fade_end]
                    
                    # Pad if needed
                    if len(fade_out_chunk) < self.CHUNK_SIZE:
                        fade_out_chunk = np.concatenate([fade_out_chunk, np.zeros(self.CHUNK_SIZE - len(fade_out_chunk), dtype=np.float32)])
                        fade_in_chunk = np.concatenate([fade_in_chunk, np.ones(self.CHUNK_SIZE - len(fade_in_chunk), dtype=np.float32)])
                    
                    # Mix with crossfade
                    output_chunk = output_chunk[:len(fade_out_chunk)] * fade_out_chunk + next_chunk[:len(fade_in_chunk)] * fade_in_chunk
                
                crossfade_position += self.CHUNK_SIZE
                
                # Check if crossfade complete
                if crossfade_position >= CROSSFADE_SAMPLES:
                    logger.debug(f'CrossfadeStream: Crossfade complete, switching to new track instance')
                    current_decoder = next_decoder
                    buffer = next_buffer
                    current_done = next_done
                    next_decoder = None
                    next_buffer = np.empty(0, dtype=np.float32)
                    samples_played = crossfade_position  # We're this far into the new track
                    in_crossfade = False
                    crossfade_position = 0
            
            # Convert to int16 and reshape for output
            output_chunk = np.clip(output_chunk, -32768, 32767).astype(np.int16)
            output_data = output_chunk.reshape(1, -1)
            
            samples_played += self.CHUNK_SIZE
            chunk_count += 1
            
            if chunk_count % LOG_THRESHOLD == 0:
                vol_mean = round(abs(output_chunk).mean())
                status = "XFADE" if in_crossfade else "PLAY"
                logger.debug(f'CrossfadeStream [{status}]: chunk #{chunk_count}, samples={samples_played}, vol={vol_mean}')

            yield output_data

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.gen)


class SparsePlaybackStream:
    """
    Stream for short audio files (< 15 seconds) that plays the file once,
    then outputs silence for a randomized interval before playing again.

    This prevents short sounds (like a horse whinny) from looping repeatedly.
    The interval between plays is randomized based on presence:
    - presence=1.0: Plays continuously (not sparse - use regular stream)
    - presence=0.5: Interval is middle of range (~165 seconds average)
    - presence=0.1: Interval is near max (~270 seconds average)

    The file plays once with fade in/out, then silence until next play.

    If the track is marked as 'exclusive' and an ExclusionGroupCoordinator is
    provided, only one exclusive track can play at a time. Other exclusive
    tracks output silence until the playing track finishes.
    """
    CHUNK_SIZE = 1_024

    def __init__(self, instance: RecordingThemeInstance, exclusion_coordinator: ExclusionGroupCoordinator = None):
        self.instance = instance
        self.exclusion_coordinator = exclusion_coordinator

        # Register exclusive tracks with coordinator
        if self.instance.exclusive and self.exclusion_coordinator is not None:
            self.exclusion_coordinator.register_track(self.instance.name)

        self.gen = self._gen()

    def _gen(self):
        import random

        presence = self.instance.presence
        file_duration_samples = self.instance.meta.duration_samples
        file_duration_seconds = self.instance.meta.duration_seconds

        logger.debug(f'SparsePlaybackStream: {self.instance.name} - short file ({file_duration_seconds:.1f}s), using sparse playback' +
                    (', exclusive=True' if self.instance.exclusive else ''))

        # Pre-generate fade curves for the short file
        # Use shorter fade for very short files
        fade_duration = min(TRACK_FADE_DURATION, file_duration_seconds / 3)
        grouped = bool(getattr(self.instance, "exclusion_group", None)) and self.exclusion_coordinator is not None
        if grouped:
            # A group's tracks just play, start to finish (a thunder crack keeps
            # its attack, a song its first notes): only a click-free edge
            fade_duration = min(GROUP_EDGE_FADE_SECONDS, file_duration_seconds / 3)
        fade_samples = int(fade_duration * SAMPLE_RATE)
        fade_in_curve = np.sin(np.linspace(0, np.pi/2, fade_samples)).astype(np.float32)
        fade_out_curve = np.cos(np.linspace(0, np.pi/2, fade_samples)).astype(np.float32)

        def get_silent_interval():
            """
            Calculate silence duration based on presence (lower presence = longer silence).

            - presence=1.0 (100%): ~3 min gaps (±30% = 2.1-3.9 min)
            - presence=0.5 (50%): ~16.5 min gaps (±30% = 11.5-21.5 min)
            - presence=0.1 (10%): ~27 min gaps (±30% = 18.9-35.1 min)
            """
            # Invert presence: low presence = long interval, high presence = short interval
            factor = 1.0 - presence
            base_interval = SPARSE_MIN_INTERVAL + (SPARSE_MAX_INTERVAL - SPARSE_MIN_INTERVAL) * factor

            # Apply randomization (±30% variance)
            variance_min = 1.0 - SPARSE_INTERVAL_VARIANCE
            variance_max = 1.0 + SPARSE_INTERVAL_VARIANCE
            variation = random.uniform(variance_min, variance_max)
            final_interval = base_interval * variation

            logger.debug(f'SparsePlaybackStream: {self.instance.name} next interval: {final_interval/60:.1f} min (presence={presence:.0%})')
            return int(final_interval * SAMPLE_RATE)

        def decode_file_once():
            """Decode the entire file once, returning samples"""
            resampler = av.AudioResampler(format='s16', layout='mono', rate=SAMPLE_RATE)
            container = av.open(self.instance.meta.path)

            if len(container.streams.audio) == 0:
                container.close()
                return np.zeros(0, dtype=np.float32)

            stream = next(iter(container.streams.audio))
            samples = []

            for frame_orig in container.decode(stream):
                for frame_resamp in resampler.resample(frame_orig):
                    data = frame_resamp.to_ndarray()
                    # Downmix to mono
                    data = data.mean(axis=0).astype(np.float32)
                    samples.append(data.flatten())

            container.close()

            if not samples:
                return np.zeros(0, dtype=np.float32)

            return np.concatenate(samples)

        def is_blocked_exclusive():
            """Check if this exclusive track is blocked (without claiming playback)."""
            if not self.instance.exclusive or self.exclusion_coordinator is None:
                return False
            return self.exclusion_coordinator.is_blocked(self.instance.name, self.instance.presence)

        def try_start_exclusive():
            """Try to claim exclusive playback slot. Returns True if allowed."""
            if not self.instance.exclusive or self.exclusion_coordinator is None:
                return True
            return self.exclusion_coordinator.try_start_playing(
                self.instance.name,
                file_duration_seconds,
                self.instance.presence,
            )

        def finish_exclusive():
            """Mark that this exclusive track has finished."""
            if self.instance.exclusive and self.exclusion_coordinator is not None:
                self.exclusion_coordinator.finish_playing(self.instance.name)

        def get_block_wait_chunks():
            """Get number of chunks to wait when blocked (randomized to prevent races)."""
            if self.exclusion_coordinator is not None:
                wait_time = self.exclusion_coordinator.get_wait_time()
                if wait_time > 0:
                    # Add some randomization to prevent all tracks trying at once
                    wait_time += random.uniform(0.5, 3.0)
                    return int(wait_time * SAMPLE_RATE / self.CHUNK_SIZE)
            # Default: wait 1-3 seconds before retrying
            return int(random.uniform(1.0, 3.0) * SAMPLE_RATE / self.CHUNK_SIZE)

        chunk_count = 0
        silence_chunk = np.zeros((1, self.CHUNK_SIZE), dtype=np.int16)
        first_play = True

        while True:
            # Check for updated presence
            presence = self.instance.presence

            # 0% means never: stay silent, checking again every second
            if presence <= 0.0:
                for _ in range(max(1, SAMPLE_RATE // self.CHUNK_SIZE)):
                    yield silence_chunk
                continue

            # On first play, delay with a random portion of the interval
            # This prevents all sparse tracks from playing at stream start
            if first_play and grouped:
                first_play = False  # the group decides when its tracks play
            if first_play:
                first_play = False
                # Random initial delay: 0% to 100% of the normal interval
                initial_delay_samples = int(get_silent_interval() * random.uniform(0.0, 1.0))
                initial_delay_chunks = initial_delay_samples // self.CHUNK_SIZE
                if initial_delay_chunks > 0:
                    logger.debug(f'SparsePlaybackStream: {self.instance.name} initial delay: {initial_delay_samples/SAMPLE_RATE:.1f}s')
                    for _ in range(initial_delay_chunks):
                        chunk_count += 1
                        yield silence_chunk

            # For exclusive tracks, first check if we're blocked
            if is_blocked_exclusive():
                # Output silence for a while before checking again
                wait_chunks = get_block_wait_chunks()
                for _ in range(wait_chunks):
                    chunk_count += 1
                    yield silence_chunk
                continue

            # Try to claim the exclusive playback slot
            if not try_start_exclusive():
                # Another track claimed it first - wait and retry
                wait_chunks = get_block_wait_chunks()
                for _ in range(wait_chunks):
                    chunk_count += 1
                    yield silence_chunk
                continue

            # Decode and play the file once with fade in/out
            audio_data = decode_file_once()

            if len(audio_data) > 0:
                # Apply volume
                audio_data = audio_data * self.instance.volume

                # Apply fade in at start
                if len(fade_in_curve) <= len(audio_data):
                    audio_data[:len(fade_in_curve)] *= fade_in_curve

                # Apply fade out at end
                if len(fade_out_curve) <= len(audio_data):
                    audio_data[-len(fade_out_curve):] *= fade_out_curve

                # Yield the audio in chunks
                pos = 0
                while pos < len(audio_data):
                    chunk_end = min(pos + self.CHUNK_SIZE, len(audio_data))
                    chunk = audio_data[pos:chunk_end]

                    # Pad if needed
                    if len(chunk) < self.CHUNK_SIZE:
                        chunk = np.concatenate([chunk, np.zeros(self.CHUNK_SIZE - len(chunk), dtype=np.float32)])

                    # Convert to int16
                    output = np.clip(chunk, -32768, 32767).astype(np.int16).reshape(1, -1)

                    chunk_count += 1
                    if chunk_count % LOG_THRESHOLD == 0:
                        logger.debug(f'SparsePlaybackStream: {self.instance.name} playing chunk #{chunk_count}')

                    yield output
                    pos += self.CHUNK_SIZE

            # Mark exclusive track as finished playing
            finish_exclusive()

            # Now output silence for the interval (in a group, the group's
            # gap and pick decide when this track plays again)
            silent_samples = 0 if grouped else get_silent_interval()
            silent_chunks = silent_samples // self.CHUNK_SIZE

            logger.debug(f'SparsePlaybackStream: {self.instance.name} entering silence for {silent_samples/SAMPLE_RATE:.1f}s ({silent_chunks} chunks)')

            for _ in range(silent_chunks):
                chunk_count += 1
                yield silence_chunk

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.gen)


class PresenceMixingStream:
    """
    Wrapper stream that controls track presence in the mix.

    Instead of controlling amplitude, the 'presence' value (0.0-1.0) controls
    how often this track is audible in the mix:
    - presence=1.0: Track plays continuously (always in mix)
    - presence=0.5: Track plays ~50% of the time, fading in/out
    - presence=0.0: Track never plays (always silent)

    Uses randomized timing so tracks don't all fade in/out together.
    """
    CHUNK_SIZE = 1_024

    # In a group: how soon to ask again when another track has the turn
    RETRY_SECONDS = 2.0

    def __init__(self, base_stream, instance: RecordingThemeInstance, exclusion_coordinator: ExclusionGroupCoordinator = None):
        self.base_stream = base_stream
        self.instance = instance
        # In a group, the track only fades in when it gets the group's turn
        self.exclusion_coordinator = exclusion_coordinator
        if exclusion_coordinator is not None:
            exclusion_coordinator.register_track(instance.name)
        self.gen = self._gen()

    def _gen(self):
        """Generator that applies presence-based fading"""
        import random

        # State for presence fading
        is_active = True  # Start active
        current_gain = 1.0 if self.instance.presence >= 1.0 else 0.0
        target_gain = 1.0 if self.instance.presence >= 1.0 else 0.0
        fade_position = 0
        samples_until_change = 0

        # Timing parameters (in samples)
        min_active_duration = int(30 * SAMPLE_RATE)  # Min 30 seconds active
        max_active_duration = int(120 * SAMPLE_RATE)  # Max 2 minutes active
        min_inactive_duration = int(20 * SAMPLE_RATE)  # Min 20 seconds inactive
        max_inactive_duration = int(90 * SAMPLE_RATE)  # Max 90 seconds inactive

        def get_next_duration(presence, is_active):
            """Calculate how long to stay in current state based on presence"""
            if presence >= 1.0:
                return float('inf')  # Always active
            if presence <= 0.0:
                return float('inf')  # Always inactive

            if is_active:
                # Higher presence = longer active periods
                base_duration = min_active_duration + (max_active_duration - min_active_duration) * presence
                variation = random.uniform(0.7, 1.3)
                return int(base_duration * variation)
            else:
                # Higher presence = shorter inactive periods
                base_duration = max_inactive_duration - (max_inactive_duration - min_inactive_duration) * presence
                variation = random.uniform(0.7, 1.3)
                return int(base_duration * variation)

        # Initialize timing
        presence = self.instance.presence
        if presence >= 1.0:
            is_active = True
            current_gain = 1.0
            target_gain = 1.0
        elif presence <= 0.0:
            is_active = False
            current_gain = 0.0
            target_gain = 0.0
        else:
            # Start randomly based on presence
            is_active = random.random() < presence
            current_gain = 1.0 if is_active else 0.0
            target_gain = current_gain

        coordinator = self.exclusion_coordinator
        retry_samples = int(self.RETRY_SECONDS * SAMPLE_RATE)
        releasing = False  # faded out and still holding the group's turn
        if coordinator is not None:
            # In a group: start silent and wait for the group's turn
            is_active, current_gain, target_gain = False, 0.0, 0.0
            samples_until_change = retry_samples
        else:
            samples_until_change = get_next_duration(presence, is_active)

        chunk_count = 0

        while True:
            # Get base audio chunk
            try:
                chunk = next(self.base_stream)
            except StopIteration:
                return

            # Check for presence value changes
            new_presence = self.instance.presence
            if new_presence != presence:
                presence = new_presence
                # Recalculate state for new presence (in a group the turn logic does this)
                if coordinator is not None:
                    pass
                elif presence >= 1.0 and target_gain < 1.0:
                    target_gain = 1.0
                    fade_position = 0
                elif presence <= 0.0 and target_gain > 0.0:
                    target_gain = 0.0
                    fade_position = 0

            # Check if it's time to change state
            samples_until_change -= self.CHUNK_SIZE
            if coordinator is not None and samples_until_change <= 0 and presence > 0.0:
                if not is_active:
                    # Ask for the group's turn; at 100% the track keeps it
                    active_samples = get_next_duration(presence, True)
                    # Held through the fade-out too, with room to spare: the turn is
                    # handed back by finish_playing once the fade has finished
                    seconds = 1e9 if active_samples == float('inf') else (active_samples + 2 * TRACK_FADE_SAMPLES) / SAMPLE_RATE + 1.0
                    if coordinator.try_start_playing(self.instance.name, seconds, presence):
                        is_active, target_gain, fade_position = True, 1.0, 0
                        samples_until_change = active_samples
                    else:
                        samples_until_change = retry_samples
                else:
                    # Fade out; the turn is handed back (and the group's gap
                    # starts) once the fade has finished
                    is_active, target_gain, fade_position = False, 0.0, 0
                    releasing = True
                    samples_until_change = retry_samples
            elif coordinator is not None and presence <= 0.0 and is_active:
                is_active, target_gain, fade_position = False, 0.0, 0
                releasing = True
            elif coordinator is None and samples_until_change <= 0 and 0 < presence < 1.0:
                is_active = not is_active
                target_gain = 1.0 if is_active else 0.0
                fade_position = 0
                samples_until_change = get_next_duration(presence, is_active)
                if chunk_count % LOG_THRESHOLD == 0:
                    logger.debug(f'PresenceMixingStream: {self.instance.name} {"fading in" if is_active else "fading out"}')

            # Apply fade if current_gain != target_gain
            if current_gain != target_gain:
                # Calculate fade progress
                fade_progress = fade_position / TRACK_FADE_SAMPLES
                fade_progress = min(1.0, fade_progress)

                if target_gain > current_gain:
                    # Fading in - equal power curve
                    applied_gain = np.sin(fade_progress * np.pi / 2)
                else:
                    # Fading out - equal power curve
                    applied_gain = np.cos(fade_progress * np.pi / 2)

                fade_position += self.CHUNK_SIZE

                # Check if fade complete
                if fade_progress >= 1.0:
                    current_gain = target_gain
                    applied_gain = target_gain
                    if releasing and target_gain == 0.0:
                        releasing = False
                        coordinator.finish_playing(self.instance.name)  # silent now: the group's gap starts
            else:
                applied_gain = current_gain

            # Apply gain to chunk
            if applied_gain < 1.0:
                # Convert to float, apply gain, convert back
                chunk_float = chunk.astype(np.float32) * applied_gain
                chunk = np.clip(chunk_float, -32768, 32767).astype(np.int16)

            chunk_count += 1
            yield chunk

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.gen)


class _SampleQueue:
    """Decoded samples waiting to play, kept as the decoder's blocks (no copying on every push)."""

    def __init__(self):
        self._blocks = deque()
        self._head = 0  # samples of the first block already taken
        self.size = 0

    def push(self, block):
        if len(block):
            self._blocks.append(block)
            self.size += len(block)

    def take(self, count: int):
        out = np.empty(count, np.float32)
        filled = 0
        while filled < count and self._blocks:
            block = self._blocks[0]
            n = min(len(block) - self._head, count - filled)
            out[filled:filled + n] = block[self._head:self._head + n]
            filled += n
            self._head += n
            if self._head >= len(block):
                self._blocks.popleft()
                self._head = 0
        self.size -= filled
        return out[:filled]


class _BedFile:
    """
    One file of a merry-go-round while it sounds: decoded a little ahead of
    where it plays, never all at once.
    """

    def __init__(self, view):
        self.view = view  # the file's TrackView: live volume and mute
        self.name = view.name
        self.pos = 0  # samples played
        self.end = None  # sample where it stops: known once decoded to its end, or set to leave early
        self.fade_in = 0  # samples, from its start
        self.fade_out = 0  # samples, up to self.end
        self.level = None  # gain of the last chunk, to ramp from
        self.done = False  # decoded to the end
        self._queue = _SampleQueue()
        try:
            self.estimate = int(view.meta.duration_samples)
        except Exception:
            self.estimate = 0
        try:
            self._decoder = decode_mono(view.meta.path)
        except Exception as e:
            logger.warning(f'MerryGoRound: cannot play "{self.name}": {e}')
            self._decoder = iter(())

    def fill(self, ahead: int, budget: int | None = None):
        """Decode until `ahead` samples wait to play (or the file ends); at most `budget` samples now."""
        decoded = 0
        while self.end is None and self._queue.size < ahead and (budget is None or decoded < budget):
            try:
                block = next(self._decoder)
            except StopIteration:
                self.done = True
            except Exception as e:
                logger.warning(f'MerryGoRound: "{self.name}" stopped decoding: {e}')
                self.done = True
            else:
                block = np.asarray(block, np.float32).reshape(-1)
                self._queue.push(block)
                decoded += len(block)
            if self.done:
                self.end = self.pos + self._queue.size
                break

    @property
    def length(self) -> int:
        """Its length if known, else at least this long."""
        return self.end if self.end is not None else self.pos + self._queue.size

    def remaining_estimate(self) -> int:
        if self.end is not None:
            return self.end - self.pos
        return max(self.estimate, self.length) - self.pos

    @property
    def finished(self) -> bool:
        return self.end is not None and self.pos >= self.end

    def render(self, count: int, offset: int, level: float):
        """`count` samples of the mix: silence for `offset`, then this file with its fades and level."""
        out = np.zeros(count, np.float32)
        n = count - offset
        if self.end is not None:
            n = min(n, self.end - self.pos)
        samples = self._queue.take(max(0, n))
        n = len(samples)
        if n:
            positions = self.pos + np.arange(n, dtype=np.float64)
            env = np.ones(n, np.float64)
            if self.fade_in > 0:
                env *= np.sin(np.pi / 2 * np.clip(positions / self.fade_in, 0.0, 1.0))
            if self.fade_out > 0 and self.end is not None:
                env *= np.sin(np.pi / 2 * np.clip((self.end - positions) / self.fade_out, 0.0, 1.0))
            start = level if self.level is None else self.level
            if start != level:
                env *= np.linspace(start, level, n)  # a level change ramps over the chunk: no click
            else:
                env *= level
            out[offset:offset + n] = samples * env
            self.pos += n
        self.level = level
        return out


class _BedHandle:
    """What the mix asks a stream (`instance.name`, `instance.is_enabled`): a bed always runs."""

    def __init__(self, name: str):
        self.name = name
        self.is_enabled = True


class MerryGoRoundStream:
    """
    A group in Merry-go-round mode: a continuous bed made from the group's files.

    One file plays; before it ends the group picks the next and crossfades
    into it (equal power) over the group's crossfade, so two files never start
    or stop at the same moment. The pick is by weight among the playable files
    (unmuted, Interval above 0), the same way an Intermittent group picks: a
    file that played recently carries drag, and each file's Interval is its
    share. The file that just played is left out unless it is the only one; a
    single file crossfades into itself. A transition never overlaps a file
    beyond half its length. The bed never stops while a file is playable.

    Each file's volume and mute (with the group's masters, through its
    TrackView) are read every chunk. A file that is muted or set to 0% while
    it plays hands over at once with a crossfade. Files are decoded as they
    play, a few seconds ahead; nothing keeps a whole file in memory.

    `members()` gives the group's TrackViews now (files join and leave as the
    theme is rescanned; a file that left finishes first), `settings()` the
    group's settings now (crossfade).
    """
    CHUNK_SIZE = 1_024
    # Start getting the next file ready this many crossfades before the current one ends
    PREPARE_CROSSFADES = 3
    # How much of the next file to decode per chunk while it waits
    PREPARE_BUDGET = 8 * CHUNK_SIZE

    def __init__(self, group: str, members, settings):
        self.group = group
        self.instance = _BedHandle(GROUP_KEY_PREFIX + group)
        self._members = members
        self._settings = settings
        self._voices: list[_BedFile] = []  # files sounding now, oldest first
        self._current: _BedFile | None = None  # the newest of them
        self._next: _BedFile | None = None  # picked, decoding, waiting for its crossfade
        self._last: str | None = None  # file that started most recently
        self._drag: dict[str, float] = {}
        self._samples = 0  # samples this bed has produced
        self._started = False
        # (sample, "start" | "stop", file) for the most recent starts and stops
        self.events = deque(maxlen=512)
        logger.debug(f'MerryGoRound "{group}": created')
        self.gen = self._gen()

    # --- picking ---

    @staticmethod
    def _playable(view) -> bool:
        return bool(view.is_enabled) and view.presence > 0

    def _member(self, view) -> bool:
        return any(v is view for v in self._members())

    def _pick(self, exclude: str | None):
        candidates = [v for v in self._members() if self._playable(v)]
        if not candidates:
            return None
        others = [v for v in candidates if v.name != exclude] or candidates
        weights = [turn_weight(v.presence, self._drag.get(v.name, 0.0)) for v in others]
        view = weighted_choice(others, weights)
        logger.debug('MerryGoRound "%s": picked "%s" from %s', self.group, view.name,
                     ", ".join(f"{v.name} {w:.2f}" for v, w in zip(others, weights)))
        return view

    def _begin(self, voice: _BedFile, offset: int, fade_in: int):
        """The file starts sounding `offset` samples into this chunk."""
        shares = {v.name: v.presence for v in self._members()}
        for name in list(self._drag):
            if name not in shares:
                del self._drag[name]  # left the group
            elif name != voice.name and self._drag[name]:
                self._drag[name] = worn_drag(self._drag[name], shares[name])
        self._drag[voice.name] = 1.0
        self._last = voice.name
        voice.fade_in = max(1, int(fade_in))
        self._voices.append(voice)
        self._current = voice
        self.events.append((self._samples + offset, "start", voice.name))
        logger.debug(f'MerryGoRound "{self.group}": "{voice.name}" starts at {(self._samples + offset) / SAMPLE_RATE:.1f}s, '
                     f'fade in {voice.fade_in / SAMPLE_RATE:.1f}s')

    # --- the bed ---

    def _chunk(self):
        n = self.CHUNK_SIZE
        cf = int(group_crossfade(self._settings()) * SAMPLE_RATE)
        edge = max(1, int(GROUP_EDGE_FADE_SECONDS * SAMPLE_RATE))
        starts: dict[int, int] = {}  # id(file) -> where in this chunk it starts

        if self._next is not None and not (self._playable(self._next.view) and self._member(self._next.view)):
            self._next = None  # muted or removed while it waited: pick again

        cur = self._current
        if cur is None:
            # Nothing sounding: start at once (at the very start with no fade, as a bed)
            voice = self._next
            self._next = None
            if voice is None:
                view = self._pick(self._last)
                voice = _BedFile(view) if view is not None else None
            if voice is not None:
                voice.fill(2 * cf + 2 * n)
                fade = edge if not self._started else min(cf, max(edge, voice.length // 2))
                self._started = True
                self._begin(voice, 0, fade)
                starts[id(voice)] = 0
        else:
            cur.fill(cf + 2 * n)  # its end is known at least a crossfade ahead
            playable = self._playable(cur.view)
            if self._next is None and (not playable or cur.end is not None
                                       or cur.remaining_estimate() <= self.PREPARE_CROSSFADES * cf + n):
                view = self._pick(cur.name)
                if view is not None:
                    self._next = _BedFile(view)
            nxt = self._next
            if nxt is not None:
                nxt.fill(2 * cf + 2 * n, budget=self.PREPARE_BUDGET)
                cur_offset = starts.get(id(cur), 0)
                if not playable and cur.fade_out == 0:
                    # Muted or set to 0% while it plays: hand over now
                    nxt.fill(2 * cf + 2 * n)
                    xf = max(edge, min(cf, nxt.length // 2, cur.pos))
                    if cur.end is not None:
                        xf = max(1, min(xf, cur.end - cur.pos))
                    cur.end = cur.pos + xf
                    cur.fade_out = xf
                    self._next = None
                    self._begin(nxt, cur_offset, xf)
                    starts[id(nxt)] = cur_offset
                elif cur.end is not None and cur.end - min(cf, cur.end // 2) < cur.pos + n - cur_offset:
                    # The crossfade may start in this chunk: never longer than half of either file
                    nxt.fill(2 * cf + 2 * n)
                    xf = max(1, min(cf, cur.end // 2, nxt.length // 2))
                    xf = min(xf, cur.end - cur.pos)
                    start_at = cur.end - xf
                    if start_at < cur.pos + n - cur_offset:
                        offset = cur_offset + (start_at - cur.pos)
                        cur.fade_out = xf
                        self._next = None
                        self._begin(nxt, offset, xf)
                        starts[id(nxt)] = offset

        out = np.zeros(n, np.float32)
        for voice in list(self._voices):
            view = voice.view
            level = float(view.volume) if view.is_enabled else 0.0
            offset = starts.get(id(voice), 0)
            before = voice.pos
            out += voice.render(n, offset, level)
            if voice.finished:
                stop = self._samples + offset + (voice.pos - before)
                self.events.append((stop, "stop", voice.name))
                logger.debug(f'MerryGoRound "{self.group}": "{voice.name}" stops at {stop / SAMPLE_RATE:.1f}s')
                self._voices.remove(voice)
                if voice is self._current:
                    self._current = None
        self._samples += n
        return np.clip(out, -32768, 32767).astype(np.int16).reshape(1, -1)

    def _gen(self):
        while True:
            yield self._chunk()

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.gen)
