from enum import Enum
import threading
import time
import numpy as np

from sonorium.obs import logger
import av

LOG_THRESHOLD = 500


class ExclusionGroupCoordinator:
    """
    Coordinates exclusive playback for tracks in a mutual exclusion group.

    When multiple tracks are marked as 'exclusive', only one can play at a time.
    Other exclusive tracks must wait until the playing track finishes AND a
    cooldown period has passed before they can play.

    Key behaviors:
    - On stream start, no exclusive track plays immediately (initial delay)
    - Only one exclusive track can play at a time
    - After a track finishes, there's a mandatory gap before any exclusive track plays
    - Avoids playing the same track twice in a row (unless it's the only exclusive track)

    This is shared across all streams in a ThemeStream.
    """

    # Minimum gap after an exclusive track finishes before another can start (seconds)
    MIN_GAP_AFTER_EXCLUSIVE = 30.0
    # Initial delay before any exclusive track can play on stream start
    INITIAL_DELAY = 60.0

    def __init__(self):
        self._lock = threading.Lock()
        self._playing_track: str | None = None  # Name of currently playing exclusive track
        self._play_end_time: float = 0  # When current track will finish
        self._last_played_track: str | None = None  # Track that played most recently
        self._cooldown_until: float = 0  # No exclusive track can play until this time
        self._registered_tracks: set[str] = set()  # All registered exclusive tracks
        self._start_time: float = time.time()  # When the coordinator was created

    def register_track(self, track_name: str):
        """Register an exclusive track with the coordinator."""
        with self._lock:
            self._registered_tracks.add(track_name)
            logger.debug(f'ExclusionGroup: Registered track "{track_name}" ({len(self._registered_tracks)} total)')

    def try_start_playing(self, track_name: str, duration_seconds: float) -> bool:
        """
        Attempt to start playing an exclusive track.

        Returns True if allowed to play, False otherwise.

        Conditions to play:
        1. Initial delay has passed since stream start
        2. No exclusive track is currently playing
        3. Cooldown period has passed since last track finished
        4. This is not the same track that just played (unless it's the only track)
        """
        with self._lock:
            now = time.time()

            # Check initial delay on stream start
            if now < self._start_time + self.INITIAL_DELAY:
                return False

            # Check if current playing track has finished
            if self._playing_track is not None:
                if now >= self._play_end_time:
                    # Track finished - start cooldown
                    self._last_played_track = self._playing_track
                    self._playing_track = None
                    self._cooldown_until = now + self.MIN_GAP_AFTER_EXCLUSIVE
                    logger.debug(f'ExclusionGroup: "{self._last_played_track}" finished, cooldown until +{self.MIN_GAP_AFTER_EXCLUSIVE}s')
                else:
                    # Track still playing
                    return False

            # Check cooldown period
            if now < self._cooldown_until:
                return False

            # Don't play same track twice in a row (unless only one exclusive track)
            if (self._last_played_track == track_name and
                len(self._registered_tracks) > 1):
                return False

            # All checks passed - start playing
            self._playing_track = track_name
            self._play_end_time = now + duration_seconds
            logger.debug(f'ExclusionGroup: "{track_name}" starting playback (duration: {duration_seconds:.1f}s)')
            return True

    def finish_playing(self, track_name: str):
        """Mark that an exclusive track has finished playing."""
        with self._lock:
            if self._playing_track == track_name:
                now = time.time()
                self._last_played_track = track_name
                self._playing_track = None
                self._play_end_time = 0
                self._cooldown_until = now + self.MIN_GAP_AFTER_EXCLUSIVE
                logger.debug(f'ExclusionGroup: "{track_name}" finished, cooldown until +{self.MIN_GAP_AFTER_EXCLUSIVE}s')

    def is_blocked(self, track_name: str) -> bool:
        """Check if a track is blocked from playing."""
        with self._lock:
            now = time.time()

            # Initial delay check
            if now < self._start_time + self.INITIAL_DELAY:
                return True

            # Check if current track has finished
            if self._playing_track is not None and now >= self._play_end_time:
                self._last_played_track = self._playing_track
                self._playing_track = None
                self._cooldown_until = now + self.MIN_GAP_AFTER_EXCLUSIVE

            # Blocked if another track is playing
            if self._playing_track is not None and self._playing_track != track_name:
                return True

            # Blocked during cooldown
            if now < self._cooldown_until:
                return True

            # Blocked if same track just played (and there are other options)
            if (self._last_played_track == track_name and
                len(self._registered_tracks) > 1):
                return True

            return False

    def get_wait_time(self) -> float:
        """Get seconds until this coordinator might allow a play."""
        with self._lock:
            now = time.time()

            # Initial delay
            if now < self._start_time + self.INITIAL_DELAY:
                return (self._start_time + self.INITIAL_DELAY) - now

            # Currently playing
            if self._playing_track is not None:
                remaining = self._play_end_time - now
                if remaining > 0:
                    return remaining + self.MIN_GAP_AFTER_EXCLUSIVE

            # In cooldown
            if now < self._cooldown_until:
                return self._cooldown_until - now

            return 0

    def get_track_count(self) -> int:
        """Get number of registered exclusive tracks."""
        with self._lock:
            return len(self._registered_tracks)


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
# Sample rate
SAMPLE_RATE = 44100
# Calculated sample counts
CROSSFADE_SAMPLES = int(LOOP_CROSSFADE_DURATION * SAMPLE_RATE)
TRACK_FADE_SAMPLES = int(TRACK_FADE_DURATION * SAMPLE_RATE)


class RecordingMetadata:
    """
    Represents file, metadata, etc. The non-state stuff, on disk. One per file. Immutable
    """

    def __init__(self, path):
        self.path = path
        self._duration_samples = None

    def get_instance(self, theme=None):
        return RecordingThemeInstance(self, theme=theme)

    @property
    def name(self):
        return self.path.stem
    
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

        # PRESENCE: Wrap with fade in/out based on presence value
        if mode == PlaybackMode.PRESENCE and self.presence < 1.0:
            return PresenceMixingStream(base_stream, self)

        return base_stream

    @property
    def name(self):
        return self.meta.name


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
                            logger.info(f'{self.__class__.__name__} Yielding chunk #{i} {data.shape=}, {buffer.shape=}, {vol_mean=}')
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
        """Create a new decoder generator for the audio file"""
        resampler = av.AudioResampler(format='s16', layout='mono', rate=SAMPLE_RATE)
        container = av.open(self.instance.meta.path)
        
        if len(container.streams.audio) == 0:
            raise ValueError('No audio stream')
        stream = next(iter(container.streams.audio))
        
        def decode():
            try:
                for frame_orig in container.decode(stream):
                    for frame_resamp in resampler.resample(frame_orig):
                        data = frame_resamp.to_ndarray()
                        # Downmix to mono
                        data = data.mean(axis=0).astype(np.float32)
                        # Apply instance volume
                        data = data * self.instance.volume
                        yield data
            finally:
                container.close()
        
        return decode()

    def _gen(self):
        """Main generator with crossfade logic"""
        
        # Get track duration for crossfade timing
        track_duration = self.instance.meta.duration_samples
        crossfade_start = max(0, track_duration - CROSSFADE_SAMPLES)
        
        logger.info(f'CrossfadeStream: {self.instance.name} duration={track_duration} samples ({track_duration/SAMPLE_RATE:.1f}s), crossfade at {crossfade_start} ({crossfade_start/SAMPLE_RATE:.1f}s)')
        
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
        
        while True:
            # Fill buffer from current decoder
            while len(buffer) < self.CHUNK_SIZE * 2:
                try:
                    chunk = next(current_decoder)
                    buffer = np.concatenate([buffer, chunk.flatten()])
                except StopIteration:
                    # Current track ended - should have transitioned already
                    # Start fresh if we somehow got here
                    logger.debug(f'CrossfadeStream: Track ended, starting fresh')
                    current_decoder = self._create_decoder()
                    samples_played = 0
                    in_crossfade = False
                    continue
            
            # Check if we should start crossfade
            if not in_crossfade and samples_played >= crossfade_start:
                logger.info(f'CrossfadeStream: Starting crossfade at sample {samples_played}')
                in_crossfade = True
                crossfade_position = 0
                next_decoder = self._create_decoder()
                next_buffer = np.empty(0, dtype=np.float32)
            
            # If in crossfade, also fill next_buffer
            if in_crossfade:
                while len(next_buffer) < self.CHUNK_SIZE * 2:
                    try:
                        chunk = next(next_decoder)
                        next_buffer = np.concatenate([next_buffer, chunk.flatten()])
                    except StopIteration:
                        break
            
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
                    logger.info(f'CrossfadeStream: Crossfade complete, switching to new track instance')
                    current_decoder = next_decoder
                    buffer = next_buffer
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
                logger.info(f'CrossfadeStream [{status}]: chunk #{chunk_count}, samples={samples_played}, vol={vol_mean}')

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

        logger.info(f'SparsePlaybackStream: {self.instance.name} - short file ({file_duration_seconds:.1f}s), using sparse playback' +
                    (', exclusive=True' if self.instance.exclusive else ''))

        # Pre-generate fade curves for the short file
        # Use shorter fade for very short files
        fade_duration = min(TRACK_FADE_DURATION, file_duration_seconds / 3)
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
            return self.exclusion_coordinator.is_blocked(self.instance.name)

        def try_start_exclusive():
            """Try to claim exclusive playback slot. Returns True if allowed."""
            if not self.instance.exclusive or self.exclusion_coordinator is None:
                return True
            return self.exclusion_coordinator.try_start_playing(
                self.instance.name,
                file_duration_seconds
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

            # On first play, delay with a random portion of the interval
            # This prevents all sparse tracks from playing at stream start
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

            # Now output silence for the interval
            silent_samples = get_silent_interval()
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

    def __init__(self, base_stream, instance: RecordingThemeInstance):
        self.base_stream = base_stream
        self.instance = instance
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
                # Recalculate state for new presence
                if presence >= 1.0 and target_gain < 1.0:
                    target_gain = 1.0
                    fade_position = 0
                elif presence <= 0.0 and target_gain > 0.0:
                    target_gain = 0.0
                    fade_position = 0

            # Check if it's time to change state
            samples_until_change -= self.CHUNK_SIZE
            if samples_until_change <= 0 and 0 < presence < 1.0:
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