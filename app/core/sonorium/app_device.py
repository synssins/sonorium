"""
Standalone Sonorium device - no Home Assistant dependencies.

Replaces device.py with local audio output support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sonorium.audio_output import AudioOutputDevice, AudioMixer
from sonorium.obs import logger
from sonorium.recording import RecordingMetadata, ExclusionGroupCoordinator

if TYPE_CHECKING:
    from sonorium.theme import ThemeDefinition


SAMPLE_RATE = 44100


@dataclass
class AudioDevice:
    """Represents a local audio output device."""
    id: int | str
    name: str
    channels: int
    sample_rate: float
    is_default: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> 'AudioDevice':
        return cls(**data)


@dataclass
class SonoriumApp:
    """
    Standalone Sonorium application controller.

    Manages themes, audio mixing, and playback to local audio devices.
    """
    path_audio: Path
    themes: list = field(default_factory=list)
    theme_metas: dict = field(default_factory=dict)

    # Audio output
    _mixer: AudioMixer | None = field(default=None, repr=False)
    _current_device: AudioDevice | None = field(default=None, repr=False)
    _enabled_network_speakers: set = field(default_factory=set, repr=False)

    # Playback state
    current_theme: str | None = field(default=None)
    current_preset: str | None = field(default=None)  # Track current preset for restore
    playback_state: str = field(default='stopped')  # 'stopped', 'playing', 'paused'
    master_volume: float = field(default=0.8)

    def __post_init__(self):
        # Convert string path to Path
        if isinstance(self.path_audio, str):
            self.path_audio = Path(self.path_audio)

        # Create audio directory if needed
        if not self.path_audio.exists():
            logger.warning(f'Audio path "{self.path_audio}" does not exist. Creating...')
            self.path_audio.mkdir(parents=True, exist_ok=True)

        # Load themes
        self._load_themes()

        # Initialize audio output
        self._init_audio()

    def _load_themes(self):
        """Scan and load themes from audio directory."""
        from sonorium.theme import ThemeDefinition

        theme_folders = [f for f in self.path_audio.iterdir() if f.is_dir()]
        logger.info(f'Scanning for themes in "{self.path_audio}"...')
        logger.info(f'Found {len(theme_folders)} theme folder(s)')

        # Install bundled themes if none exist
        if not theme_folders:
            self._install_bundled_themes()
            theme_folders = [f for f in self.path_audio.iterdir() if f.is_dir()]

        self.themes = []
        self.theme_metas = {}

        for folder in theme_folders:
            audio_files = [
                f for f in folder.iterdir()
                if f.is_file() and f.suffix.lower() in ['.mp3', '.wav', '.flac', '.ogg']
            ]

            if audio_files:
                theme_name = folder.name
                self.theme_metas[theme_name] = [RecordingMetadata(p) for p in audio_files]
                theme_def = ThemeDefinition(sonorium=self, name=theme_name)
                self.themes.append(theme_def)
                logger.info(f'Loaded theme "{theme_name}" with {len(audio_files)} audio files')

        if self.themes:
            self.current_theme = self.themes[0].name
            logger.info(f'Set default theme to: "{self.current_theme}"')

    def _install_bundled_themes(self):
        """Install bundled themes from the application package."""
        import shutil
        import sys

        # Determine bundled themes location
        if getattr(sys, 'frozen', False):
            # Running as compiled EXE - themes are in sys._MEIPASS
            bundled_path = Path(sys._MEIPASS) / 'themes'
        else:
            # Running as script - themes are in project root
            bundled_path = Path(__file__).parent.parent / 'themes'

        logger.info(f'Looking for bundled themes at: {bundled_path}')

        if not bundled_path.exists():
            logger.warning(f'Bundled themes directory not found: {bundled_path}')
            return

        for src in bundled_path.iterdir():
            if src.is_dir() and src.name != '__pycache__':
                dst = self.path_audio / src.name
                if not dst.exists():
                    try:
                        shutil.copytree(str(src), str(dst))
                        logger.info(f'Installed bundled theme: {src.name}')
                    except Exception as e:
                        logger.error(f'Failed to install theme {src.name}: {e}')

    def _init_audio(self):
        """Initialize audio output system."""
        try:
            devices = AudioOutputDevice.list_devices()
            if devices:
                default = next((d for d in devices if d['is_default']), devices[0])
                self._current_device = AudioDevice.from_dict(default)
                logger.info(f'Default audio device: {self._current_device.name}')

            self._mixer = AudioMixer()
        except Exception as e:
            logger.error(f'Failed to initialize audio: {e}')
            self._mixer = None

    def list_audio_devices(self) -> list[AudioDevice]:
        """Get list of available audio output devices."""
        devices = AudioOutputDevice.list_devices()
        return [AudioDevice.from_dict(d) for d in devices]

    def set_audio_device(self, device_id: int | str):
        """Change the audio output device."""
        was_playing = self.playback_state == 'playing'

        if was_playing:
            self.stop()

        try:
            output = AudioOutputDevice(device_id=device_id)
            self._mixer = AudioMixer(output_device=output)

            devices = self.list_audio_devices()
            self._current_device = next((d for d in devices if d.id == device_id), None)
            logger.info(f'Changed audio device to: {device_id}')

            if was_playing:
                self.play()
        except Exception as e:
            logger.error(f'Failed to set audio device: {e}')
            raise

    def get_enabled_network_speakers(self) -> list[str]:
        """Get list of enabled network speaker IDs."""
        return list(self._enabled_network_speakers)

    def set_enabled_network_speakers(self, speaker_ids: list[str]):
        """Set which network speakers are enabled for streaming."""
        self._enabled_network_speakers = set(speaker_ids)
        logger.info(f'Enabled network speakers: {speaker_ids}')
        # TODO: Start/stop streaming to speakers based on enabled list

    def get_theme(self, name: str) -> 'ThemeDefinition | None':
        """Get a theme by name."""
        return next((t for t in self.themes if t.name == name), None)

    def refresh_themes(self):
        """Rescan themes from disk without interrupting playback."""
        current = self.current_theme

        # Reload themes from disk
        # This does NOT affect currently playing streams - they continue uninterrupted
        self._load_themes()

        # Restore current theme if still exists
        if current and self.get_theme(current):
            self.current_theme = current
        elif self.themes:
            self.current_theme = self.themes[0].name

        # Note: We intentionally do NOT restart/crossfade playback here
        # The old streams continue playing with their existing file handles
        # User can manually switch themes/presets if they want the new content
        logger.info(f'Themes refreshed. Current theme: {self.current_theme}')

    def play(self, theme_name: str | None = None, preset_id: str | None = None):
        """Start playback of a theme, optionally with a preset."""
        if theme_name:
            self.current_theme = theme_name

        if not self.current_theme:
            logger.warning('No theme selected')
            return

        theme = self.get_theme(self.current_theme)
        if not theme:
            logger.error(f'Theme not found: {self.current_theme}')
            return

        if not self._mixer:
            logger.error('Audio mixer not initialized')
            return

        # Determine which preset to use
        # Priority: explicit preset_id > current_preset (restore) > default preset
        effective_preset_id = preset_id

        # If no preset specified, try to restore current preset
        if not effective_preset_id and self.current_preset:
            effective_preset_id = self.current_preset
            logger.info(f'Restoring previous preset: {effective_preset_id}')

        # If still no preset, try to find the default preset
        if not effective_preset_id:
            presets = theme._metadata.get('presets', {})
            for pid, pdata in presets.items():
                if pdata.get('is_default', False):
                    effective_preset_id = pid
                    logger.info(f'Using default preset: {pdata.get("name", pid)}')
                    break

        # Save current preset for future restore
        self.current_preset = effective_preset_id

        # Load preset if we have one
        if effective_preset_id:
            self._apply_preset(theme, effective_preset_id)

        # Stop any current playback
        self._mixer.stop()
        self._mixer.clear_streams()

        # Create exclusion coordinator for this theme
        exclusion_coordinator = ExclusionGroupCoordinator()

        # Start mixer
        self._mixer.master_volume = self.master_volume
        self._mixer.start()

        # Add streams for each enabled track
        # Use consistent naming: theme_ThemeName_trackname
        group_name = f"theme_{self.current_theme}"
        for instance in theme.instances:
            if instance.is_enabled:
                stream = instance.get_stream(exclusion_coordinator)
                stream_id = f"{group_name}_{instance.name}"
                self._mixer.add_stream(stream_id, stream, group=group_name)

        self.playback_state = 'playing'
        logger.info(f'Playing theme: {self.current_theme}' + (f' with preset: {effective_preset_id}' if effective_preset_id else ''))

    def _apply_preset(self, theme: 'ThemeDefinition', preset_id: str):
        """Apply a preset to a theme."""
        from sonorium.recording import PlaybackMode

        # Presets are stored in metadata.json under 'presets' key
        presets = theme._metadata.get('presets', {})
        if not presets:
            logger.warning(f'No presets in metadata for theme: {theme.name}')
            return

        preset_data = presets.get(preset_id)
        if not preset_data:
            logger.warning(f'Preset not found: {preset_id}')
            return

        # Apply track settings from preset
        track_settings = preset_data.get('tracks', {})
        for instance in theme.instances:
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

        logger.info(f'Applied preset: {preset_data.get("name", preset_id)}')

    def crossfade_to(self, theme_name: str | None = None, preset_id: str | None = None):
        """
        Crossfade to a new theme/preset without stopping playback.

        For theme changes: fades out old streams and fades in new ones.
        For preset changes on same theme: updates volumes in-place, only
        fades out/in tracks that changed mute state.
        """
        if not self._mixer:
            logger.error('Audio mixer not initialized')
            return

        # If not currently playing, just do a normal play
        if self.playback_state != 'playing':
            self.play(theme_name, preset_id)
            return

        target_theme_name = theme_name or self.current_theme
        if not target_theme_name:
            logger.warning('No theme specified for crossfade')
            return

        theme = self.get_theme(target_theme_name)
        if not theme:
            logger.error(f'Theme not found: {target_theme_name}')
            return

        # Check if this is a preset change on the same theme
        is_same_theme = (target_theme_name == self.current_theme)

        # Determine which preset to use
        effective_preset_id = preset_id

        # If no preset specified, try to find the default preset
        if not effective_preset_id:
            presets = theme._metadata.get('presets', {})
            for pid, pdata in presets.items():
                if pdata.get('is_default', False):
                    effective_preset_id = pid
                    logger.info(f'Using default preset: {pdata.get("name", pid)}')
                    break

        if is_same_theme:
            # Preset change on same theme - update in place
            self._apply_preset_live(theme, effective_preset_id)
        else:
            # Theme change - full crossfade
            self._crossfade_theme(theme, effective_preset_id)

        # Update current theme and preset
        self.current_theme = target_theme_name
        self.current_preset = effective_preset_id

        logger.info(f'{"Applied preset to" if is_same_theme else "Crossfading to"} theme: {target_theme_name}' +
                   (f' with preset: {effective_preset_id}' if effective_preset_id else ''))

    def _apply_preset_live(self, theme: 'ThemeDefinition', preset_id: str | None):
        """
        Apply a preset to a currently playing theme without restarting streams.

        - Tracks that stay enabled: update volume in place
        - Tracks that become disabled: fade out and remove
        - Tracks that become enabled: create and fade in
        """
        # Get current stream state before applying preset
        current_streams = {}  # instance.name -> stream_id
        for stream_id in self._mixer.get_stream_ids():
            # Stream IDs are like "theme_ThemeName_trackname" or "preset_new_XXX_trackname"
            # Extract the track name (last part after last underscore that matches an instance)
            for instance in theme.instances:
                if stream_id.endswith(f"_{instance.name}"):
                    current_streams[instance.name] = stream_id
                    break

        # Apply preset settings to theme instances
        if preset_id:
            self._apply_preset(theme, preset_id)

        # IMPORTANT: Prepare new streams FIRST before any fade operations
        # This ensures audio is ready immediately, avoiding latency gaps
        exclusion_coordinator = ExclusionGroupCoordinator()
        group_name = f"theme_{theme.name}"

        # Collect operations to perform
        volume_updates = []  # (stream_id, volume)
        streams_to_add = []  # (stream_id, stream)
        streams_to_remove = []  # stream_id

        for instance in theme.instances:
            stream_id = current_streams.get(instance.name)
            previously_playing = stream_id is not None

            if instance.is_enabled:
                if previously_playing:
                    # Track stays enabled - just update volume
                    volume_updates.append((stream_id, instance.volume, instance.name))
                else:
                    # Track was muted, now enabled - prepare stream for fade in
                    # Use random_start=True so it doesn't always start from beginning
                    stream = instance.get_stream(exclusion_coordinator, random_start=True)
                    new_stream_id = f"{group_name}_{instance.name}"
                    streams_to_add.append((new_stream_id, stream, instance.name))
            else:
                if previously_playing:
                    # Track was playing, now muted - mark for fade out
                    streams_to_remove.append((stream_id, instance.name))

        # Now apply all operations (streams are already prepared)
        for stream_id, volume, name in volume_updates:
            self._mixer.set_stream_volume(stream_id, volume, fade=True)
            logger.debug(f'Updated volume for {name} to {volume}')

        for stream_id, stream, name in streams_to_add:
            self._mixer.add_stream(stream_id, stream, group=group_name, fade_in=True)
            logger.debug(f'Fading in newly enabled track: {name}')

        for stream_id, name in streams_to_remove:
            self._mixer.remove_stream(stream_id, fade_out=True)
            logger.debug(f'Fading out newly muted track: {name}')

    def _crossfade_theme(self, theme: 'ThemeDefinition', preset_id: str | None):
        """
        Full crossfade to a different theme.
        Fades out all current streams and fades in new theme's streams.
        """
        # Apply preset settings to theme instances
        if preset_id:
            self._apply_preset(theme, preset_id)

        # Create exclusion coordinator for new theme
        exclusion_coordinator = ExclusionGroupCoordinator()
        group_name = f"theme_{theme.name}"

        # IMPORTANT: Prepare all new streams FIRST before fading out old ones
        # This ensures new audio is ready immediately when crossfade starts
        # Opening files and decoding can cause latency - do it upfront
        prepared_streams = []
        for instance in theme.instances:
            if instance.is_enabled:
                stream = instance.get_stream(exclusion_coordinator)
                stream_id = f"{group_name}_{instance.name}"
                prepared_streams.append((stream_id, stream))

        # Now fade out all current streams
        self._mixer.clear_streams(fade_out=True)

        # Add new streams with fade in (they're already prepared/primed)
        for stream_id, stream in prepared_streams:
            self._mixer.add_stream(stream_id, stream, group=group_name, fade_in=True)

    def stop(self):
        """Stop playback."""
        if self._mixer:
            self._mixer.stop()

        self.playback_state = 'stopped'
        logger.info('Playback stopped')

    def pause(self):
        """Pause playback (stops output but remembers state)."""
        if self._mixer and self.playback_state == 'playing':
            self._mixer.stop()
            self.playback_state = 'paused'
            logger.info('Playback paused')

    def resume(self):
        """Resume paused playback."""
        if self.playback_state == 'paused':
            self.play()

    def set_volume(self, volume: float):
        """Set master volume (0.0 to 1.0)."""
        self.master_volume = max(0.0, min(1.0, volume))
        if self._mixer:
            self._mixer.master_volume = self.master_volume
        logger.debug(f'Volume set to: {self.master_volume}')

    def get_status(self) -> dict:
        """Get current playback status."""
        return {
            'playback_state': self.playback_state,
            'current_theme': self.current_theme,
            'current_preset': self.current_preset,
            'master_volume': self.master_volume,
            'audio_device': self._current_device.name if self._current_device else None,
            'theme_count': len(self.themes)
        }
