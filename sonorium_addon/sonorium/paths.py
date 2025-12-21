"""
Sonorium Paths - Path management for the addon.
Replaces fmtr.tools.PackagePaths with a simple implementation.
"""
from functools import cached_property
from pathlib import Path


class PackagePaths:
    """
    Manages paths for the Sonorium package.
    """

    def __init__(self, name: str = "sonorium"):
        self._name = name

    @cached_property
    def name_ns(self) -> str:
        """Package namespace name."""
        return self._name

    @cached_property
    def package(self) -> Path:
        """Path to the package directory."""
        return Path(__file__).parent

    @cached_property
    def data(self) -> Path:
        """Path to the data directory."""
        # In HA addon context, data is at /config/sonorium
        # In development, use package/data
        config_path = Path("/config/sonorium")
        if config_path.exists():
            return config_path
        return self.package / "data"

    @cached_property
    def audio(self) -> Path:
        """Path to audio files."""
        return self.data / 'audio'

    @cached_property
    def example_700KB(self) -> Path:
        return self.audio / 'file_example_MP3_700KB.mp3'

    @cached_property
    def gambling(self) -> Path:
        return self.audio / 'A Good Bass for Gambling.mp3'


paths = PackagePaths()
