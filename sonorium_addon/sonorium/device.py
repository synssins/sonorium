"""
Sonorium Device - Core device state and configuration.

Manages themes, media players, and playback state.
MQTT entities are handled separately by SonoriumMQTTManager.
"""
from dataclasses import dataclass, field, fields
from functools import cached_property
from pathlib import Path
from typing import Self, TYPE_CHECKING

import homeassistant_api

from sonorium.obs import logger
from sonorium.recording import RecordingMetadata
from sonorium.utils import IndexList

if TYPE_CHECKING:
    from sonorium.theme import ThemeDefinition


@dataclass
class MediaState:
    entity_id: str
    state: str
    friendly_name: str = ""
    supported_features: int = 0

    @classmethod
    def from_state(cls, state) -> Self:
        data = state.model_dump()
        data |= data.pop('attributes', {})
        allowed = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in allowed}
        if 'friendly_name' not in filtered or filtered['friendly_name'] is None:
            filtered['friendly_name'] = filtered.get('entity_id', 'Unknown')
        return cls(**filtered)


@dataclass(kw_only=True)
class Sonorium:
    """
    Sonorium device state container.

    Holds themes, media players, and playback configuration.
    Does not handle MQTT - that's done by SonoriumMQTTManager.
    """

    themes: IndexList = field(default_factory=IndexList, metadata=dict(exclude=True))
    metas: IndexList = field(default_factory=IndexList, metadata=dict(exclude=True))
    theme_metas: dict = field(default_factory=dict, metadata=dict(exclude=True))

    client_ha: homeassistant_api.Client | None = field(default=None, metadata=dict(exclude=True))

    path_audio_str: str = field(metadata=dict(exclude=True))

    # Master volume as gain multiplier (default 6.0 = 60%)
    master_volume: float = field(default=6.0, metadata=dict(exclude=True))

    # Playback state: 'stopped', 'playing', 'paused'
    playback_state: str = field(default='stopped', metadata=dict(exclude=True))

    # Media player states
    media_player_states: IndexList = field(default_factory=IndexList, metadata=dict(exclude=True))

    def __post_init__(self):
        # Lazy import to avoid circular dependency
        from sonorium.theme import ThemeDefinition

        if not self.path_audio.exists():
            logger.warning(f'Audio path "{self.path_audio}" does not exist. Will be created.')
            self.path_audio.mkdir(parents=True, exist_ok=True)

        theme_folders = [folder for folder in self.path_audio.iterdir() if folder.is_dir()]

        logger.info(f'Scanning for themes in "{self.path_audio}"...')
        logger.info(f'Found {len(theme_folders)} theme folder(s): {[f.name for f in theme_folders]}')

        if not theme_folders:
            # Install bundled themes on first run
            bundled_themes_path = Path('/app/themes')
            if bundled_themes_path.exists():
                bundled_folders = [f for f in bundled_themes_path.iterdir() if f.is_dir()]
                if bundled_folders:
                    logger.info(f'Installing {len(bundled_folders)} bundled theme(s) on first run...')
                    import shutil
                    for src_folder in bundled_folders:
                        dst_folder = self.path_audio / src_folder.name
                        shutil.copytree(str(src_folder), str(dst_folder))
                        logger.info(f'Installed bundled theme: {src_folder.name}')
                    theme_folders = [folder for folder in self.path_audio.iterdir() if folder.is_dir()]

            # Fallback: create empty example folder if no bundled themes
            if not theme_folders:
                logger.warning(f'No bundled themes found. Creating example structure...')
                example_theme = self.path_audio / 'example_theme'
                example_theme.mkdir(exist_ok=True)
                logger.info(f'Created example theme folder: {example_theme}')
                theme_folders = [example_theme]

        self.themes = IndexList()
        self.theme_metas = {}

        for folder in theme_folders:
            audio_files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in ['.mp3', '.wav', '.flac', '.ogg']]

            if audio_files:
                theme_name = folder.name
                self.theme_metas[theme_name] = IndexList(RecordingMetadata(path) for path in audio_files)

                # Read UUID from metadata.json if it exists
                theme_id = None
                metadata_path = folder / "metadata.json"
                if metadata_path.exists():
                    try:
                        import json
                        metadata = json.loads(metadata_path.read_text())
                        theme_id = metadata.get("id")
                    except Exception:
                        pass  # Fall back to sanitized folder name

                theme_def = ThemeDefinition(sonorium=self, name=theme_name, theme_id=theme_id)
                self.themes.append(theme_def)
                logger.info(f'Loaded theme "{theme_name}" with {len(audio_files)} audio files')
            else:
                logger.warning(f'Theme folder "{folder.name}" contains no audio files')

        self.metas = IndexList()
        for theme_recordings in self.theme_metas.values():
            self.metas.extend(theme_recordings)

        if not self.themes:
            logger.warning(f'No themes with audio files found in "{self.path_audio}". Add audio files to theme folders.')
            self.themes = IndexList(ThemeDefinition(sonorium=self, name=f'Theme {ab}') for ab in 'AB')

        if self.themes:
            self.themes.current = self.themes[0]
            logger.info(f'Set default theme to: "{self.themes.current.name}"')
            # Enable ALL recordings in ALL themes by default for seamless mixing
            for theme in self.themes:
                if theme.instances:
                    for inst in theme.instances:
                        inst.is_enabled = True
                    logger.info(f'Auto-enabled all {len(theme.instances)} recordings in theme "{theme.name}"')

        try:
            media_players_data = [state for state in self.client_ha.get_states() if state.entity_id.startswith("media_player.")]
            self.media_player_states = IndexList()
            for data in media_players_data:
                try:
                    media_state = MediaState.from_state(data)
                    self.media_player_states.append(media_state)
                except Exception as e:
                    logger.warning(f'Could not parse media player {data.entity_id}: {e}')
            logger.info(f'Found {len(self.media_player_states)} media players')
            if self.media_player_states:
                self.media_player_states.current = self.media_player_states[0]
        except Exception as e:
            logger.error(f'Error fetching media players: {e}')
            self.media_player_states = IndexList()

    @cached_property
    def path_audio(self) -> Path:
        return Path(self.path_audio_str)
