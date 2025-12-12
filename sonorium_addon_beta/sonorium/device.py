from dataclasses import dataclass
from dataclasses import field, fields
from functools import cached_property
from typing import Self

import homeassistant_api

from sonorium.controls import SelectTheme, NumberMasterVolume, SelectMediaPlayer, StreamURL, PlayPauseSwitch
from sonorium.obs import logger
from sonorium.recording import RecordingMetadata
from sonorium.theme import ThemeDefinition
from fmtr.tools import Path
from fmtr.tools.iterator_tools import IndexList
from haco.device import Device


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
        self = cls(**filtered)
        return self


@dataclass(kw_only=True)
class Sonorium(Device):
    themes: IndexList[ThemeDefinition] = field(default_factory=IndexList, metadata=dict(exclude=True))
    metas: IndexList[RecordingMetadata] = field(default_factory=IndexList, metadata=dict(exclude=True))
    theme_metas: dict = field(default_factory=dict, metadata=dict(exclude=True))

    client_ha: homeassistant_api.Client | None = field(default=None, metadata=dict(exclude=True))

    path_audio_str: str = field(metadata=dict(exclude=True))
    
    # Master volume as gain multiplier (default 6.0 = 60%)
    master_volume: float = field(default=6.0, metadata=dict(exclude=True))
    
    # Playback state: 'stopped', 'playing', 'paused'
    playback_state: str = field(default='stopped', metadata=dict(exclude=True))

    def __post_init__(self):
        if not self.path_audio.exists():
            logger.warning(f'Audio path "{self.path_audio}" does not exist. Will be created.')
            self.path_audio.mkdir(parents=True, exist_ok=True)
        
        theme_folders = [folder for folder in self.path_audio.iterdir() if folder.is_dir()]
        
        logger.info(f'Scanning for themes in "{self.path_audio}"...')
        logger.info(f'Found {len(theme_folders)} theme folder(s): {[f.name for f in theme_folders]}')
        
        if not theme_folders:
            logger.warning(f'No theme folders found in "{self.path_audio}". Creating example structure...')
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
                theme_def = ThemeDefinition(sonorium=self, name=theme_name)
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
            logger.info(f'All {len(self.themes)} themes loaded with all tracks active')

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

        # Simplified controls
        self.controls = [
            self.select_theme,
            self.nbr_master_volume,
            self.select_media_player,
            self.swt_play_pause,
            self.sns_url
        ]

    @cached_property
    def path_audio(self):
        return Path(self.path_audio_str)

    @cached_property
    def select_theme(self):
        return SelectTheme(options=[str(defin.name) for defin in self.themes])

    @cached_property
    def select_media_player(self):
        return SelectMediaPlayer(options=list(self.media_player_states.friendly_name.keys()))

    @cached_property
    def swt_play_pause(self):
        return PlayPauseSwitch()

    @cached_property
    def sns_url(self):
        return StreamURL()

    @cached_property
    def nbr_master_volume(self):
        return NumberMasterVolume()
