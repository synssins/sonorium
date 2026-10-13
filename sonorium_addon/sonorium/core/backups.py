"""
Theme backups from the Sonorium app: each account's own copies of what its
phones hold, kept apart from the server's themes (web/backup_routes.py has
the API; the API page, /docs, describes every route).

What a phone sends, per theme:

    a mix     for a theme it got from this server (the key is the server
              theme's id): its presets, sequences, track settings and plugin
              attribute blocks, as one JSON document
    a theme   for a theme made on the phone (the key is the phone's own id):
              the whole theme as a package, the same file list with sizes and
              SHA-256 as core/theme_package.py, then each file it doesn't
              have yet (resumable, by byte range)

plus the phone's playlists (one document per phone).

Rules:

- Backups belong to an account ("user:<id>", or without accounts "local" /
  "ha:<HA user id>", web/personal_routes.account_of) and, inside it, to the
  linked phone that sent them. Only that account sees them; admins don't.
- A phone writes only its own backups: phones never overwrite each other.
  Per phone and theme the latest backup wins (a whole-theme upload replaces
  the previous one once its last file is in).
- Nothing here writes into the server's themes. core/backup_load.py does,
  and only when the account switched "load into themes" on.
- A phone that is unlinked keeps its backups (with the name it last had)
  until the account deletes them; removing the account deletes them all.
- Space: Settings > Advanced "backup_limit_mb" per account (0 = no limit).
  Answers carry "storage" with a warning from 90 %; a backup that would go
  over the limit is refused (507), nothing over it is ever kept.

On disk (<config>/backups), every folder named by a hash, so no name from a
request ever reaches a path:

    <h(account)>/account.json                 the account, its settings, what was loaded
    <h(account)>/blobs/<sha256>                whole-theme files, shared by its phones
    <h(account)>/blobs/<sha256>.part           a file being uploaded
    <h(account)>/devices/<h(device)>/device.json
    <h(account)>/devices/<h(device)>/playlists.json
    <h(account)>/devices/<h(device)>/themes/<h(key)>.json
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

FOLDER = "backups"
ACCOUNT_FILE = "account.json"
DEVICE_FILE = "device.json"
PLAYLISTS_FILE = "playlists.json"
LIMIT_SETTING = "backup_limit_mb"
WARN_SHARE = 0.9

# What one request may carry (whole-theme files come one by one, by byte range)
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 12
MAX_ITEMS = 2000          # presets, sequences, tracks, playlists in one document
MAX_FILES = 10000         # files in one whole-theme backup
MAX_PATH = 255
MAX_PARTS = 4             # theme folder / group / file (+1 spare)
MAX_ID = 200
MAX_NAME = 200
SHA256 = re.compile(r"^[0-9a-f]{64}$")
# A whole theme holds audio and its documents; nothing that runs or renders
FILE_TYPES = frozenset({".mp3", ".wav", ".flac", ".ogg", ".json", ".md", ".txt",
                        ".png", ".jpg", ".jpeg", ".webp"})
_NOT_IN_NAMES = set('<>:"|?*\\')
MIX_KEYS = ("name", "presets", "sequences", "sequence_defaults", "tracks", "groups", "plugins")
PRESET_KEYS = ("name", "is_default", "tracks", "groups", "plugins")


class BackupError(Exception):
    """A request the backups refuse: status and the message for the caller."""

    def __init__(self, message: str, status: int = 400, extra: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.extra = extra or {}


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path, default=None):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return copy.deepcopy(default)
    return data if isinstance(data, dict) else copy.deepcopy(default)


def _write(path: Path, data: dict) -> None:
    """Atomic, owner-only where the system has it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _remove(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


def _remove_tree(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_id(value, what: str) -> str:
    """A key, preset or device id: text, 1..MAX_ID characters, no control characters, no slashes."""
    if not isinstance(value, str) or not value or len(value) > MAX_ID:
        raise BackupError(f"{what} must be text of 1 to {MAX_ID} characters")
    if any(ord(c) < 32 or c in "/\\" for c in value) or value in (".", ".."):
        raise BackupError(f"{what} has characters that aren't allowed")
    return value


def _check_name(value, what: str = "name") -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_NAME or any(ord(c) < 32 for c in value):
        raise BackupError(f"{what} must be text of at most {MAX_NAME} characters")
    return value.strip()


def _check_json(value, where: str, depth: int = 0) -> None:
    """Plain JSON (objects with text keys, lists, text, finite numbers, true/false, null), not too deep."""
    if depth > MAX_DEPTH:
        raise BackupError(f"{where} is nested too deeply")
    if isinstance(value, dict):
        if len(value) > MAX_ITEMS:
            raise BackupError(f"{where} has more than {MAX_ITEMS} entries")
        for k, v in value.items():
            if not isinstance(k, str) or len(k) > 1000:
                raise BackupError(f"{where} has a key that isn't short text")
            _check_json(v, where, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_ITEMS:
            raise BackupError(f"{where} has more than {MAX_ITEMS} entries")
        for v in value:
            _check_json(v, where, depth + 1)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise BackupError(f"{where} has a number that isn't finite")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise BackupError(f"{where} isn't plain JSON")


def _object_of_objects(value, where: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BackupError(f"{where} must be an object")
    for k, v in value.items():
        check_id(k, f"An id in {where}")
        if not isinstance(v, dict):
            raise BackupError(f"{where} '{k}' must be an object")
    return value


def check_mix(body) -> dict:
    """
    A mix as stored: {"name", "presets": {id: preset}, "sequences": {id: sequence},
    "sequence_defaults", "tracks": {key: settings}, "groups", "plugins"}, every
    part optional. Presets keep only name, is_default, tracks, groups and
    plugins. Raises BackupError.
    """
    if not isinstance(body, dict):
        raise BackupError("Expected a JSON object")
    unknown = sorted(set(body) - set(MIX_KEYS))
    if unknown:
        raise BackupError(f"Unknown field '{unknown[0]}' (a mix has {', '.join(MIX_KEYS)})")
    _check_json(body, "The mix")
    mix = {"name": _check_name(body.get("name"))}
    presets = {}
    for pid, preset in _object_of_objects(body.get("presets"), "presets").items():
        kept = {k: preset[k] for k in PRESET_KEYS if k in preset}
        kept["name"] = _check_name(kept.get("name"), f"Preset '{pid}' name") or pid
        for part in ("tracks", "groups", "plugins"):
            if part in kept and not isinstance(kept[part], dict):
                raise BackupError(f"Preset '{pid}' {part} must be an object")
        presets[pid] = kept
    mix["presets"] = presets
    mix["sequences"] = dict(_object_of_objects(body.get("sequences"), "sequences"))
    mix["tracks"] = dict(_object_of_objects(body.get("tracks"), "tracks"))
    for part in ("sequence_defaults", "groups", "plugins"):
        value = body.get(part)
        if value is not None and not isinstance(value, dict):
            raise BackupError(f"{part} must be an object")
        if value:
            mix[part] = value
    return mix


def check_playlists(body) -> dict:
    """{"playlists": [...] or {id: playlist}}: stored as sent (plain JSON). Raises BackupError."""
    if not isinstance(body, dict) or set(body) - {"playlists"}:
        raise BackupError('Expected {"playlists": ...}')
    playlists = body.get("playlists")
    if not isinstance(playlists, (list, dict)):
        raise BackupError("playlists must be a list or an object")
    _check_json(playlists, "playlists")
    return {"playlists": playlists}


def check_path(path) -> str:
    """
    A file's path in a whole-theme backup: "/"-separated, relative, at most
    MAX_PATH characters and MAX_PARTS parts, no "." or ".." part, no hidden
    part, no character a file system refuses, one of FILE_TYPES.
    """
    if not isinstance(path, str) or not path or len(path) > MAX_PATH:
        raise BackupError(f"A file path must be text of 1 to {MAX_PATH} characters")
    parts = path.split("/")
    if len(parts) > MAX_PARTS:
        raise BackupError(f"'{path}': at most {MAX_PARTS} folder levels")
    for part in parts:
        if not part or part in (".", "..") or part.startswith(".") or part != part.strip() or part.endswith("."):
            raise BackupError(f"'{path}' isn't a path inside a theme")
        if any(ord(c) < 32 or c in _NOT_IN_NAMES for c in part):
            raise BackupError(f"'{path}' has characters that aren't allowed")
    dot = parts[-1].rfind(".")
    if dot <= 0 or parts[-1][dot:].lower() not in FILE_TYPES:
        raise BackupError(f"'{path}': only {', '.join(sorted(FILE_TYPES))} files")
    return path


def check_manifest(body) -> dict:
    """{"name", "files": [{"path", "size", "sha256"}]}, checked. Raises BackupError."""
    if not isinstance(body, dict) or set(body) - {"name", "files"}:
        raise BackupError('Expected {"name", "files": [{"path", "size", "sha256"}]}')
    files = body.get("files")
    if not isinstance(files, list) or not files:
        raise BackupError("files must be a list of at least one file")
    if len(files) > MAX_FILES:
        raise BackupError(f"At most {MAX_FILES} files in a theme")
    seen, out = set(), []
    for f in files:
        if not isinstance(f, dict) or set(f) - {"path", "size", "sha256"}:
            raise BackupError('Each file is {"path", "size", "sha256"}')
        path = check_path(f.get("path"))
        size = f.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BackupError(f"'{path}': size must be a whole number of bytes")
        sha = f.get("sha256")
        if not isinstance(sha, str) or not SHA256.match(sha):
            raise BackupError(f"'{path}': sha256 must be 64 lowercase hex characters")
        folded = path.casefold()  # two names one file system would see as one
        if folded in seen:
            raise BackupError(f"'{path}' is listed twice")
        seen.add(folded)
        out.append({"path": path, "size": size, "sha256": sha})
    out.sort(key=lambda f: f["path"])
    return {"name": _check_name(body.get("name")), "files": out}


def content_hash(files: list[dict]) -> str:
    """As core/theme_package.py: "sha256:" + SHA-256 of the sorted "<path>\\t<size>\\t<sha256>\\n" lines."""
    lines = "".join(f"{f['path']}\t{f['size']}\t{f['sha256']}\n" for f in sorted(files, key=lambda f: f["path"]))
    return "sha256:" + hashlib.sha256(lines.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

class BackupStore:
    """Every account's backups under one folder. Thread-safe per account."""

    def __init__(self, root: Path, limit_mb=None):
        self.root = Path(root)
        self._limit_mb = limit_mb  # a function giving Settings > Advanced's value (MB, 0 = none)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_lock = threading.Lock()
        self._writing: set[str] = set()  # blobs being uploaded right now

    # ------------------------------------------------------------ places

    def lock(self, owner: str) -> threading.RLock:
        with self._locks_lock:
            return self._locks.setdefault(owner, threading.RLock())

    def account_dir(self, owner: str) -> Path:
        return self.root / _h("account:" + owner)

    def device_dir(self, owner: str, device_id: str) -> Path:
        return self.account_dir(owner) / "devices" / _h("device:" + device_id)

    def theme_file(self, owner: str, device_id: str, key: str) -> Path:
        return self.device_dir(owner, device_id) / "themes" / (_h("theme:" + key) + ".json")

    def blob(self, owner: str, sha: str) -> Path:
        return self.account_dir(owner) / "blobs" / sha  # sha is checked: 64 hex characters

    # ------------------------------------------------------------ account

    def account(self, owner: str) -> dict:
        doc = _read(self.account_dir(owner) / ACCOUNT_FILE, {})
        doc["owner"] = owner
        doc.setdefault("settings", {})
        doc["settings"].setdefault("load_into_themes", False)
        doc.setdefault("loaded", {})
        return doc

    def save_account(self, owner: str, doc: dict) -> None:
        _write(self.account_dir(owner) / ACCOUNT_FILE, doc)

    def settings(self, owner: str) -> dict:
        return dict(self.account(owner)["settings"])

    def set_load_into_themes(self, owner: str, on: bool) -> dict:
        with self.lock(owner):
            doc = self.account(owner)
            doc["settings"]["load_into_themes"] = bool(on)
            self.save_account(owner, doc)
            return dict(doc["settings"])

    def owners(self) -> list[str]:
        out = []
        try:
            for entry in self.root.iterdir():
                doc = _read(entry / ACCOUNT_FILE, None)
                if doc and isinstance(doc.get("owner"), str):
                    out.append(doc["owner"])
        except OSError:
            pass
        return out

    # ------------------------------------------------------------ space

    def limit_bytes(self) -> int:
        try:
            mb = float(self._limit_mb()) if self._limit_mb else 0.0
        except Exception:
            mb = 0.0
        return int(mb * 1024 * 1024) if mb > 0 and math.isfinite(mb) else 0

    def used_bytes(self, owner: str) -> int:
        total = 0
        for folder, _, files in os.walk(self.account_dir(owner)):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(folder, name))
                except OSError:
                    pass
        return total

    def storage(self, owner: str) -> dict:
        used, limit = self.used_bytes(owner), self.limit_bytes()
        return {"used_bytes": used, "limit_bytes": limit or None,
                "warning": bool(limit and used >= WARN_SHARE * limit)}

    def _room_for(self, owner: str, adding: int) -> None:
        limit = self.limit_bytes()
        if not limit or adding <= 0:
            return
        used = self.used_bytes(owner)
        if used + adding > limit:
            raise BackupError(
                f"Not enough backup space: this needs {adding} bytes, {max(0, limit - used)} are left of "
                f"{limit}. Delete older backups, or raise Settings › Advanced › Backup space per user.",
                507, {"storage": self.storage(owner)})

    # ------------------------------------------------------------ devices

    def note_device(self, owner: str, device_id: str, info: Optional[dict]) -> None:
        """The phone's name, platform and model as it has them now (shown after it's unlinked too)."""
        path = self.device_dir(owner, device_id) / DEVICE_FILE
        doc = _read(path, {})
        doc["device_id"] = device_id
        for k in ("name", "platform", "model"):
            if info and info.get(k):
                doc[k] = str(info[k])[:100]
        doc["updated"] = _now()
        _write(path, doc)

    def _device_docs(self, owner: str) -> list[tuple[Path, dict]]:
        out = []
        try:
            folders = sorted((self.account_dir(owner) / "devices").iterdir())
        except OSError:
            return []
        for folder in folders:
            doc = _read(folder / DEVICE_FILE, None)
            if doc and isinstance(doc.get("device_id"), str):
                out.append((folder, doc))
        return out

    def _theme_docs(self, device_folder: Path) -> list[dict]:
        out = []
        try:
            files = sorted((device_folder / "themes").glob("*.json"))
        except OSError:
            return []
        for f in files:
            doc = _read(f, None)
            if doc and isinstance(doc.get("key"), str):
                doc["_size"] = f.stat().st_size if f.exists() else 0
                out.append(doc)
        return out

    def has_device(self, owner: str, device_id: str) -> bool:
        return (self.device_dir(owner, device_id) / DEVICE_FILE).is_file()

    # ------------------------------------------------------------ mixes and playlists

    def put_mix(self, owner: str, device_id: str, key: str, body, device_info=None) -> dict:
        """Store a phone's mix of a server theme (replaces its previous backup of that theme)."""
        mix = check_mix(body)
        data = {"key": key, "kind": "mix", "name": mix.pop("name", None), "updated": _now(), "mix": mix}
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_JSON_BYTES:
            raise BackupError(f"The mix is larger than {MAX_JSON_BYTES} bytes", 413)
        with self.lock(owner):
            path = self.theme_file(owner, device_id, key)
            old = path.stat().st_size if path.exists() else 0
            self._room_for(owner, len(encoded) - old)
            self.note_device(owner, device_id, device_info)
            _write(path, data)
            self._collect(owner)
        return self.theme_summary(data, len(encoded))

    def put_playlists(self, owner: str, device_id: str, body, device_info=None) -> dict:
        doc = check_playlists(body)
        doc["updated"] = _now()
        encoded = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_JSON_BYTES:
            raise BackupError(f"The playlists are larger than {MAX_JSON_BYTES} bytes", 413)
        with self.lock(owner):
            path = self.device_dir(owner, device_id) / PLAYLISTS_FILE
            old = path.stat().st_size if path.exists() else 0
            self._room_for(owner, len(encoded) - old)
            self.note_device(owner, device_id, device_info)
            _write(path, doc)
        return {"playlists": len(doc["playlists"]), "updated": doc["updated"]}

    def playlists(self, owner: str, device_id: str) -> Optional[dict]:
        return _read(self.device_dir(owner, device_id) / PLAYLISTS_FILE, None)

    def theme(self, owner: str, device_id: str, key: str) -> Optional[dict]:
        return _read(self.theme_file(owner, device_id, key), None)

    def mixes(self, owner: str) -> list[tuple[str, dict, dict]]:
        """(device id, device doc, theme doc) of every mix backup of the account."""
        out = []
        for folder, device in self._device_docs(owner):
            for doc in self._theme_docs(folder):
                if doc.get("kind") == "mix":
                    out.append((device["device_id"], device, doc))
        return out

    # ------------------------------------------------------------ whole themes

    def _blob_ok(self, owner: str, entry: dict) -> bool:
        path = self.blob(owner, entry["sha256"])
        try:
            return path.stat().st_size == entry["size"]
        except OSError:
            return False

    def _part_size(self, owner: str, sha: str) -> int:
        try:
            return self.blob(owner, sha).with_name(sha + ".part").stat().st_size
        except OSError:
            return 0

    def _missing(self, owner: str, files: list[dict]) -> list[dict]:
        out, seen = [], set()
        for f in files:
            if f["sha256"] in seen or self._blob_ok(owner, f):
                continue
            seen.add(f["sha256"])
            out.append({**f, "received": self._part_size(owner, f["sha256"])})
        return out

    def put_manifest(self, owner: str, device_id: str, key: str, body, device_info=None) -> dict:
        """
        Start (or resume) a whole-theme backup: the files it has, by SHA-256.
        Answers which are still missing (with the bytes already received of
        each); with none missing it is the theme's backup at once.
        """
        manifest = check_manifest(body)
        with self.lock(owner):
            path = self.theme_file(owner, device_id, key)
            doc = _read(path, None) or {"key": key, "kind": "theme"}
            missing = self._missing(owner, manifest["files"])
            self._room_for(owner, sum(f["size"] - f["received"] for f in missing))
            self.note_device(owner, device_id, device_info)
            doc["pending"] = {**manifest, "started": _now()}
            _write(path, doc)
            if not missing:
                doc = self._commit(owner, path, doc)
        return self.upload_status(owner, device_id, key)

    def upload_status(self, owner: str, device_id: str, key: str) -> dict:
        doc = self.theme(owner, device_id, key)
        if doc is None:
            raise BackupError("No backup of this theme from this device", 404)
        pending = doc.get("pending")
        if not pending:
            return {"complete": doc.get("kind") == "theme", "missing": [],
                    "content_hash": doc.get("content_hash")}
        return {"complete": False, "missing": self._missing(owner, pending["files"]),
                "content_hash": content_hash(pending["files"])}

    def begin_file(self, owner: str, device_id: str, key: str, path: str, start: int) -> tuple[dict, Path]:
        """
        A file of the pending upload is about to arrive from byte `start`
        (0, or the bytes already received: resuming). Returns (its entry, the
        partial file). Raises BackupError (409 with "received" when `start`
        is anywhere else).
        """
        with self.lock(owner):
            doc = self.theme(owner, device_id, key)
            pending = (doc or {}).get("pending")
            if not pending:
                raise BackupError("Send the theme's file list first (PUT .../package)", 409)
            entry = next((f for f in pending["files"] if f["path"] == path), None)
            if entry is None:
                raise BackupError(f"No file '{path}' in this upload", 404)
            sha = entry["sha256"]
            if self._blob_ok(owner, entry):
                raise BackupError("This file is already here", 409, {"received": entry["size"]})
            if sha in self._writing:
                raise BackupError("This file is being uploaded right now", 409)
            part = self.blob(owner, sha).with_name(sha + ".part")
            received = self._part_size(owner, sha)
            if start != 0 and start != received:
                raise BackupError(f"Resume from byte {received}", 409, {"received": received})
            part.parent.mkdir(parents=True, exist_ok=True)
            if start == 0:
                _remove(part)
            self._writing.add(sha)
            return entry, part

    def end_file(self, owner: str, device_id: str, key: str, entry: dict, part: Path) -> dict:
        """The bytes of a request are in: a whole file is checked and kept; the theme is backed up once every file is."""
        sha = entry["sha256"]
        try:
            with self.lock(owner):
                size = part.stat().st_size if part.exists() else 0
                if size < entry["size"]:
                    return {"path": entry["path"], "received": size, "complete": False}
                from sonorium.core.theme_package import file_sha256
                if size != entry["size"] or file_sha256(part) != sha:
                    _remove(part)
                    raise BackupError(f"'{entry['path']}' doesn't match its sha256; send it again", 422)
                os.replace(part, self.blob(owner, sha))
                path = self.theme_file(owner, device_id, key)
                doc = _read(path, None)
                if doc and doc.get("pending") and not self._missing(owner, doc["pending"]["files"]):
                    self._commit(owner, path, doc)
                status = self.upload_status(owner, device_id, key)
                return {"path": entry["path"], "received": size, **status}
        finally:
            self._writing.discard(sha)

    def abort_file(self, sha: str) -> None:
        self._writing.discard(sha)

    def _commit(self, owner: str, path: Path, doc: dict) -> dict:
        pending = doc.pop("pending")
        doc = {"key": doc["key"], "kind": "theme", "name": pending.get("name"), "updated": _now(),
               "files": pending["files"], "content_hash": content_hash(pending["files"]),
               "size_bytes": sum(f["size"] for f in pending["files"])}
        _write(path, doc)
        self._collect(owner)
        return doc

    def package(self, owner: str, device_id: str, key: str) -> dict:
        """A whole-theme backup as a theme package: {key, name, content_hash, size_bytes, files}."""
        doc = self.theme(owner, device_id, key)
        if doc is None or doc.get("kind") != "theme":
            raise BackupError("No whole-theme backup of this theme from this device", 404)
        return {"key": key, "name": doc.get("name"), "updated": doc.get("updated"),
                "content_hash": doc["content_hash"], "size_bytes": doc["size_bytes"], "files": doc["files"]}

    def file_of(self, owner: str, device_id: str, key: str, path: str) -> tuple[dict, Path]:
        """(entry, the file on disk) of a whole-theme backup, looked up in its list only."""
        package = self.package(owner, device_id, key)
        entry = next((f for f in package["files"] if f["path"] == path), None)
        if entry is None or not self._blob_ok(owner, entry):
            raise BackupError(f"No file '{path}' in this backup", 404)
        return entry, self.blob(owner, entry["sha256"])

    # ------------------------------------------------------------ deleting

    def _collect(self, owner: str) -> None:
        """Files no backup (or upload under way) of the account uses any more go."""
        wanted, uploading = set(), set()
        for folder, _ in self._device_docs(owner):
            for doc in self._theme_docs(folder):
                wanted.update(f["sha256"] for f in doc.get("files") or [])
                pend = (doc.get("pending") or {}).get("files") or []
                uploading.update(f["sha256"] for f in pend)
        try:
            blobs = list((self.account_dir(owner) / "blobs").iterdir())
        except OSError:
            return
        for blob in blobs:
            name = blob.name
            sha = name[:-5] if name.endswith(".part") else name
            if sha in self._writing:
                continue
            if name.endswith(".part") and sha in uploading:
                continue
            if not name.endswith(".part") and (sha in wanted or sha in uploading):
                continue
            _remove(blob)

    def delete_theme(self, owner: str, device_id: str, key: str) -> bool:
        with self.lock(owner):
            path = self.theme_file(owner, device_id, key)
            if not path.exists():
                return False
            _remove(path)
            self._forget_loaded(owner, device_id, key)
            self._collect(owner)
            return True

    def delete_device(self, owner: str, device_id: str) -> bool:
        with self.lock(owner):
            folder = self.device_dir(owner, device_id)
            if not folder.exists():
                return False
            _remove_tree(folder)
            self._forget_loaded(owner, device_id, None)
            self._collect(owner)
            return True

    def delete_owner(self, owner: str) -> bool:
        """Every backup of an account (the account was removed)."""
        with self.lock(owner):
            folder = self.account_dir(owner)
            if not folder.exists():
                return False
            _remove_tree(folder)
            return True

    def _forget_loaded(self, owner: str, device_id: str, key: Optional[str]) -> None:
        doc = self.account(owner)
        prefix = loaded_key(device_id, key) if key is not None else loaded_key(device_id, "")
        drop = [k for k in doc["loaded"] if (k == prefix if key is not None else k.startswith(prefix))]
        if drop:
            for k in drop:
                doc["loaded"].pop(k, None)
            self.save_account(owner, doc)

    # ------------------------------------------------------------ listing

    @staticmethod
    def theme_summary(doc: dict, size: int) -> dict:
        row = {"key": doc["key"], "kind": doc.get("kind"), "name": doc.get("name"), "updated": doc.get("updated")}
        if doc.get("kind") == "mix":
            mix = doc.get("mix") or {}
            row.update(size_bytes=size, presets=len(mix.get("presets") or {}),
                       sequences=len(mix.get("sequences") or {}))
        else:
            row.update(size_bytes=doc.get("size_bytes", 0), file_count=len(doc.get("files") or []),
                       content_hash=doc.get("content_hash"))
        if doc.get("pending"):
            row["uploading"] = True
        return row

    def listing(self, owner: str, device_rows=None) -> list[dict]:
        """
        The account's backups by device, newest first. device_rows(device_id)
        gives the phone's row from the pairing system (None: unlinked).
        """
        out = []
        for folder, device in self._device_docs(owner):
            device_id = device["device_id"]
            linked = device_rows(device_id) if device_rows else None
            themes = [self.theme_summary(d, d.get("_size", 0)) for d in self._theme_docs(folder)]
            themes = [t for t in themes if t["kind"] == "mix" or t.get("file_count") is not None
                      or t.get("uploading")]
            themes.sort(key=lambda t: (str(t.get("name") or t["key"]).casefold(), t["key"]))
            playlists = _read(folder / PLAYLISTS_FILE, None)
            if not themes and playlists is None:
                continue  # everything of this phone was deleted
            out.append({
                "device_id": device_id,
                "name": (linked or {}).get("name") or device.get("name") or "Phone",
                "platform": (linked or {}).get("platform") or device.get("platform") or "",
                "model": (linked or {}).get("model") or device.get("model") or "",
                "linked": linked is not None,
                "updated": device.get("updated"),
                "size_bytes": sum(t.get("size_bytes") or 0 for t in themes)
                + ((folder / PLAYLISTS_FILE).stat().st_size if playlists is not None else 0),
                "playlists": len((playlists or {}).get("playlists") or []) if playlists is not None else None,
                "themes": themes,
            })
        out.sort(key=lambda d: d.get("updated") or "", reverse=True)
        return out


def loaded_key(device_id: str, key: str) -> str:
    """Where account.json "loaded" keeps what a phone's mix of a theme put into the server theme."""
    return f"{device_id}\n{key}"


def default_root() -> Path:
    from sonorium.auth import config
    return Path(config.config_dir()) / FOLDER


_stores: dict[str, BackupStore] = {}
_stores_lock = threading.Lock()


def _limit_mb() -> float:
    from sonorium import playback_values
    return playback_values.get(LIMIT_SETTING)


def store_for(root: Optional[Path] = None) -> BackupStore:
    """The one store of a folder (default: <config>/backups), limited by Settings > Advanced."""
    root = Path(root or default_root())
    with _stores_lock:
        store = _stores.get(str(root))
        if store is None:
            store = _stores[str(root)] = BackupStore(root, _limit_mb)
        return store


def remove_account(owner: str, root: Optional[Path] = None) -> None:
    """Delete an account's backups (Users: the account was removed). Never raises."""
    try:
        if store_for(root).delete_owner(owner):
            from sonorium.obs import logger
            logger.info(f"Backups of {owner} deleted with the account")
    except Exception:
        pass
