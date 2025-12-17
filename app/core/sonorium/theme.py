"""
Theme definitions for Sonorium.

Standalone version without fmtr.tools dependencies.
"""

import json
import re
import time
from functools import cached_property
from pathlib import Path

import numpy as np

from sonorium.obs import logger
from sonorium.recording import LOG_THRESHOLD, ExclusionGroupCoordinator, PlaybackMode

try:
    import av
except ImportError:
    from sonorium._av_compat import av

# Default output gain multiplier (now controlled via device.master_volume)
DEFAULT_OUTPUT_GAIN = 6.0

# Default threshold for short file detection (seconds)
DEFAULT_SHORT_FILE_THRESHOLD = 15.0


def sanitize(text: str) -> str:
    """Sanitize a string for use as an ID."""
    # Replace spaces and special chars with underscores
    text = re.sub(r'[^\w\-]', '_', text)
    # Remove consecutive underscores
    text = re.sub(r'_+', '_', text)
    # Remove leading/trailing underscores
    text = text.strip('_')
    return text.lower()


class IndexList(list):
    """List that supports dict-like access by item name/id attribute."""

    def __getattr__(self, name):
        """Allow attribute-style access to grouped items."""
        if name.startswith('_'):
            raise AttributeError(name)

        # Return a dict mapping the attribute value to item
        result = {}
        for item in self:
            if hasattr(item, name):
                key = getattr(item, name)
                result[key] = item
        return result


class ThemeDefinition:
    """
    A theme is a collection of audio files that play together.

    ThemeDefinition: What recordings are involved, volumes. User defines these via the UI.
    ThemeStream: One instance per client/connection. Has a RecordingStream for each recording.
    """

    def __init__(self, sonorium, name):
        self.sonorium = sonorium
        self.name = name

        # Short file threshold (seconds) - files shorter than this use sparse playback
        self.short_file_threshold = DEFAULT_SHORT_FILE_THRESHOLD

        # Load metadata.json if exists
        self._metadata = {}
        self._load_metadata()

        # Use theme-specific recordings
        if name in self.sonorium.theme_metas:
            theme_metas = self.sonorium.theme_metas[name]
        else:
            theme_metas = []

        # Create instances for each recording
        self.instances = IndexList(meta.get_instance(theme=self) for meta in theme_metas)

        # Apply track settings from metadata
        self._apply_track_settings()

        self.streams: list['ThemeStream'] = []

    def _get_metadata_path(self) -> Path:
        """Get path to metadata.json for this theme."""
        return self.sonorium.path_audio / self.name / 'metadata.json'

    def _load_metadata(self):
        """Load metadata.json if it exists."""
        meta_path = self._get_metadata_path()
        logger.debug(f'Looking for metadata at: {meta_path}')
        if meta_path.exists():
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    self._metadata = json.load(f)
                # Apply theme-level settings
                if 'short_file_threshold' in self._metadata:
                    self.short_file_threshold = float(self._metadata['short_file_threshold'])

                # Log what was loaded
                track_count = len(self._metadata.get('tracks', {}))
                preset_count = len(self._metadata.get('presets', {}))
                logger.info(f'Loaded metadata for theme "{self.name}": {track_count} track settings, {preset_count} presets')
            except PermissionError as e:
                logger.error(f'Permission denied reading metadata for {self.name}: {e}')
                self._metadata = {}
            except json.JSONDecodeError as e:
                logger.error(f'Invalid JSON in metadata for {self.name}: {e}')
                self._metadata = {}
            except Exception as e:
                logger.error(f'Failed to load metadata for {self.name}: {e}')
                self._metadata = {}
        else:
            logger.debug(f'No metadata.json found for theme: {self.name}')

    def _apply_track_settings(self):
        """Apply track settings from metadata to instances."""
        track_settings = self._metadata.get('tracks', {})

        for instance in self.instances:
            # Try both with and without file extension
            settings = track_settings.get(instance.name) or track_settings.get(f'{instance.name}.mp3', {})

            if settings:
                if 'volume' in settings:
                    instance.volume = float(settings['volume'])
                if 'presence' in settings:
                    instance.presence = float(settings['presence'])
                if 'muted' in settings:
                    instance.is_enabled = not settings['muted']
                if 'playback_mode' in settings:
                    try:
                        instance.playback_mode = PlaybackMode(settings['playback_mode'])
                    except ValueError:
                        instance.playback_mode = PlaybackMode.AUTO
                if 'exclusive' in settings:
                    instance.exclusive = bool(settings['exclusive'])
                if 'seamless_loop' in settings:
                    instance.crossfade_enabled = bool(settings['seamless_loop'])

                logger.debug(f'Applied settings for track {instance.name}: vol={instance.volume}, presence={instance.presence}, mode={instance.playback_mode.value}')

    def save_metadata(self):
        """Save current track settings to metadata.json."""
        meta_path = self._get_metadata_path()

        # Build track settings from current state
        tracks = {}
        for instance in self.instances:
            tracks[instance.name] = {
                'volume': instance.volume,
                'presence': instance.presence,
                'muted': not instance.is_enabled,
                'playback_mode': instance.playback_mode.value,
                'exclusive': instance.exclusive,
                'seamless_loop': instance.crossfade_enabled
            }

        # Update metadata
        self._metadata['tracks'] = tracks
        self._metadata['short_file_threshold'] = self.short_file_threshold

        # Preserve existing fields
        if 'name' not in self._metadata:
            self._metadata['name'] = self.name

        try:
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(self._metadata, f, indent=2)
            logger.info(f'Saved metadata for theme: {self.name}')
        except PermissionError as e:
            logger.error(f'Permission denied saving metadata for {self.name}: {e}. Check that the themes folder is writable.')
            raise
        except Exception as e:
            logger.error(f'Failed to save metadata for {self.name}: {e}')
            raise

    def get_presets(self) -> list:
        """Get presets from metadata."""
        presets_dict = self._metadata.get('presets', {})
        presets = []
        for preset_id, preset_data in presets_dict.items():
            presets.append({
                'id': preset_id,
                'name': preset_data.get('name', preset_id),
                'is_default': preset_data.get('is_default', False),
                'track_settings': preset_data.get('tracks', {})
            })
        return presets

    def save_preset(self, preset_id: str, name: str, track_settings: dict = None):
        """Save a preset to metadata."""
        if 'presets' not in self._metadata:
            self._metadata['presets'] = {}

        # If no track settings provided, capture current state
        if track_settings is None:
            track_settings = {}
            for instance in self.instances:
                track_settings[instance.name] = {
                    'volume': instance.volume,
                    'presence': instance.presence,
                    'playback_mode': instance.playback_mode.value,
                    'seamless_loop': instance.crossfade_enabled,
                    'exclusive': instance.exclusive,
                    'muted': not instance.is_enabled
                }

        self._metadata['presets'][preset_id] = {
            'name': name,
            'is_default': False,
            'tracks': track_settings
        }

        self.save_metadata()
        return self._metadata['presets'][preset_id]

    def delete_preset(self, preset_id: str):
        """Delete a preset from metadata."""
        if 'presets' in self._metadata and preset_id in self._metadata['presets']:
            del self._metadata['presets'][preset_id]
            self.save_metadata()

    @cached_property
    def id(self):
        return sanitize(self.name)

    def get_stream(self):
        theme = ThemeStream(self)
        self.streams.append(theme)
        logger.info(f'ThemeDefinition {self.name}: Created new ThemeStream (total: {len(self.streams)} streams)')
        return theme


class ThemeStream:
    """
    A live stream instance of a theme.

    One instance per client/connection. Has a RecordingStream for each recording in the ThemeDefinition.
    """

    def __init__(self, theme_def: ThemeDefinition):
        self.theme_def = theme_def

        # Create shared exclusion coordinator for tracks marked as exclusive
        self.exclusion_coordinator = ExclusionGroupCoordinator()

        # Create streams, passing the exclusion coordinator
        self.recording_streams = [
            instance.get_stream(exclusion_coordinator=self.exclusion_coordinator)
            for instance in theme_def.instances
        ]

    @cached_property
    def chunk_silence(self):
        from sonorium.recording import RecordingThemeStream
        data = np.zeros((1, RecordingThemeStream.CHUNK_SIZE), np.int16)
        return data

    def iter_chunks(self):
        """Generate mixed audio chunks from all enabled recordings."""
        while True:
            data_recs = [
                next(streams)
                for streams in self.recording_streams
                if streams.instance.is_enabled
            ]

            if not data_recs:
                data_recs.append(self.chunk_silence)

            # Stack all recordings
            data = np.vstack(data_recs)

            # Proper audio mixing: sum the signals, then normalize
            mixed = data.astype(np.float32).sum(axis=0)

            # Normalize by sqrt(n) to prevent clipping
            n_tracks = len(data_recs)
            if n_tracks > 1:
                mixed = mixed / np.sqrt(n_tracks)

            # Apply output gain
            output_gain = getattr(self.theme_def.sonorium, 'master_volume', DEFAULT_OUTPUT_GAIN)
            mixed = mixed * output_gain

            # Clip to int16 range
            mixed = np.clip(mixed, -32768, 32767)
            data = mixed.astype(np.int16).reshape(1, -1)

            yield data

    def __iter__(self):
        """Iterate as MP3 stream for HTTP streaming."""
        from io import BytesIO

        # Create in-memory MP3 encoder
        buffer = BytesIO()
        output = av.open(buffer, mode="w", format='mp3')
        bitrate = 128_000
        out_stream = output.add_stream(codec_name='mp3', rate=44100)
        out_stream.bit_rate = bitrate

        iter_chunks = self.iter_chunks()

        start_time = time.time()
        audio_time = 0.0

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

                    # Maintain real-time pacing
                    now = time.time()
                    ahead = audio_time - (now - start_time)
                    if ahead > 0:
                        time.sleep(ahead)

                    if i % LOG_THRESHOLD == 0:
                        logger.debug(f'Streaming {audio_time:.1f}s...')

        finally:
            logger.info('Closing transcoder...')
            iter_chunks.close()
            output.close()
