import numpy as np

from sonorium.obs import logger
from fmtr.tools import av

LOG_THRESHOLD = 500

# Crossfade duration in seconds for loop transitions
LOOP_CROSSFADE_DURATION = 3.0
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

    def get_instance(self):
        return RecordingThemeInstance(self)

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

    def __init__(self, meta: RecordingMetadata):
        self.meta = meta
        self.volume = 1.0  # Amplitude multiplier (keep at 1.0 for now)
        self.presence = 1.0  # How often this track plays: 1.0 = always, 0.5 = half the time, 0 = never
        self.is_enabled = True  # Master enable/disable (mute)
        self.crossfade_enabled = True  # Enable crossfade looping by default

    def get_stream(self):
        # Get base stream (with or without crossfade looping)
        if self.crossfade_enabled:
            base_stream = CrossfadeRecordingStream(self)
        else:
            base_stream = RecordingThemeStream(self)

        # Wrap with presence-based mixing if presence < 1.0
        # This allows tracks to fade in/out of the mix based on presence setting
        if self.presence < 1.0:
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