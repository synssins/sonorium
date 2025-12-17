"""
Ambient Mixer Importer Plugin for Sonorium

Imports soundscapes from ambient-mixer.com by:
1. Extracting the template ID from the page
2. Fetching the XML configuration from the API
3. Parsing audio channel information
4. Downloading audio files with proper attribution

Uses the same XML API that ambient-mixer.com's player uses for reliable extraction.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from sonorium.plugins.base import BasePlugin
from sonorium.obs import logger


# Constants
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
XML_API_BASE = "http://xml.ambient-mixer.com/audio-template?player=html5&id_template="


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
    all audio tracks as a new Sonorium theme with proper attribution.

    Uses the XML API for reliable audio extraction.
    """

    id = "ambient_mixer"
    name = "Ambient Mixer Importer"
    version = "2.0.0"
    description = "Import soundscapes from Ambient-Mixer.com using the XML API"
    author = "Sonorium"

    def get_ui_schema(self) -> dict:
        """Return the UI schema for the import form."""
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
                    "name": "theme_name",
                    "type": "string",
                    "label": "Theme Name (optional)",
                    "placeholder": "Leave empty to use page title",
                    "required": False,
                },
            ],
            "actions": [
                {
                    "id": "import",
                    "label": "Import Soundscape",
                    "primary": True,
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
        }

    async def handle_action(self, action: str, data: dict) -> dict:
        """Handle the import action."""
        if action == "import":
            return await self._import_soundscape(data)
        return {"success": False, "message": f"Unknown action: {action}"}

    async def _import_soundscape(self, data: dict) -> dict:
        """
        Import a soundscape from Ambient-Mixer using the XML API.

        Args:
            data: Form data with 'url' and optional 'theme_name'

        Returns:
            Result dict with success status and message
        """
        url = data.get("url", "").strip()
        custom_name = data.get("theme_name", "").strip()

        if not url:
            return {"success": False, "message": "URL is required"}

        # Validate URL
        parsed = urlparse(url)
        if "ambient-mixer" not in parsed.netloc.lower():
            return {
                "success": False,
                "message": "URL must be from ambient-mixer.com",
            }

        try:
            # Import httpx for async HTTP requests
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

                # Step 4: Determine theme folder name
                theme_name = custom_name or mix.name or f"ambient_mix_{template_id}"
                safe_theme_name = self._sanitize_folder_name(theme_name)

                # Use the audio_path from the plugin (injected from config)
                theme_folder = self.audio_path / safe_theme_name
                theme_folder.mkdir(parents=True, exist_ok=True)

                logger.info(f"Downloading to: {theme_folder}")

                # Step 5: Download audio files
                downloaded = 0
                track_metadata = {}

                for channel in mix.channels:
                    if not channel.url:
                        continue

                    success = await self._download_audio(client, channel, theme_folder)
                    if success:
                        downloaded += 1
                        # Store track metadata for attribution
                        if channel.local_filename:
                            track_metadata[channel.local_filename] = {
                                "attribution": {
                                    "original_name": channel.name,
                                    "audio_id": channel.audio_id,
                                    "source_url": channel.url,
                                },
                                "default_volume": channel.volume / 100.0,
                                "default_balance": channel.balance,
                            }

                    # Small delay between downloads
                    await asyncio.sleep(0.3)

                if downloaded == 0:
                    return {
                        "success": False,
                        "message": "Failed to download any audio files.",
                    }

                # Step 6: Create metadata.json
                if self.get_setting("auto_create_metadata", True):
                    metadata = {
                        "description": f"Imported from {url}",
                        "icon": "mdi:music",
                        "attribution": {
                            "source": "Ambient-Mixer.com",
                            "source_url": url,
                            "template_id": template_id,
                            "license": "Creative Commons Sampling Plus 1.0",
                            "license_url": "https://creativecommons.org/licenses/sampling+/1.0/",
                            "imported_date": datetime.utcnow().isoformat() + "Z",
                            "imported_by": self.id,
                        },
                        "tracks": track_metadata,
                    }

                    metadata_path = theme_folder / "metadata.json"
                    metadata_path.write_text(json.dumps(metadata, indent=2))
                    logger.info(f"Created metadata.json for {theme_name}")

                # Step 7: Write MANIFEST.json (detailed harvest info)
                manifest_path = theme_folder / "MANIFEST.json"
                manifest_path.write_text(json.dumps(mix.to_manifest(), indent=2))

                # Step 8: Write ATTRIBUTION.md
                self._write_attribution(mix, theme_folder / "ATTRIBUTION.md")

                return {
                    "success": True,
                    "message": f"Successfully imported '{theme_name}' with {downloaded} track(s). Refresh themes to see it.",
                    "data": {
                        "theme_name": theme_name,
                        "folder": str(theme_folder),
                        "tracks_downloaded": downloaded,
                        "template_id": template_id,
                    },
                }

        except Exception as e:
            logger.error(f"Error importing soundscape: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Import failed: {e}",
            }

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

        # Extract mix name from URL
        parsed = urlparse(source_url)
        mix.name = parsed.path.strip('/').split('/')[-1].replace('-', '_')
        mix.category = parsed.netloc.split('.')[0]  # e.g., "christmas" from christmas.ambient-mixer.com

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

    async def _download_audio(self, client, channel: AudioChannel, dest_dir: Path) -> bool:
        """Download a single audio file."""
        if not channel.url:
            return False

        # Generate filename: sanitized name + audio_id + extension
        ext = Path(urlparse(channel.url).path).suffix or '.mp3'
        safe_name = re.sub(r'[^\w\s-]', '', channel.name).strip().replace(' ', '_')[:50]
        filename = f"{safe_name}_{channel.audio_id}{ext}"
        dest_path = dest_dir / filename

        # Skip if already exists
        if dest_path.exists():
            logger.info(f"  Skipping (exists): {filename}")
            channel.local_filename = filename
            return True

        logger.info(f"  Downloading: {channel.name} -> {filename}")

        try:
            response = await client.get(channel.url, timeout=60.0)
            response.raise_for_status()

            dest_path.write_bytes(response.content)

            # Calculate hash
            channel.file_hash = hashlib.md5(response.content).hexdigest()
            channel.local_filename = filename

            return True

        except Exception as e:
            logger.warning(f"  Failed to download {channel.name}: {e}")
            return False

    def _sanitize_folder_name(self, name: str) -> str:
        """Create a safe folder name from a string."""
        # Remove or replace invalid characters
        safe = re.sub(r'[<>:"/\\|?*]', '', name)
        safe = safe.strip('. ')
        # Collapse multiple spaces
        safe = re.sub(r'\s+', ' ', safe)
        # Replace spaces with underscores for cleaner paths
        safe = safe.replace(' ', '_')
        return safe or "Imported_Theme"

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
            "*Generated by Sonorium Ambient Mixer Plugin*",
        ])

        filepath.write_text('\n'.join(lines))
