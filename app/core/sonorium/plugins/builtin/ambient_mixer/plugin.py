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

Uses the same XML API that ambient-mixer.com's player uses for reliable extraction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from sonorium.plugins.base import BasePlugin
from sonorium.obs import logger


# Constants
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
XML_API_BASE = "http://xml.ambient-mixer.com/audio-template?player=html5&id_template="
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.flac', '.m4a'}


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
    version = "3.0.0"
    description = "Import soundscapes from Ambient-Mixer.com with preset support"
    author = "Sonorium"

    def get_ui_schema(self) -> dict:
        """Return the UI schema for the import form."""
        # Get list of existing themes for dropdown
        existing_themes = self._list_existing_themes()
        theme_options = [{"value": "", "label": "-- Create New Theme --"}]
        theme_options.extend([
            {"value": t["path"], "label": t["name"]}
            for t in existing_themes
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
            "auto_create_metadata": {
                "type": "boolean",
                "default": True,
                "label": "Auto-create metadata.json",
            },
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
    # Theme Discovery
    # =========================================================================

    def _list_existing_themes(self) -> list[dict]:
        """
        Scan audio_path for existing themes with metadata.json.
        Returns list of {id, name, path} dicts.
        """
        themes = []

        if not self.audio_path or not self.audio_path.exists():
            return themes

        for folder in self.audio_path.iterdir():
            if not folder.is_dir():
                continue

            metadata_path = folder / "metadata.json"
            if not metadata_path.exists():
                continue

            try:
                metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                themes.append({
                    "id": metadata.get("id", folder.name),
                    "name": metadata.get("name", folder.name),
                    "path": str(folder),
                })
            except (json.JSONDecodeError, OSError) as e:
                # Include theme but flag as potentially corrupted
                logger.warning(f"Could not read metadata for {folder.name}: {e}")
                themes.append({
                    "id": folder.name,
                    "name": f"{folder.name} (metadata error)",
                    "path": str(folder),
                })

        # Sort by name
        themes.sort(key=lambda t: t["name"].lower())
        return themes

    # =========================================================================
    # Metadata Handling (with self-repair)
    # =========================================================================

    def _load_theme_metadata(self, theme_path: Path) -> dict:
        """
        Load metadata.json with automatic repair on corruption.
        """
        metadata_path = theme_path / "metadata.json"

        try:
            return json.loads(metadata_path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as e:
            logger.warning(f"Corrupted metadata.json in {theme_path.name}: {e}")
            return self._repair_metadata(theme_path)
        except FileNotFoundError:
            logger.warning(f"Missing metadata.json in {theme_path.name}")
            return self._repair_metadata(theme_path)

    def _repair_metadata(self, theme_path: Path) -> dict:
        """
        Attempt to repair/regenerate metadata.json from available data.
        """
        logger.info(f"Attempting metadata repair for {theme_path.name}...")
        metadata_path = theme_path / "metadata.json"
        metadata = {}

        # Try to salvage from corrupted file
        if metadata_path.exists():
            try:
                content = metadata_path.read_text(encoding='utf-8', errors='replace')
                metadata = self._salvage_json(content)
                # Backup corrupted file
                backup_path = theme_path / f"metadata.json.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                metadata_path.rename(backup_path)
                logger.info(f"Backed up corrupted metadata to {backup_path.name}")
            except OSError:
                pass

        # Generate missing fields
        if 'id' not in metadata:
            metadata['id'] = str(uuid.uuid4())

        if 'name' not in metadata:
            metadata['name'] = theme_path.name.replace('_', ' ').title()

        if 'description' not in metadata:
            metadata['description'] = f"Theme recovered from {theme_path.name}"

        # Generate tracks from audio files
        if 'tracks' not in metadata:
            metadata['tracks'] = {}

        for file in theme_path.iterdir():
            if file.suffix.lower() in AUDIO_EXTENSIONS:
                track_key = file.stem
                if track_key not in metadata['tracks']:
                    metadata['tracks'][track_key] = {
                        "presence": 1.0,
                        "muted": False,
                        "volume": 1.0,
                        "playback_mode": "auto",
                        "seamless_loop": False,
                        "exclusive": False
                    }

        # Pull attribution from MANIFEST.json if available
        manifest_path = theme_path / "MANIFEST.json"
        if manifest_path.exists() and 'attribution' not in metadata:
            try:
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                source = manifest.get("source", {})
                license_info = manifest.get("license", {})
                metadata['attribution'] = {
                    "source": source.get("site", "Unknown"),
                    "source_url": source.get("url", ""),
                    "template_id": source.get("template_id", ""),
                    "license": license_info.get("name", ""),
                    "license_url": license_info.get("url", ""),
                }
            except (json.JSONDecodeError, OSError):
                pass

        # Add recovery marker
        metadata['_recovered'] = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'reason': 'metadata.json was corrupted or missing'
        }

        # Save repaired metadata
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        logger.info(f"Repaired metadata.json for {theme_path.name}")

        return metadata

    def _salvage_json(self, corrupted_content: str) -> dict:
        """Attempt to extract valid data from corrupted JSON."""
        salvaged = {}

        # Try to find known keys via regex
        patterns = {
            'id': r'"id"\s*:\s*"([^"]+)"',
            'name': r'"name"\s*:\s*"([^"]+)"',
            'description': r'"description"\s*:\s*"([^"]+)"',
            'icon': r'"icon"\s*:\s*"([^"]+)"',
        }

        for key, pattern in patterns.items():
            match = re.search(pattern, corrupted_content)
            if match:
                salvaged[key] = match.group(1)

        return salvaged

    def _save_theme_metadata(self, theme_path: Path, metadata: dict) -> None:
        """Save metadata.json to theme folder."""
        metadata_path = theme_path / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')

    # =========================================================================
    # Duplicate Detection
    # =========================================================================

    def _find_duplicate(self, channel: AudioChannel, theme_path: Path) -> Optional[str]:
        """
        Check if audio file already exists in theme folder.
        Returns existing filename if duplicate found, None otherwise.

        Uses multi-layer detection:
        1. Exact filename match
        2. Audio ID match (same ID = same file on Ambient Mixer)
        """
        audio_id = channel.audio_id
        ext = Path(channel.url).suffix or '.mp3'

        # Layer 1: Exact filename match
        safe_name = self._sanitize_filename(channel.name)
        expected_name = f"{safe_name}_{audio_id}{ext}"
        if (theme_path / expected_name).exists():
            return expected_name

        # Layer 2: Audio ID match in any filename
        # Ambient Mixer audio IDs are unique - same ID = same audio
        for existing in theme_path.glob(f"*_{audio_id}.*"):
            if existing.suffix.lower() in AUDIO_EXTENSIONS:
                return existing.name

        # Layer 3: Check for audio_id anywhere in filename (broader match)
        for existing in theme_path.iterdir():
            if existing.suffix.lower() in AUDIO_EXTENSIONS:
                if f"_{audio_id}." in existing.name or f"_{audio_id}_" in existing.name:
                    return existing.name

        return None

    async def _download_with_dedup(
        self,
        client,
        channel: AudioChannel,
        theme_path: Path,
        use_hash_check: bool = True
    ) -> tuple[bool, str, Optional[str]]:
        """
        Download audio file with duplicate detection.

        Returns:
            (is_new, filename, error_message)
            - is_new: True if newly downloaded, False if existing used
            - filename: The filename to use (existing or new)
            - error_message: None on success, error string on failure
        """
        # Check for existing duplicate first
        existing = self._find_duplicate(channel, theme_path)
        if existing:
            logger.info(f"  Using existing: {existing} (audio_id={channel.audio_id})")
            return (False, existing, None)

        # Prepare download
        ext = Path(channel.url).suffix or '.mp3'
        safe_name = self._sanitize_filename(channel.name)
        final_name = f"{safe_name}_{channel.audio_id}{ext}"
        temp_path = theme_path / f".downloading_{channel.audio_id}{ext}"
        final_path = theme_path / final_name

        try:
            logger.info(f"  Downloading: {channel.name} -> {final_name}")
            response = await client.get(channel.url, timeout=60.0)
            response.raise_for_status()

            temp_path.write_bytes(response.content)
            file_hash = hashlib.md5(response.content).hexdigest()

            # Layer 4: Hash-based duplicate check (if enabled)
            if use_hash_check:
                file_size = temp_path.stat().st_size
                for existing_file in theme_path.iterdir():
                    if existing_file.suffix.lower() not in AUDIO_EXTENSIONS:
                        continue
                    if existing_file.name.startswith('.'):
                        continue

                    # Quick size check first
                    if existing_file.stat().st_size != file_size:
                        continue

                    # Full hash comparison
                    existing_hash = hashlib.md5(existing_file.read_bytes()).hexdigest()
                    if existing_hash == file_hash:
                        temp_path.unlink()
                        logger.info(f"  Hash match: using {existing_file.name}")
                        return (False, existing_file.name, None)

            # No duplicate found - finalize
            temp_path.rename(final_path)
            channel.local_filename = final_name
            channel.file_hash = file_hash
            return (True, final_name, None)

        except Exception as e:
            # Cleanup temp file on error
            if temp_path.exists():
                temp_path.unlink()
            error_msg = f"Failed to download {channel.name}: {e}"
            logger.warning(f"  {error_msg}")
            return (False, "", error_msg)

    # =========================================================================
    # Preset Generation
    # =========================================================================

    def _generate_preset_id(self, name: str) -> str:
        """Generate a safe preset ID from name."""
        # Lowercase, replace spaces with underscores, remove special chars
        preset_id = re.sub(r'[^\w\s-]', '', name.lower())
        preset_id = re.sub(r'[\s-]+', '_', preset_id).strip('_')
        return preset_id or "preset"

    def _ensure_unique_preset_id(self, preset_id: str, existing_presets: dict) -> str:
        """Ensure preset ID is unique by appending number if needed."""
        if preset_id not in existing_presets:
            return preset_id

        counter = 2
        while f"{preset_id}_{counter}" in existing_presets:
            counter += 1
        return f"{preset_id}_{counter}"

    def _create_preset_from_channels(
        self,
        channels: list[AudioChannel],
        preset_name: str,
        is_default: bool = False
    ) -> dict:
        """
        Create a preset dict from AudioChannel list.
        Maps Ambient Mixer settings to Sonorium settings.
        """
        tracks = {}

        for channel in channels:
            if not channel.local_filename:
                continue

            # Map AM settings to Sonorium
            # AM volume is 0-100, Sonorium is 0.0-1.0
            volume = channel.volume / 100.0

            # AM random = sparse playback in Sonorium
            if channel.is_random:
                playback_mode = "sparse"
                # Use random_counter to influence presence
                # Higher counter = less frequent = lower presence
                presence = max(0.1, 1.0 - (channel.random_counter * 0.1))
            else:
                playback_mode = "auto"
                presence = 1.0

            # Track key is filename without extension
            track_key = Path(channel.local_filename).stem

            tracks[track_key] = {
                "volume": volume,
                "presence": presence,
                "playback_mode": playback_mode,
                "seamless_loop": channel.crossfade,
                "exclusive": False,
                "muted": channel.mute,
            }

        return {
            "name": preset_name,
            "is_default": is_default,
            "tracks": tracks,
        }

    # =========================================================================
    # Main Import Logic
    # =========================================================================

    async def _import_soundscape(self, data: dict) -> dict:
        """
        Import a soundscape from Ambient-Mixer using the XML API.
        Supports both new themes and adding to existing themes as presets.
        """
        url = data.get("url", "").strip()
        existing_theme_path = data.get("existing_theme", "").strip()
        custom_theme_name = data.get("theme_name", "").strip()
        custom_preset_name = data.get("preset_name", "").strip()

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

        # Track warnings for partial failures
        warnings = []

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

                # Step 4: Determine theme folder and mode
                if existing_theme_path:
                    # Import to existing theme
                    theme_path = Path(existing_theme_path)
                    if not theme_path.exists():
                        return {
                            "success": False,
                            "message": f"Theme folder not found: {existing_theme_path}",
                        }
                    is_new_theme = False
                    metadata = self._load_theme_metadata(theme_path)
                    theme_name = metadata.get("name", theme_path.name)
                    logger.info(f"Adding to existing theme: {theme_name}")
                else:
                    # Create new theme
                    theme_name = custom_theme_name or mix.name or f"ambient_mix_{template_id}"
                    safe_theme_name = self._sanitize_folder_name(theme_name)
                    theme_path = self.audio_path / safe_theme_name
                    theme_path.mkdir(parents=True, exist_ok=True)
                    is_new_theme = True
                    metadata = {
                        "id": str(uuid.uuid4()),
                        "name": theme_name,
                        "description": f"Imported from {url}",
                        "icon": "mdi:music",
                        "tracks": {},
                        "presets": {},
                    }
                    logger.info(f"Creating new theme: {theme_name}")

                # Ensure presets section exists
                if "presets" not in metadata:
                    metadata["presets"] = {}
                if "tracks" not in metadata:
                    metadata["tracks"] = {}

                # Step 5: Download audio files with duplicate detection
                use_hash_check = self.get_setting("hash_check_duplicates", True)
                downloaded_new = 0
                downloaded_existing = 0
                failed = 0

                for channel in mix.channels:
                    if not channel.url:
                        continue

                    is_new, filename, error = await self._download_with_dedup(
                        client, channel, theme_path, use_hash_check
                    )

                    if error:
                        warnings.append(error)
                        failed += 1
                        continue

                    channel.local_filename = filename

                    if is_new:
                        downloaded_new += 1
                    else:
                        downloaded_existing += 1
                        warnings.append(f"Used existing file: {filename}")

                    # Add track to metadata if not present
                    track_key = Path(filename).stem
                    if track_key not in metadata["tracks"]:
                        metadata["tracks"][track_key] = {
                            "presence": 1.0,
                            "muted": False,
                            "volume": channel.volume / 100.0,
                            "playback_mode": "auto",
                            "seamless_loop": False,
                            "exclusive": False,
                        }

                    # Small delay between downloads
                    if is_new:
                        await asyncio.sleep(0.3)

                total_tracks = downloaded_new + downloaded_existing
                if total_tracks == 0:
                    return {
                        "success": False,
                        "message": "Failed to download any audio files.",
                        "warnings": warnings,
                    }

                # Step 6: Create preset
                preset_name = custom_preset_name or mix.name or f"Preset {len(metadata['presets']) + 1}"
                preset_id = self._generate_preset_id(preset_name)
                preset_id = self._ensure_unique_preset_id(preset_id, metadata["presets"])

                # First preset in a new theme is default
                is_default = is_new_theme and len(metadata["presets"]) == 0

                preset = self._create_preset_from_channels(
                    [ch for ch in mix.channels if ch.local_filename],
                    preset_name,
                    is_default
                )
                metadata["presets"][preset_id] = preset

                # Step 7: Update attribution for new themes
                if is_new_theme:
                    metadata["attribution"] = {
                        "source": "Ambient-Mixer.com",
                        "source_url": url,
                        "template_id": template_id,
                        "license": "Creative Commons Sampling Plus 1.0",
                        "license_url": "https://creativecommons.org/licenses/sampling+/1.0/",
                        "imported_date": datetime.utcnow().isoformat() + "Z",
                        "imported_by": self.id,
                    }

                # Step 8: Save metadata
                self._save_theme_metadata(theme_path, metadata)
                logger.info(f"Saved metadata.json with preset '{preset_name}'")

                # Step 9: Write/update MANIFEST.json
                manifest_path = theme_path / "MANIFEST.json"
                if is_new_theme or not manifest_path.exists():
                    manifest_path.write_text(json.dumps(mix.to_manifest(), indent=2))

                # Step 10: Write/update ATTRIBUTION.md
                if is_new_theme:
                    self._write_attribution(mix, theme_path / "ATTRIBUTION.md")

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
                        "theme_name": theme_name,
                        "theme_path": str(theme_path),
                        "preset_name": preset_name,
                        "preset_id": preset_id,
                        "tracks_new": downloaded_new,
                        "tracks_existing": downloaded_existing,
                        "tracks_failed": failed,
                        "template_id": template_id,
                        "is_new_theme": is_new_theme,
                    },
                }

        except Exception as e:
            logger.error(f"Error importing soundscape: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Import failed: {e}",
                "warnings": warnings if warnings else None,
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
            mix.name = parsed.path.strip('/').split('/')[-1].replace('-', ' ').title()

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

    def _sanitize_folder_name(self, name: str) -> str:
        """Create a safe folder name from a string."""
        safe = re.sub(r'[<>:"/\\|?*]', '', name)
        safe = safe.strip('. ')
        safe = re.sub(r'\s+', ' ', safe)
        safe = safe.replace(' ', '_')
        return safe or "Imported_Theme"

    def _sanitize_filename(self, name: str) -> str:
        """Create a safe filename component from a string."""
        safe = re.sub(r'[^\w\s-]', '', name).strip()
        safe = safe.replace(' ', '_')
        return safe[:50] or "audio"

    def _write_attribution(self, mix: AmbientMix, filepath: Path):
        """Write human-readable attribution file."""
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
            "*Generated by Sonorium Ambient Mixer Plugin v3.0.0*",
        ])

        filepath.write_text('\n'.join(lines), encoding='utf-8')
