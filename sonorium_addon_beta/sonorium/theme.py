import time
from functools import cached_property

import numpy as np

from sonorium.obs import logger
from sonorium.recording import LOG_THRESHOLD
from fmtr.tools import av
from fmtr.tools.iterator_tools import IndexList
from fmtr.tools.string_tools import sanitize

# Default output gain multiplier (now controlled via device.master_volume)
DEFAULT_OUTPUT_GAIN = 6.0


class ThemeDefinition:
    """
    A theme containing multiple audio recordings that are mixed together.
    
    Each theme represents a soundscape (e.g., "Thunder", "Rain Forest") with
    multiple layered audio tracks that play simultaneously.
    """

    def __init__(self, sonorium, name):
        self.sonorium = sonorium
        self.name = name

        # Use theme-specific recordings instead of all recordings
        if name in self.sonorium.theme_metas:
            theme_metas = self.sonorium.theme_metas[name]
        else:
            # Fallback to all recordings for backwards compatibility
            theme_metas = self.sonorium.metas
        
        self.instances = IndexList(meta.get_instance() for meta in theme_metas)
        self.streams: list[ThemeStream] = []

    @cached_property
    def url(self) -> str:
        from sonorium.settings import settings
        return f'{settings.stream_url}/stream/{self.id}'

    @cached_property
    def id(self):
        return sanitize(self.name)

    def get_stream(self):
        theme = ThemeStream(self)
        self.streams.append(theme)
        return theme


class ThemeStream:
    """
    A streaming instance for a theme.
    
    One ThemeStream is created per client/connection. Each stream has its own
    set of recording streams that are mixed together in real-time.
    """

    def __init__(self, theme_def: ThemeDefinition):
        self.theme_def = theme_def
        # Create streams for ALL recordings - no enable/disable filtering
        self.recording_streams = [instance.get_stream() for instance in theme_def.instances]

    @cached_property
    def chunk_silence(self):
        from sonorium.recording import RecordingThemeStream
        data = np.zeros((1, RecordingThemeStream.CHUNK_SIZE), np.int16)
        return data

    def iter_chunks(self):
        while True:
            # Get chunks from ALL recording streams (no is_enabled check)
            data_recs = [next(stream) for stream in self.recording_streams]
            
            if not data_recs:
                # Shouldn't happen, but safety fallback
                data_recs.append(self.chunk_silence)
            
            # Stack all recordings
            data = np.vstack(data_recs)
            
            # Proper audio mixing: sum the signals, then normalize to prevent clipping
            # Using float32 for intermediate calculation to avoid overflow
            mixed = data.astype(np.float32).sum(axis=0)
            
            # Soft clipping / normalization to prevent distortion
            # Divide by sqrt(n) for a good balance between volume and avoiding clipping
            n_tracks = len(data_recs)
            if n_tracks > 1:
                # Use sqrt(n) normalization - louder than mean, but prevents harsh clipping
                mixed = mixed / np.sqrt(n_tracks)
            
            # Apply output gain boost (use device master_volume if available)
            output_gain = getattr(self.theme_def.sonorium, 'master_volume', DEFAULT_OUTPUT_GAIN)
            mixed = mixed * output_gain
            
            # Clip to int16 range and convert back
            mixed = np.clip(mixed, -32768, 32767)
            data = mixed.astype(np.int16).reshape(1, -1)
            
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
