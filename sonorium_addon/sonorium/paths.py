"""Where Sonorium keeps things on disk when the settings don't say."""
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent
# The add-on's share for Sonorium (Home Assistant's /config); in a development
# checkout, which has none, a folder inside the package stands in for it
ADDON_SHARE = Path("/config/sonorium")


def default_audio_dir() -> Path:
    """The folder of theme folders when SONORIUM__PATH_AUDIO names none."""
    share = ADDON_SHARE if ADDON_SHARE.exists() else PACKAGE_DIR / "data"
    return share / "audio"


def audio_dir() -> Path:
    """The folder of theme folders Sonorium was started with (SONORIUM__PATH_AUDIO)."""
    from sonorium.settings import settings
    return Path(settings.path_audio)
