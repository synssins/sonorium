"""
MyNoise Importer Plugin for Sonorium

Imports soundscapes from MyNoise.net by:
1. Fetching the PHP/JS source file
2. Parsing sourceFile references and file extensions
3. Downloading audio files with proper attribution

Based on the PowerShell script pattern from Amniotic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urljoin

from sonorium.plugins.base import BasePlugin
from sonorium.obs import logger


# Constants
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
SUPPORTED_EXTENSIONS = ['.ogg', '.wav', '.mp3']


@dataclass
class AudioSource:
    """Represents a single audio source from MyNoise."""
    name: str
    base_url: str
    extension: str
    full_url: str = ""
    local_filename: Optional[str] = None
    file_hash: Optional[str] = None

    def __post_init__(self):
        if not self.full_url:
            self.full_url = f"{self.base_url}{self.extension}"


@dataclass
class MyNoiseSoundscape:
    """Represents a complete MyNoise soundscape."""
    source_url: str
    name: str = ""
    sources: list = field(default_factory=list)
    extensions_found: list = field(default_factory=list)
    harvested_at: str = ""

    def to_manifest(self) -> dict:
        """Convert to manifest dict for JSON export."""
        return {
            "source": {
                "site": "myNoise.net",
                "url": self.source_url,
                "harvested_at": self.harvested_at,
            },
            "license": {
                "name": "MyNoise Personal Use License",
                "url": "https://mynoise.net/termsOfService.php",
                "requires_attribution": True,
                "note": "Audio files are for personal use only. Please support MyNoise.net.",
            },
            "soundscape_name": self.name,
            "extensions_found": self.extensions_found,
            "sources": [
                {
                    "name": s.name,
                    "url": s.full_url,
                    "local_filename": s.local_filename,
                }
                for s in self.sources
            ],
        }


class MyNoisePlugin(BasePlugin):
    """
    Import soundscapes from MyNoise.net.

    This plugin allows users to paste a MyNoise URL and import
    all audio tracks as a new Sonorium theme with proper attribution.

    Parses PHP/JS source files to find audio URLs similar to the
    PowerShell approach.
    """

    id = "mynoise"
    name = "MyNoise Importer"
    version = "1.0.0"
    description = "Import soundscapes from MyNoise.net"
    author = "Sonorium"

    def get_ui_schema(self) -> dict:
        """Return the UI schema for the import form."""
        return {
            "type": "form",
            "fields": [
                {
                    "name": "url",
                    "type": "url",
                    "label": "MyNoise URL",
                    "placeholder": "https://mynoise.net/NoiseMachines/...",
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
            "preferred_format": {
                "type": "string",
                "default": "ogg",
                "label": "Preferred audio format",
            },
        }

    async def handle_action(self, action: str, data: dict) -> dict:
        """Handle the import action."""
        if action == "import":
            return await self._import_soundscape(data)
        return {"success": False, "message": f"Unknown action: {action}"}

    async def _import_soundscape(self, data: dict) -> dict:
        """
        Import a soundscape from MyNoise.

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
        if "mynoise" not in parsed.netloc.lower():
            return {
                "success": False,
                "message": "URL must be from mynoise.net",
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
                # Step 1: Fetch the page to find the PHP source
                logger.info(f"Fetching MyNoise page: {url}")
                response = await client.get(url)
                response.raise_for_status()
                html = response.text

                # Find the PHP source file (usually referenced in script tag)
                php_url = self._find_php_source(html, url)
                if not php_url:
                    # Try parsing audio directly from the HTML/JS
                    logger.info("No PHP source found, trying direct HTML parsing")
                    content = html
                else:
                    # Fetch the PHP source
                    logger.info(f"Fetching PHP source: {php_url}")
                    php_response = await client.get(php_url)
                    php_response.raise_for_status()
                    content = php_response.text

                # Step 2: Parse extensions and source files
                extensions = self._find_extensions(content)
                source_urls = self._find_source_files(content)

                if not extensions:
                    # Default to preferred format
                    pref = self.get_setting("preferred_format", "ogg")
                    extensions = [f".{pref}"]
                    logger.info(f"No extensions found, using preferred: {extensions}")

                if not source_urls:
                    return {
                        "success": False,
                        "message": "Could not find any audio sources in the page.",
                    }

                logger.info(f"Found {len(source_urls)} sources with extensions: {extensions}")

                # Step 3: Create soundscape object
                soundscape = MyNoiseSoundscape(
                    source_url=url,
                    extensions_found=extensions,
                    harvested_at=datetime.now().isoformat(),
                )

                # Extract name from URL path
                path_parts = parsed.path.strip('/').split('/')
                soundscape.name = path_parts[-1].replace('.php', '').replace('-', '_') if path_parts else 'mynoise_import'

                # Build audio sources
                preferred_ext = f".{self.get_setting('preferred_format', 'ogg')}"
                use_ext = preferred_ext if preferred_ext in extensions else extensions[0]

                for i, base_url in enumerate(source_urls):
                    source = AudioSource(
                        name=f"track_{i+1}",
                        base_url=base_url,
                        extension=use_ext,
                    )
                    soundscape.sources.append(source)

                # Step 4: Determine theme folder name
                theme_name = custom_name or soundscape.name or "MyNoise_Import"
                safe_theme_name = self._sanitize_folder_name(theme_name)

                theme_folder = self.audio_path / safe_theme_name
                theme_folder.mkdir(parents=True, exist_ok=True)

                logger.info(f"Downloading to: {theme_folder}")

                # Step 5: Download audio files
                downloaded = 0
                track_metadata = {}

                for source in soundscape.sources:
                    success = await self._download_audio(client, source, theme_folder)
                    if success:
                        downloaded += 1
                        if source.local_filename:
                            track_metadata[source.local_filename] = {
                                "attribution": {
                                    "original_name": source.name,
                                    "source_url": source.full_url,
                                },
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
                        "icon": "mdi:volume-high",
                        "attribution": {
                            "source": "MyNoise.net",
                            "source_url": url,
                            "license": "MyNoise Personal Use License",
                            "license_url": "https://mynoise.net/termsOfService.php",
                            "imported_date": datetime.utcnow().isoformat() + "Z",
                            "imported_by": self.id,
                            "note": "Please support MyNoise.net - these sounds are for personal use.",
                        },
                        "tracks": track_metadata,
                    }

                    metadata_path = theme_folder / "metadata.json"
                    metadata_path.write_text(json.dumps(metadata, indent=2))
                    logger.info(f"Created metadata.json for {theme_name}")

                # Step 7: Write MANIFEST.json
                manifest_path = theme_folder / "MANIFEST.json"
                manifest_path.write_text(json.dumps(soundscape.to_manifest(), indent=2))

                # Step 8: Write ATTRIBUTION.md
                self._write_attribution(soundscape, theme_folder / "ATTRIBUTION.md")

                return {
                    "success": True,
                    "message": f"Successfully imported '{theme_name}' with {downloaded} track(s). Refresh themes to see it.",
                    "data": {
                        "theme_name": theme_name,
                        "folder": str(theme_folder),
                        "tracks_downloaded": downloaded,
                    },
                }

        except Exception as e:
            logger.error(f"Error importing soundscape: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Import failed: {e}",
            }

    def _find_php_source(self, html: str, base_url: str) -> Optional[str]:
        """Find the PHP source file URL from the HTML."""
        # Look for script src with .php
        patterns = [
            r'<script[^>]+src=["\']([^"\']+\.php)["\']',
            r'src=["\']([^"\']+Sounds[^"\']*\.php)["\']',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                php_path = match.group(1)
                # Make absolute URL
                if not php_path.startswith('http'):
                    php_path = urljoin(base_url, php_path)
                return php_path

        return None

    def _find_extensions(self, content: str) -> list:
        """Find all supported file extensions in the content."""
        found = []
        for ext in SUPPORTED_EXTENSIONS:
            # Look for patterns like fileExt = ".ogg" or fileExt:".ogg"
            pattern = rf'fileExt\s*[=:]\s*["\']?{re.escape(ext)}["\']?'
            if re.search(pattern, content, re.IGNORECASE):
                found.append(ext)
                logger.debug(f"Found extension: {ext}")

        return found

    def _find_source_files(self, content: str) -> list:
        """Find all sourceFile references and extract URLs."""
        source_urls = []

        # Pattern to match sourceFile variations with URLs
        # Matches: sourceFileA[0] = 'url', sourceFile = "url", etc.
        pattern = r'sourceFile[A-Za-z]?(?:\[\d+\])?\s*[=:]\s*["\']?(https?://[^"\'\s;+]+)["\']?'

        matches = re.findall(pattern, content, re.IGNORECASE)

        for url in matches:
            # Remove any trailing file extension
            clean_url = re.sub(r'\.(ogg|wav|mp3)$', '', url, flags=re.IGNORECASE)
            clean_url = clean_url.rstrip('/')

            if clean_url and clean_url not in source_urls:
                source_urls.append(clean_url)
                logger.debug(f"Found source: {clean_url}")

        return source_urls

    async def _download_audio(self, client, source: AudioSource, dest_dir: Path) -> bool:
        """Download a single audio file."""
        if not source.full_url:
            return False

        # Generate filename
        url_path = urlparse(source.full_url).path
        filename = Path(url_path).name
        if not filename or filename == source.extension:
            filename = f"{source.name}{source.extension}"

        dest_path = dest_dir / filename

        # Skip if already exists
        if dest_path.exists():
            logger.info(f"  Skipping (exists): {filename}")
            source.local_filename = filename
            return True

        logger.info(f"  Downloading: {source.name} -> {filename}")

        try:
            response = await client.get(source.full_url, timeout=60.0)
            response.raise_for_status()

            dest_path.write_bytes(response.content)

            # Calculate hash
            source.file_hash = hashlib.md5(response.content).hexdigest()
            source.local_filename = filename

            return True

        except Exception as e:
            logger.warning(f"  Failed to download {source.name}: {e}")
            return False

    def _sanitize_folder_name(self, name: str) -> str:
        """Create a safe folder name from a string."""
        safe = re.sub(r'[<>:"/\\|?*]', '', name)
        safe = safe.strip('. ')
        safe = re.sub(r'\s+', ' ', safe)
        safe = safe.replace(' ', '_')
        return safe or "Imported_Theme"

    def _write_attribution(self, soundscape: MyNoiseSoundscape, filepath: Path):
        """Write human-readable attribution file."""
        lines = [
            f"# Attribution for {soundscape.name}",
            "",
            f"**Source:** [{soundscape.source_url}]({soundscape.source_url})",
            f"**Harvested:** {soundscape.harvested_at}",
            "",
            "## License",
            "",
            "All audio files in this folder are from MyNoise.net.",
            "",
            "**IMPORTANT:** These sounds are for personal use only.",
            "Please support the creator at [MyNoise.net](https://mynoise.net/)",
            "",
            "- Do not redistribute these files",
            "- Do not use for commercial purposes",
            "- Consider donating to support the project",
            "",
            "## Audio Files",
            "",
        ]

        for source in soundscape.sources:
            if source.local_filename:
                lines.append(f"- **{source.local_filename}**")
                lines.append(f"  - Source URL: {source.full_url}")
                lines.append("")

        lines.extend([
            "---",
            "*Generated by Sonorium MyNoise Plugin*",
        ])

        filepath.write_text('\n'.join(lines))
