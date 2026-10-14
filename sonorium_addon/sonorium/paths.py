"""Where Sonorium keeps things on disk when the settings don't say."""
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

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


def plain_name(name) -> bool:
    """
    One file or folder name, nothing more: no separator, drive, anchor or ':'
    on Windows or Linux ("C:", "F:..", a UNC share, "a/b", ".." and ""
    aren't). A name from a request must pass this before it's joined to a
    folder: on Windows `folder / "C:"` is drive C, not a folder inside.
    """
    if not isinstance(name, str) or name.strip() in ("", ".", ".."):
        return False
    if any(c in name for c in (":", "/", "\\", "\x00")):
        return False
    for flavour in (PurePosixPath, PureWindowsPath):
        path = flavour(name)
        if path.drive or path.anchor or len(path.parts) != 1:
            return False
    return True


def inside(folder: Path, root: Path) -> bool:
    """
    The folder is directly inside root: its resolved parent is root's (a
    theme folder that is a link to another place still counts, by its place
    in root).
    """
    try:
        if Path(folder).resolve().parent == Path(root).resolve():
            return True
        return Path(folder).is_symlink() and \
            Path(os.path.abspath(folder)).parent == Path(os.path.abspath(root))
    except (OSError, RuntimeError, ValueError):
        return False


def child_folder(root: Path, name) -> Optional[Path]:
    """The folder `name` directly inside root, or None (not a plain name, not there, or outside root)."""
    if not plain_name(name):
        return None
    folder = Path(root) / name
    try:
        if not folder.is_dir():
            return None
    except (OSError, ValueError):
        return None
    return folder if inside(folder, root) else None
