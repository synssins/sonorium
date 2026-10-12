"""
The theme library: what Sonorium found in its audio folder at start-up.

It owns the loaded themes (theme.LoadedTheme) and the audio files behind them,
the master output level every mix reads, and the media players Home
Assistant reported at start-up. Rescans (api.refresh_themes) replace its
themes and file lists in place. MQTT entities live in ha/mqtt_entities.py.
"""
import json
import os
import shutil
from pathlib import Path
from typing import NamedTuple

from sonorium import runtime
from sonorium.obs import logger
from sonorium.recording import AudioFile
from sonorium.utils import HomeAssistantClient, IndexList

# Themes shipped in the image, copied into an empty audio folder on first start
# (SONORIUM_BUNDLED_THEMES: where a host keeps them instead)
BUNDLED_THEMES = Path(os.environ.get('SONORIUM_BUNDLED_THEMES') or '/app/themes')
# The output gain every mix starts at: 6.0 is 60 % (Settings' Master volume)
DEFAULT_MASTER_VOLUME = 6.0
# Placeholder themes when the audio folder has none, so the UI has something to show
PLACEHOLDER_THEMES = ('Theme A', 'Theme B')


class HaMediaPlayer(NamedTuple):
    """A media_player entity as Home Assistant's states API reported it."""
    entity_id: str  # media_player.<name>
    state: str  # playing, idle, off, unavailable...
    friendly_name: str
    supported_features: int = 0  # Home Assistant's feature bits

    @classmethod
    def from_ha(cls, entity: dict) -> "HaMediaPlayer":
        """From one item of GET /api/states; the entity id stands in for a missing friendly name."""
        attributes = entity.get('attributes') or {}
        name = attributes.get('friendly_name', entity.get('friendly_name'))
        features = attributes.get('supported_features', entity.get('supported_features', 0))
        return cls(entity_id=entity['entity_id'], state=entity['state'],
                   friendly_name=entity.get('entity_id', 'Unknown') if name is None else name,
                   supported_features=features)


def _visible_folders(path: Path) -> list[Path]:
    return [entry for entry in path.iterdir() if entry.is_dir() and not entry.name.startswith(".")]


def _saved_theme_id(folder: Path) -> str | None:
    """The id in a theme's metadata.json; None when there is none (the folder name is used)."""
    metadata = folder / "metadata.json"
    if not metadata.exists():
        return None
    try:
        return json.loads(metadata.read_text(encoding='utf-8')).get("id")
    except Exception:
        return None


class Library:
    """Themes, their files and playback-wide state for one Sonorium (see the module docstring)."""

    def __init__(self, *, audio_dir, client_ha: HomeAssistantClient | None = None,
                 master_volume: float = DEFAULT_MASTER_VOLUME):
        self.audio_dir = Path(audio_dir)
        self.client_ha = client_ha
        self.master_volume = master_volume  # the output gain every mix reads (0-10)
        self.playback_state = 'stopped'
        self.themes: IndexList = IndexList()  # LoadedThemes, in folder order
        self.all_files = IndexList()  # every theme's files, in theme order
        self.files_by_theme: dict = {}  # theme name -> its AudioFiles
        self.media_players = IndexList()

        self._prepare_audio_dir()
        self._load_themes(self._theme_folders())
        self._load_media_players()

    # --- the audio folder ---

    def _prepare_audio_dir(self) -> None:
        if not self.audio_dir.exists():
            logger.warning(f'No audio folder at "{self.audio_dir}" yet: creating it')
            self.audio_dir.mkdir(parents=True, exist_ok=True)
        # A plugin's theme write interrupted by a crash leaves hidden leftovers
        try:
            from sonorium.plugins.theme_api import remove_stale_staging
            remove_stale_staging(self.audio_dir)
        except Exception as e:
            logger.warning(f'Could not check for interrupted theme writes: {e}')

    def _theme_folders(self) -> list[Path]:
        """The theme folders, after a first start has filled an empty audio folder."""
        folders = _visible_folders(self.audio_dir)
        logger.debug(f'Scanning for themes in "{self.audio_dir}"...')
        logger.debug(f'Found {len(folders)} theme folder(s): {[f.name for f in folders]}')
        if folders:
            return folders
        bundled = [f for f in BUNDLED_THEMES.iterdir() if f.is_dir()] if BUNDLED_THEMES.exists() else []
        if bundled:
            logger.info(f'Installing {len(bundled)} bundled theme(s) on first run...')
            for source in bundled:
                shutil.copytree(str(source), str(self.audio_dir / source.name))
                logger.info(f'Installed bundled theme: {source.name}')
            folders = _visible_folders(self.audio_dir)
        if not folders:
            logger.warning('No bundled themes found. Creating example structure...')
            example = self.audio_dir / 'example_theme'
            example.mkdir(exist_ok=True)
            logger.info(f'Created example theme folder: {example}')
            folders = [example]
        return folders

    # --- themes ---

    def _load_themes(self, folders: list[Path]) -> None:
        from sonorium.theme import LoadedTheme
        from sonorium.theme_files import theme_audio_files

        for folder in folders:
            files = theme_audio_files(folder)  # top-level files and group folders
            # A theme made in Sonorium has its metadata.json before it has any audio
            if not files and not (folder / "metadata.json").is_file():
                logger.warning(f'Theme folder "{folder.name}" contains no audio files')
                continue
            self.files_by_theme[folder.name] = IndexList(AudioFile(path, folder) for path in files)
            self.themes.append(LoadedTheme(library=self, name=folder.name, theme_id=_saved_theme_id(folder)))
            logger.debug(f'Loaded theme "{folder.name}" with {len(files)} audio files')

        for files in self.files_by_theme.values():
            self.all_files.extend(files)

        if not self.themes:
            logger.warning(f'No themes with audio files found in "{self.audio_dir}". Add audio files to theme folders.')
            self.themes = IndexList(LoadedTheme(library=self, name=name) for name in PLACEHOLDER_THEMES)

        self.themes.current = self.themes[0]
        logger.debug(f'Set default theme to: "{self.themes.current.name}"')
        # Every track starts switched on, so a theme plays its whole mix
        for theme in self.themes:
            for track in theme.tracks:
                track.is_enabled = True

    # --- Home Assistant ---

    def _load_media_players(self) -> None:
        if not runtime.ha_configured():
            logger.info("Home Assistant not connected (optional: Settings -> Connection)")
            return
        try:
            states = self.client_ha.get_states()
        except Exception as e:
            logger.error(f'Error fetching media players: {e}')
            return
        for entity in states:
            if not entity.get("entity_id", "").startswith("media_player."):
                continue
            try:
                self.media_players.append(HaMediaPlayer.from_ha(entity))
            except Exception as e:
                logger.warning(f'Could not parse media player {entity.get("entity_id")}: {e}')
        logger.debug(f'Found {len(self.media_players)} media players')
        if self.media_players:
            self.media_players.current = self.media_players[0]
