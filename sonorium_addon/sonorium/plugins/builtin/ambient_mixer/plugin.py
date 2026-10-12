"""
Ambient Mixer Importer Plugin for Sonorium

Imports soundscapes from ambient-mixer.com by:
1. Extracting the template ID from the page
2. Fetching the XML configuration from the API
3. Parsing audio channel information
4. Downloading audio files with proper attribution

Supports:
- Creating new themes with default preset
- Importing into existing themes as new presets
- Multi-layer duplicate file detection
- Automatic preset name generation

The theme is written through plugins/theme_api.py (Themes 2.0). Each
channel's Ambient Mixer settings become the track's settings
(channel_track_settings): a random channel plays Intermittent ("sparse")
about as often as it did there, any other one Background ("continuous");
"crossfade" off means a gapless loop (seamless_loop).

Uses the same XML API that ambient-mixer.com's player uses for reliable extraction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sonorium.plugins.base import BasePlugin
from sonorium.obs import logger


# Constants
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
XML_API_BASE = "http://xml.ambient-mixer.com/audio-template?player=html5&id_template="
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.flac'}  # what Sonorium plays

# Intermittent gaps out of the box (Settings > Advanced's Wait between plays,
# sonorium/playback_values.py): presence 1 waits about 3 minutes between plays,
# presence 0 about 30. Used when this Sonorium has no such setting.
SPARSE_MIN_GAP = 180.0
SPARSE_MAX_GAP = 1800.0
RANDOM_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}


@dataclass
class AudioChannel:
    """Represents a single audio channel from an ambient-mixer template."""
    channel_num: int
    name: str
    audio_id: str
    url: str
    volume: int = 100
    balance: int = 0
    is_random: bool = False
    random_counter: int = 1
    random_unit: str = "1h"
    crossfade: bool = False
    mute: bool = False

    # Post-download info
    local_filename: Optional[str] = None
    file_hash: Optional[str] = None


@dataclass
class AmbientMix:
    """Represents a complete ambient-mixer template/mix."""
    template_id: str
    source_url: str
    name: str = ""
    channels: list = field(default_factory=list)

    # Metadata
    creator: str = ""
    category: str = ""
    harvested_at: str = ""

    def to_manifest(self) -> dict:
        """Convert to manifest dict for JSON export."""
        return {
            "source": {
                "site": "ambient-mixer.com",
                "url": self.source_url,
                "template_id": self.template_id,
                "creator": self.creator,
                "harvested_at": self.harvested_at,
            },
            "license": {
                "name": "Creative Commons Sampling Plus 1.0",
                "url": "https://creativecommons.org/licenses/sampling+/1.0/",
                "requires_attribution": True,
            },
            "mix_name": self.name,
            "category": self.category,
            "channels": [asdict(ch) for ch in self.channels if ch.url],
        }


def name_from_slug(slug: str) -> str:
    """A mix's name from the last part of its address: "spring-rain---sanctuary" is "Spring Rain - Sanctuary"."""
    parts = [" ".join(w for w in part.split("-") if w) for part in slug.split("---")]
    return " - ".join(p.title() for p in parts if p) or "Imported mix"


def random_unit_seconds(unit: str) -> float:
    """Ambient Mixer's random unit ("1m", "10m", "1h") in seconds (an hour if unreadable)."""
    match = re.match(r'^\s*(\d+(?:\.\d+)?)\s*([smh])', str(unit or "").lower())
    if not match:
        return 3600.0
    return max(1.0, float(match.group(1)) * RANDOM_UNIT_SECONDS[match.group(2)])


def wait_range() -> tuple[float, float]:
    """Intermittent Wait between plays now (shortest, longest), seconds: Settings > Advanced, else 180-1800."""
    try:
        from sonorium import playback_values
        return playback_values.wait_range()
    except (ImportError, AttributeError):
        return SPARSE_MIN_GAP, SPARSE_MAX_GAP


def random_presence(random_counter: int, random_unit: str) -> float:
    """
    The Intermittent presence that plays a random channel about as often as
    Ambient Mixer does: random_counter plays per random_unit is a gap of
    unit / counter, and presence p gives a gap of min + (max - min) * (1 - p)
    seconds in the current Wait between plays range (by default 180-1800,
    docs/THEME_FORMAT.md 3.4). Kept within 0-1.
    """
    gap = random_unit_seconds(random_unit) / max(1, int(random_counter or 1))
    low, high = wait_range()
    if high <= low:
        return 1.0
    presence = 1.0 - (gap - low) / (high - low)
    return round(min(1.0, max(0.0, presence)), 3)


def channel_track_settings(channel: AudioChannel) -> dict:
    """
    A channel's settings as Sonorium track settings:
    - random channels (short sounds played now and then) are Intermittent
      ("sparse"), with the presence that plays them about as often
    - every other channel is Background ("continuous")
    - Ambient Mixer's crossfade off is a gapless loop (seamless_loop True);
      on, the loop point is crossfaded (seamless_loop False)
    - volume 0-100 becomes 0.0-1.0; mute is kept
    """
    if channel.is_random:
        mode, presence = "sparse", random_presence(channel.random_counter, channel.random_unit)
    else:
        mode, presence = "continuous", 1.0
    return {
        "playback_mode": mode,
        "presence": presence,
        "volume": round(min(1.0, max(0.0, (channel.volume or 0) / 100.0)), 3),
        "seamless_loop": not channel.crossfade,
        "exclusive": False,
        "muted": bool(channel.mute),
    }


class AmbientMixerPlugin(BasePlugin):
    """
    Import soundscapes from Ambient-Mixer.com.

    This plugin allows users to paste an Ambient-Mixer URL and import
    all audio tracks as a new Sonorium theme or as a preset in an
    existing theme with proper attribution.

    Uses the XML API for reliable audio extraction.
    """

    id = "ambient_mixer"
    name = "Ambient Mixer Importer"
    version = "3.1.2"
    description = "Import soundscapes from Ambient-Mixer.com with preset support"
    author = "Sonorium"
    builtin = True  # Shipped with Sonorium

    def get_ui_schema(self) -> dict:
        """Return the UI schema for the import form."""
        # Existing themes for the dropdown, by theme id
        theme_options = [{"value": "", "label": "-- Create New Theme --"}]
        theme_options.extend([
            {"value": t["id"], "label": t["name"]}
            for t in self.list_themes()
        ])

        return {
            "type": "form",
            "fields": [
                {
                    "name": "url",
                    "type": "url",
                    "label": "Ambient Mixer URL",
                    "placeholder": "https://ambient-mixer.com/m/example",
                    "required": True,
                },
                {
                    "name": "existing_theme",
                    "type": "select",
                    "label": "Import To",
                    "options": theme_options,
                    "required": False,
                    "default": "",
                    "help": "Select existing theme to add as preset, or create new theme",
                },
                {
                    "name": "theme_name",
                    "type": "string",
                    "label": "Theme Name",
                    "placeholder": "Leave empty to use page title (new themes only)",
                    "required": False,
                    "condition": {"field": "existing_theme", "value": ""},
                },
                {
                    "name": "preset_name",
                    "type": "string",
                    "label": "Preset Name",
                    "placeholder": "Leave empty to auto-generate from mix name",
                    "required": False,
                    "help": "Name for this soundscape preset",
                },
            ],
            "actions": [
                {
                    "id": "import",
                    "label": "Import Soundscape",
                    "primary": True,
                },
                {
                    "id": "refresh_themes",
                    "label": "Refresh Theme List",
                    "primary": False,
                }
            ],
        }

    def get_settings_schema(self) -> dict:
        """Return the settings schema for persistent configuration."""
        return {
            "hash_check_duplicates": {
                "type": "boolean",
                "default": True,
                "label": "Use hash to detect duplicate files",
                "help": "More thorough but slower duplicate detection",
            },
        }

    async def handle_action(self, action: str, data: dict) -> dict:
        """Handle the import action."""
        if action == "import":
            return await self._import_soundscape(data)
        elif action == "refresh_themes":
            # Return updated UI schema with fresh theme list
            return {
                "success": True,
                "message": "Theme list refreshed",
                "refresh_ui": True,
            }
        return {"success": False, "message": f"Unknown action: {action}"}

    # =========================================================================
    # Duplicate Detection
    # =========================================================================

    @staticmethod
    def _expected_name(channel: AudioChannel, safe_name: str) -> str:
        ext = Path(channel.url).suffix.lower()
        return f"{safe_name}_{channel.audio_id}{ext if ext in AUDIO_EXTENSIONS else '.mp3'}"

    def _find_duplicate(self, channel: AudioChannel, theme_path: Optional[Path]) -> Optional[str]:
        """
        Check if audio file already exists in the theme folder (its top level).
        Returns existing filename if duplicate found, None otherwise.

        Uses multi-layer detection:
        1. Exact filename match
        2. Audio ID match (same ID = same file on Ambient Mixer)
        """
        if theme_path is None or not theme_path.is_dir():
            return None

        # Layer 1: Exact filename match
        expected_name = self._expected_name(channel, self._sanitize_filename(channel.name))
        if (theme_path / expected_name).is_file():
            return expected_name

        # Layer 2: Audio ID in any filename
        # Ambient Mixer audio IDs are unique - same ID = same audio
        if channel.audio_id:
            for existing in sorted(theme_path.iterdir()):
                if existing.is_file() and existing.suffix.lower() in AUDIO_EXTENSIONS and not existing.name.startswith('.'):
                    if f"_{channel.audio_id}." in existing.name or f"_{channel.audio_id}_" in existing.name:
                        return existing.name

        return None

    def _find_by_hash(self, content: bytes, theme_path: Optional[Path]) -> Optional[str]:
        """Layer 3: an audio file at the top of the theme folder with the same content."""
        if theme_path is None or not theme_path.is_dir():
            return None
        file_hash = hashlib.md5(content).hexdigest()
        for existing in sorted(theme_path.iterdir()):
            if not existing.is_file() or existing.suffix.lower() not in AUDIO_EXTENSIONS or existing.name.startswith('.'):
                continue
            # Quick size check first
            if existing.stat().st_size != len(content):
                continue
            if hashlib.md5(existing.read_bytes()).hexdigest() == file_hash:
                return existing.name
        return None

    # =========================================================================
    # Main Import Logic
    # =========================================================================

    async def _import_soundscape(self, data: dict) -> dict:
        """
        Import a soundscape from Ambient-Mixer using the XML API.
        Supports both new themes and adding to existing themes as presets.
        """
        from sonorium.plugins.theme_api import ThemeEdit, ThemeEditError, find_theme

        url = str(data.get("url") or "").strip()
        existing_theme_id = str(data.get("existing_theme") or "").strip()
        custom_theme_name = str(data.get("theme_name") or "").strip()
        custom_preset_name = str(data.get("preset_name") or "").strip()

        if not url:
            return {"success": False, "message": "URL is required"}

        # Validate URL
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if "ambient-mixer" not in parsed.netloc.lower():
            return {
                "success": False,
                "message": "URL must be from ambient-mixer.com",
            }

        # An existing theme is picked by id from the list; checked before downloading.
        # It is only read now (to skip files it has); it is opened and written
        # after the downloads, with no await in between, so nothing saved to it
        # meanwhile is lost.
        theme_path = None
        if existing_theme_id:
            theme_path = find_theme(self.audio_path, existing_theme_id)
            if theme_path is None:
                return {"success": False, "message": f"Theme not found: {existing_theme_id}"}

        # Track warnings for partial failures
        warnings = []
        download_dir = Path(tempfile.mkdtemp(prefix="sonorium-ambient-mixer-"))

        try:
            # Import httpx
            try:
                import httpx
            except ImportError:
                return {
                    "success": False,
                    "message": "httpx library not available. Please install it.",
                }

            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=30.0,
                headers={"User-Agent": USER_AGENT}
            ) as client:
                # Step 1: Fetch the page to get template ID
                logger.info(f"Fetching Ambient Mixer page: {url}")
                response = await client.get(url)
                response.raise_for_status()
                html = response.text

                template_id = self._extract_template_id(html)
                if not template_id:
                    return {
                        "success": False,
                        "message": "Could not find template ID in page. The URL may be invalid.",
                    }

                logger.info(f"Found template ID: {template_id}")

                # Step 2: Fetch the XML configuration
                xml_url = f"{XML_API_BASE}{template_id}"
                logger.info(f"Fetching XML config: {xml_url}")
                xml_response = await client.get(xml_url)
                xml_response.raise_for_status()

                # Step 3: Parse the XML
                mix = self._parse_template_xml(xml_response.text, url, template_id)
                if not mix or not mix.channels:
                    return {
                        "success": False,
                        "message": "No audio channels found in the template.",
                    }

                logger.info(f"Parsed mix: {mix.name} with {len(mix.channels)} channels")

                # Step 4: Download audio files (to a temporary folder outside the themes)
                downloads, failed = await self._download_channels(client, mix, theme_path, download_dir, warnings)

            # From here on no await: the theme is read, changed and written in one go
            # Step 5: New theme, or the existing one
            is_new_theme = theme_path is None
            if is_new_theme:
                theme_name = custom_theme_name or mix.name or f"ambient_mix_{template_id}"
                edit = ThemeEdit.new(
                    self.audio_path, theme_name,
                    description=f"Imported from {url}",
                    icon="mdi:music",
                    attribution=self._attribution(url, template_id),
                )
                logger.info(f"Creating new theme: {theme_name}")
            else:
                edit = ThemeEdit.open(self.audio_path, existing_theme_id)
                theme_name = edit.name
                logger.info(f"Adding to existing theme: {theme_name}")

            # Step 6: The downloaded files join the theme (or reuse its own)
            downloaded_new, downloaded_existing = self._add_downloads(edit, downloads, warnings)
            total_tracks = downloaded_new + downloaded_existing
            if total_tracks == 0:
                return {
                    "success": False,
                    "message": "Failed to download any audio files.",
                    "warnings": warnings,
                }

            # Step 7: Preset with each channel's settings; the theme's other tracks muted in it
            preset_name = edit.free_preset_name(
                custom_preset_name or mix.name or f"Preset {len(edit.metadata.presets) + 1}")
            preset_tracks = {}
            for channel in mix.channels:
                if channel.local_filename:
                    preset_tracks.setdefault(Path(channel.local_filename).stem, channel_track_settings(channel))
            preset_id = edit.add_preset(preset_name, preset_tracks)

            # Step 8: MANIFEST.json and ATTRIBUTION.md (never replacing the theme's own)
            edit.add_extra_file("MANIFEST.json", json.dumps(mix.to_manifest(), indent=2))
            if is_new_theme:
                edit.add_extra_file("ATTRIBUTION.md", self._attribution_text(mix))

            # Step 9: Write the theme
            result = edit.commit()
            logger.info(f"Saved theme '{result['name']}' with preset '{preset_name}'")

            # Build result message
            if is_new_theme:
                message = f"Created theme '{theme_name}' with preset '{preset_name}' ({total_tracks} tracks)"
            else:
                message = f"Added preset '{preset_name}' to '{theme_name}' ({total_tracks} tracks)"

            if downloaded_existing > 0:
                message += f" ({downloaded_existing} existing files reused)"

            return {
                "success": True,
                "message": message,
                "refresh_themes": True,  # Signal API to auto-refresh themes
                "warnings": warnings if warnings else None,
                "data": {
                    "theme_id": result["theme_id"],
                    "theme_name": theme_name,
                    "theme_path": str(result["folder"]),
                    "preset_name": preset_name,
                    "preset_id": preset_id,
                    "tracks_new": downloaded_new,
                    "tracks_existing": downloaded_existing,
                    "tracks_failed": failed,
                    "template_id": template_id,
                    "is_new_theme": is_new_theme,
                },
            }

        except ThemeEditError as e:
            return {
                "success": False,
                "message": f"Import failed, nothing was saved: {e}",
                "warnings": warnings if warnings else None,
            }
        except Exception as e:
            logger.error(f"Error importing soundscape: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Import failed: {e}",
                "warnings": warnings if warnings else None,
            }
        finally:
            shutil.rmtree(download_dir, ignore_errors=True)

    async def _download_channels(self, client, mix: AmbientMix, theme_path: Optional[Path],
                                 download_dir: Path, warnings: list) -> tuple[list, int]:
        """
        Download each channel into download_dir, skipping a sound the existing
        theme (theme_path, None for a new theme) already has or one already
        downloaded for another channel of this mix. Nothing in the themes
        folder is written. Returns ([(channel, kind, value)], failed) where
        kind is "existing" (value: the theme's file name), "same" (value: the
        channel with the same sound) or "new" (value: the downloaded file).
        """
        downloads = []
        by_audio_id: dict[str, AudioChannel] = {}
        failed = 0

        for channel in mix.channels:
            if not channel.url:
                continue

            # Same sound twice in this mix
            if channel.audio_id and channel.audio_id in by_audio_id:
                downloads.append((channel, "same", by_audio_id[channel.audio_id]))
                continue

            existing = self._find_duplicate(channel, theme_path)
            if existing:
                logger.info(f"  Using existing: {existing} (audio_id={channel.audio_id})")
                downloads.append((channel, "existing", existing))
                by_audio_id[channel.audio_id] = channel
                continue

            final_name = self._expected_name(channel, self._sanitize_filename(channel.name))
            try:
                logger.info(f"  Downloading: {channel.name} -> {final_name}")
                response = await client.get(channel.url, timeout=60.0)
                response.raise_for_status()
                target = download_dir / f"{channel.channel_num}" / final_name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(response.content)
            except Exception as e:
                error_msg = f"Failed to download {channel.name}: {e}"
                logger.warning(f"  {error_msg}")
                warnings.append(error_msg)
                failed += 1
                continue

            downloads.append((channel, "new", target))
            by_audio_id[channel.audio_id] = channel

            # Small delay between downloads
            await asyncio.sleep(0.3)

        return downloads, failed

    def _add_downloads(self, edit, downloads: list, warnings: list) -> tuple[int, int]:
        """
        Add the downloaded files to the theme edit (no await: the theme was
        just opened), reusing a file the theme has with the same content.
        Sets each channel's local_filename. Returns (new, existing).
        """
        use_hash_check = self.get_setting("hash_check_duplicates", True)
        by_hash: dict[str, str] = {}
        downloaded_new = downloaded_existing = 0

        for channel, kind, value in downloads:
            if kind == "same":
                channel.local_filename = value.local_filename
                if channel.local_filename:
                    downloaded_existing += 1
                continue
            if kind == "existing":
                warnings.append(f"Used existing file: {value}")
                channel.local_filename = value
                downloaded_existing += 1
                continue

            content = value.read_bytes()
            file_hash = hashlib.md5(content).hexdigest()
            same = by_hash.get(file_hash) or (self._find_by_hash(content, edit.folder) if use_hash_check else None)
            if same:
                logger.info(f"  Hash match: using {same}")
                warnings.append(f"Used existing file: {same}")
                channel.local_filename = same
                downloaded_existing += 1
            else:
                key = edit.add_file(value, value.name, settings=channel_track_settings(channel))
                channel.local_filename = key + value.suffix
                channel.file_hash = file_hash
                by_hash[file_hash] = channel.local_filename
                downloaded_new += 1

        return downloaded_new, downloaded_existing

    def _attribution(self, url: str, template_id: str) -> dict:
        """metadata.json "attribution" for an imported theme."""
        return {
            "source": "Ambient-Mixer.com",
            "source_url": url,
            "template_id": template_id,
            "license": "Creative Commons Sampling Plus 1.0",
            "license_url": "https://creativecommons.org/licenses/sampling+/1.0/",
            "imported_date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "imported_by": self.id,
        }

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _extract_template_id(self, html: str) -> Optional[str]:
        """Extract template ID from ambient-mixer page HTML."""
        # Try AmbientMixer.setup() pattern first
        match = re.search(r'AmbientMixer\.setup\((\d+)\)', html)
        if match:
            return match.group(1)

        # Try vote link pattern
        match = re.search(r'/vote/(\d+)', html)
        if match:
            return match.group(1)

        # Try id_template parameter
        match = re.search(r'id_template[=:][\s"\']*(\d+)', html)
        if match:
            return match.group(1)

        return None

    def _parse_template_xml(self, xml_content: str, source_url: str, template_id: str) -> Optional[AmbientMix]:
        """Parse XML content into an AmbientMix object."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            logger.error(f"Failed to parse XML: {e}")
            return None

        mix = AmbientMix(
            template_id=template_id,
            source_url=source_url,
            harvested_at=datetime.now().isoformat(),
        )

        # Extract mix name from title element or URL
        title_elem = root.find('title')
        if title_elem is not None and title_elem.text:
            mix.name = title_elem.text.strip()
        else:
            # Fall back to URL parsing
            from urllib.parse import urlparse
            parsed = urlparse(source_url)
            mix.name = name_from_slug(parsed.path.strip('/').split('/')[-1])

        mix.category = root.findtext('category', '')

        # Parse channels (ambient-mixer has up to 8 channels)
        for i in range(1, 9):
            channel_elem = root.find(f'channel{i}')
            if channel_elem is None:
                continue

            url_elem = channel_elem.find('url_audio')
            if url_elem is None or not url_elem.text or not url_elem.text.strip():
                continue

            def get_text(elem_name: str, default: str = "") -> str:
                elem = channel_elem.find(elem_name)
                return elem.text.strip() if elem is not None and elem.text else default

            def get_int(elem_name: str, default: int = 0) -> int:
                try:
                    return int(get_text(elem_name, str(default)))
                except ValueError:
                    return default

            def get_bool(elem_name: str, default: bool = False) -> bool:
                val = get_text(elem_name, "").lower()
                return val in ("true", "1", "yes")

            channel = AudioChannel(
                channel_num=i,
                name=get_text('name_audio', f'channel_{i}'),
                audio_id=get_text('id_audio'),
                url=get_text('url_audio'),
                volume=get_int('volume', 100),
                balance=get_int('balance', 0),
                is_random=get_bool('random'),
                random_counter=get_int('random_counter', 1),
                random_unit=get_text('random_unit', '1h'),
                crossfade=get_bool('crossfade'),
                mute=get_bool('mute'),
            )

            mix.channels.append(channel)
            logger.debug(f"  Channel {i}: {channel.name} ({channel.audio_id})")

        return mix

    def _sanitize_filename(self, name: str) -> str:
        """Create a safe filename component from a string."""
        safe = re.sub(r'[^\w\s-]', '', name).strip()
        safe = safe.replace(' ', '_')
        return safe[:50] or "audio"

    def _attribution_text(self, mix: AmbientMix) -> str:
        """Human-readable attribution file (ATTRIBUTION.md)."""
        lines = [
            f"# Attribution for {mix.name}",
            "",
            f"**Source:** [{mix.source_url}]({mix.source_url})",
            f"**Harvested:** {mix.harvested_at}",
            "",
            "## License",
            "",
            "All audio files in this folder are licensed under:",
            "[Creative Commons Sampling Plus 1.0](https://creativecommons.org/licenses/sampling+/1.0/)",
            "",
            "This license permits:",
            "- Sampling and remixing (including commercial use)",
            "- Distribution of derivative works",
            "",
            "**Attribution is required.** Credit ambient-mixer.com when using these sounds.",
            "",
            "## Audio Files",
            "",
        ]

        for ch in mix.channels:
            if ch.local_filename:
                lines.append(f"- **{ch.local_filename}**")
                lines.append(f"  - Original name: {ch.name}")
                lines.append(f"  - Source ID: {ch.audio_id}")
                lines.append(f"  - Default volume: {ch.volume}%")
                if ch.balance != 0:
                    lines.append(f"  - Balance: {ch.balance}")
                lines.append("")

        lines.extend([
            "---",
            f"*Generated by Sonorium Ambient Mixer Plugin v{self.version}*",
        ])

        return '\n'.join(lines)
