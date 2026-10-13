"""
Theme packages: a theme as a list of files with checksums, for a client that
keeps its own copy of a theme (docs/APP_API.md section 10).

A theme's package is exactly what the zip export holds (GET
/api/themes/{id}/export), file for file:

- every file in the theme folder (audio, group subfolders, ATTRIBUTION.md,
  MANIFEST.json, anything else), except the theme's own metadata.json and
  presets.json and their backup or broken copies;
- each linked file (it lives in another theme's folder) at the path it has in
  this theme ("<key><.ext>", inside its group's folder when it has one);
- metadata.json and presets.json as exported: the 2.0 layout, the links
  dropped (their settings stay under the same keys), the entries following
  the files as the server uses them (no entry for a track without a file,
  such as an old "<file>.mp3" key; see theme_metadata.theme_documents_for_export),
  written as JSON with an indent of 2, UTF-8, non-ASCII characters as they are.

Paths use "/" and are relative to the theme folder.

content_hash: "sha256:" + the SHA-256 (lowercase hex) of the UTF-8 text made
of one line per file, "<path>\\t<size>\\t<sha256>\\n", the lines sorted by
path (Unicode code points, the same as sorting the UTF-8 bytes). <size> is
in bytes, in decimal; <sha256> is the file's SHA-256 in lowercase hex. The
same files give the same hash on every server; any change to any byte, any
name, any file added or removed gives a new one.

A file's SHA-256 is kept (in memory) with its size and modification time and
worked out again only when one of them changes, so listing every theme reads
only what changed since the last time.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

HASH_PREFIX = "sha256:"
_CHUNK = 1024 * 1024


# ---------------------------------------------------------------------------
# Checksums, kept while a file's size and modification time stay the same
# ---------------------------------------------------------------------------

_hash_lock = threading.Lock()
_hashes: dict[str, tuple[int, int, str]] = {}  # absolute path -> (size, mtime_ns, sha256)
_docs_lock = threading.Lock()
_docs: dict[str, tuple[tuple, dict, dict]] = {}  # theme folder -> (folder signature, metadata, presets)


def file_sha256(path: Path, stat=None) -> str:
    """The file's SHA-256 (lowercase hex), from the cache while its size and mtime are unchanged."""
    path = Path(path)
    st = stat or path.stat()
    key = str(path)
    with _hash_lock:
        cached = _hashes.get(key)
    if cached and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
        return cached[2]
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_CHUNK), b""):
            digest.update(block)
    value = digest.hexdigest()
    with _hash_lock:
        _hashes[key] = (st.st_size, st.st_mtime_ns, value)
    return value


# ---------------------------------------------------------------------------
# Which files a package holds
# ---------------------------------------------------------------------------

@dataclass
class PackageFile:
    path: str                      # "Lute/Lute song 1.mp3", always "/"
    size: int
    sha256: str
    source: Optional[Path] = None  # the file on disk (a linked file: the other theme's)
    data: Optional[bytes] = None   # metadata.json and presets.json: the exported bytes
    mtime: float = 0.0

    def line(self) -> str:
        return f"{self.path}\t{self.size}\t{self.sha256}\n"

    def to_dict(self) -> dict:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


@dataclass
class Package:
    theme_id: str
    folder: Path
    name: str
    files: list[PackageFile]
    metadata: dict
    presets: dict
    track_count: int = 0
    content_hash: str = ""
    by_path: dict = field(default_factory=dict)

    @property
    def size_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def spec_version(self):
        return self.metadata.get("spec_version")

    @property
    def updated_at(self) -> Optional[str]:
        newest = max((f.mtime for f in self.files), default=0.0)
        return datetime.fromtimestamp(newest, timezone.utc).isoformat() if newest else None

    def manifest(self) -> list[dict]:
        return [f.to_dict() for f in self.files]


def content_hash(files) -> str:
    """"sha256:" + SHA-256 of the sorted "<path>\\t<size>\\t<sha256>\\n" lines (see the module notes)."""
    lines = "".join(f.line() for f in sorted(files, key=lambda f: f.path))
    return HASH_PREFIX + hashlib.sha256(lines.encode("utf-8")).hexdigest()


def dump_json(doc: dict) -> bytes:
    """metadata.json / presets.json as the export writes them."""
    return json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")


def _is_theme_json(theme_path: Path, path: Path) -> bool:
    """The theme's own JSON files, their backups and broken copies."""
    from sonorium.core.theme_presets import METADATA_FILE, PRESETS_FILE
    if path.parent != theme_path:
        return False
    return any(path.name == base or path.name.startswith(base + ".") for base in (METADATA_FILE, PRESETS_FILE))


def _own_files(theme_path: Path) -> list[Path]:
    return sorted(p for p in theme_path.rglob("*") if p.is_file() and not _is_theme_json(theme_path, p))


def _signature(theme_path: Path) -> tuple:
    """What the exported metadata.json and presets.json are made from: the theme's JSON files, by size and mtime."""
    out = []
    try:
        for p in sorted(theme_path.iterdir()):
            if p.is_file() and _is_theme_json(theme_path, p):
                st = p.stat()
                out.append((p.name, st.st_size, st.st_mtime_ns))
    except OSError:
        pass
    return tuple(out)


def _export_documents(theme_path: Path) -> tuple[dict, dict]:
    """
    (metadata, presets) as exported, links still in metadata (a copy every call).
    Kept while the theme's JSON files and its track files stay the same (the
    entries follow the files, see theme_documents_for_export).
    """
    from sonorium.theme_files import theme_audio_files, track_key
    key = str(theme_path)
    signature = (_signature(theme_path), tuple(sorted(track_key(theme_path, f) for f in theme_audio_files(theme_path))))
    with _docs_lock:
        cached = _docs.get(key)
    if cached is None or cached[0] != signature:
        from sonorium.core.theme_metadata import theme_documents_for_export
        metadata, presets = theme_documents_for_export(theme_path)
        cached = (signature, metadata, presets)
        with _docs_lock:
            _docs[key] = cached
    return copy.deepcopy(cached[1]), copy.deepcopy(cached[2])


def build_package(theme_path: Path, theme_id: Optional[str] = None,
                  folder_for_id: Optional[Callable] = None) -> Package:
    """The theme folder's package (files, checksums, exported documents, content hash)."""
    from sonorium.core.intrusions import export_entries
    from sonorium.core.theme_presets import METADATA_FILE, PRESETS_FILE
    from sonorium.theme_files import AUDIO_EXTENSIONS, theme_audio_files

    theme_path = Path(theme_path)
    metadata, presets = _export_documents(theme_path)
    files: list[PackageFile] = []
    for source in _own_files(theme_path):
        st = source.stat()
        files.append(PackageFile(source.relative_to(theme_path).as_posix(), st.st_size, file_sha256(source, st),
                                 source=source, mtime=st.st_mtime))
    linked = export_entries(theme_path, metadata, folder_for_id)  # drops "links" from metadata
    for relative, source in linked:
        st = Path(source).stat()
        files.append(PackageFile(Path(relative).as_posix(), st.st_size, file_sha256(source, st),
                                 source=Path(source), mtime=st.st_mtime))
    newest_json = max((s[2] for s in _signature(theme_path)), default=0) / 1e9
    for name, doc in ((METADATA_FILE, metadata), (PRESETS_FILE, presets)):
        data = dump_json(doc)
        files.append(PackageFile(name, len(data), hashlib.sha256(data).hexdigest(), data=data, mtime=newest_json))
    files.sort(key=lambda f: f.path)

    tracks = len(theme_audio_files(theme_path)) + sum(
        1 for relative, _ in linked if Path(relative).suffix.lower() in AUDIO_EXTENSIONS)
    package = Package(theme_id=theme_id or metadata.get("id") or theme_path.name, folder=theme_path,
                      name=metadata.get("name") or theme_path.name, files=files, metadata=metadata,
                      presets=presets, track_count=tracks)
    package.content_hash = content_hash(files)
    package.by_path = {f.path: f for f in files}
    return package


# ---------------------------------------------------------------------------
# Themes on disk
# ---------------------------------------------------------------------------

def _theme_id(folder: Path) -> str:
    from sonorium.utils import sanitize_id
    try:
        data = json.loads((folder / "metadata.json").read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and isinstance(data.get("id"), str) and data["id"]:
            return data["id"]
    except (OSError, ValueError):
        pass
    return sanitize_id(folder.name)


def theme_folders(root: Path) -> list[tuple[str, Path]]:
    """(theme id, folder) of every theme folder under root, by folder name (staging folders left out)."""
    try:
        folders = sorted(p for p in Path(root).iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []
    return [(_theme_id(f), f) for f in folders]


# ---------------------------------------------------------------------------
# Licence and source (MANIFEST.json, docs/THEME_FORMAT.md 6.4)
# ---------------------------------------------------------------------------

_FREE_NAMES = ("creative commons", "cc0", "cc by", "cc-by", "public domain")
_CLOSED_NAMES = ("all rights reserved", "proprietary")


def redistributable(license_doc) -> Optional[bool]:
    """
    Whether the theme's licence lets it be copied on: license.redistributable
    when MANIFEST.json says so; else True for Creative Commons and public
    domain licences, False for "All rights reserved" and proprietary ones,
    None (unknown) for anything else or no licence.
    """
    if not isinstance(license_doc, dict):
        return None
    explicit = license_doc.get("redistributable")
    if isinstance(explicit, bool):
        return explicit
    name = str(license_doc.get("name") or "").strip().lower()
    if not name:
        return None
    if any(word in name for word in _CLOSED_NAMES):
        return False
    if any(word in name for word in _FREE_NAMES):
        return True
    return None


def manifest_info(theme_path: Path) -> tuple[Optional[dict], Optional[dict]]:
    """(license, source) from the theme's MANIFEST.json, None where missing."""
    try:
        data = json.loads((Path(theme_path) / "MANIFEST.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    lic, src = data.get("license"), data.get("source")
    return (lic if isinstance(lic, dict) else None), (src if isinstance(src, dict) else None)


def summary(package: Package) -> dict:
    """One entry of GET /api/themes/packages."""
    lic, src = manifest_info(package.folder)
    return {
        "theme_id": package.theme_id,
        "name": package.name,
        "spec_version": package.spec_version,
        "content_hash": package.content_hash,
        "size_bytes": package.size_bytes,
        "file_count": len(package.files),
        "track_count": package.track_count,
        "updated_at": package.updated_at,
        "license": lic,
        "source": src,
        "redistributable": redistributable(lic),
    }


def list_packages(root: Path) -> list[dict]:
    """Every theme's package summary, by theme name."""
    from sonorium.core.intrusions import folder_for_id_in
    from sonorium.obs import logger
    lookup = folder_for_id_in(Path(root))
    out = []
    for theme_id, folder in theme_folders(root):
        try:
            out.append(summary(build_package(folder, theme_id, lookup)))
        except Exception as e:  # one unreadable theme doesn't hide the others
            logger.warning(f'Theme package for "{folder.name}" could not be built: {e}')
    out.sort(key=lambda p: (str(p["name"]).casefold(), p["theme_id"]))
    return out


# ---------------------------------------------------------------------------
# The zip export, from the same list of files
# ---------------------------------------------------------------------------

def write_zip(package: Package, out) -> None:
    """The theme as a zip: "<folder>/<path>" for every file of the package."""
    prefix = package.folder.name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in package.files:
            name = f"{prefix}/{f.path}"
            if f.data is not None:
                zf.writestr(name, f.data)
            else:
                zf.write(f.source, name)


# ---------------------------------------------------------------------------
# Byte ranges (RFC 9110 14.2), one range per request
# ---------------------------------------------------------------------------

class RangeNotSatisfiable(Exception):
    pass


def parse_range(header: Optional[str], size: int) -> Optional[tuple[int, int]]:
    """(first, last) byte, inclusive, of a "bytes=" Range header; None for the whole file."""
    if not header:
        return None
    unit, _, spec = header.strip().partition("=")
    if unit.strip().lower() != "bytes" or "," in spec:
        return None  # another unit, or several ranges: the whole file (allowed by RFC 9110)
    first, dash, last = spec.strip().partition("-")
    if not dash:
        return None
    try:
        if first == "":
            n = int(last)
            if n <= 0:
                raise RangeNotSatisfiable()
            return max(0, size - n), size - 1
        start = int(first)
        end = int(last) if last.strip() else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        raise RangeNotSatisfiable()
    return start, min(end, size - 1)


def iter_file(path: Path, start: int, length: int, chunk: int = 256 * 1024) -> Iterator[bytes]:
    with open(path, "rb") as f:
        f.seek(start)
        left = length
        while left > 0:
            block = f.read(min(chunk, left))
            if not block:
                break
            left -= len(block)
            yield block
