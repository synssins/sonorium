"""
Theme backups from the Sonorium app (core/backups.py has the rules, and
core/backup_load.py "load my phone presets into themes"). add_backup_routes()
adds them to the /api router that create_api_router() builds; each has its
permission in sonorium/auth/permissions.py. Every route works on the
caller's own account only; there is no route to anyone else's backups.

    GET    /api/me/backups                                       the account's backups by phone, space used
    GET    /api/me/backups/settings                              {"load_into_themes", "can_load"}
    PUT    /api/me/backups/settings                              {"load_into_themes": true|false}
    GET    /api/me/backups/{device_id}/playlists                 a phone's playlists
    PUT    /api/me/backups/{device_id}/playlists                 a phone backs up its playlists
    GET    /api/me/backups/{device_id}/themes/{key}              one theme's backup (a mix: the whole mix)
    PUT    /api/me/backups/{device_id}/themes/{key}              a phone backs up its mix of server theme {key}
    DELETE /api/me/backups/{device_id}/themes/{key}              delete one theme's backup
    GET    /api/me/backups/{device_id}/themes/{key}/package      a whole-theme backup's file list (restore)
    PUT    /api/me/backups/{device_id}/themes/{key}/package      a phone starts or resumes a whole-theme backup
    GET    /api/me/backups/{device_id}/themes/{key}/upload       what that upload still needs
    GET    /api/me/backups/{device_id}/themes/{key}/files/{path} one file (Range, ETag)
    PUT    /api/me/backups/{device_id}/themes/{key}/files/{path} a phone sends one file (Content-Range to resume)
    DELETE /api/me/backups/{device_id}                           delete a phone's backups

The account (account_of in web/personal_routes.py): a signed-in user, or a
user's linked phone, is "user:<id>"; a key acts for the user who made it;
without accounts, "local" (or each Home Assistant user their own). Nobody
signed in while there are accounts: 401.

{device_id} is a linked phone's id (GET /api/devices/self), or "self" for
the calling phone. Only a linked phone writes, and only its own backups
(anything else: 403). Reading and deleting take any phone of the account,
also one that has been unlinked since.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse

from sonorium.obs import logger

SELF = "self"


def _principal(request: Request):
    from sonorium.auth.middleware import principal_of
    return principal_of(request)


def _service(request: Request):
    return ((getattr(request, "scope", None) or {}).get("state") or {}).get("auth_service")


def _accounts(request: Request):
    users = (getattr(_service(request), "authenticators", None) or {}).get("users")
    return getattr(users, "accounts", None)


def owner_of(request: Request) -> str:
    """The account whose backups the caller works on (401 when there is none)."""
    from sonorium.web.personal_routes import _accounts_on, account_of
    principal = _principal(request)
    accounts_on = _accounts_on(request)
    if principal.kind == "key" and principal.key_id and not principal.user_id:
        service = _service(request)
        try:
            record = service.tokens.get(principal.key_id) or {}
        except Exception:
            record = {}
        maker = (record.get("data") or {}).get("created_by")
        if maker:
            return f"user:{maker}"
        if accounts_on:
            raise HTTPException(status_code=401, detail="This key belongs to nobody: backups need a user's key")
    owner = account_of(principal, accounts_on)
    if owner is None:
        raise HTTPException(status_code=401, detail="Sign in to use your backups")
    return owner


def _linked(request: Request, device_id: str) -> Optional[dict]:
    service = _service(request)
    try:
        return service.device(device_id) if service is not None else None
    except Exception:
        return None


def _device_rows(request: Request, owner: str):
    """A phone's row from the pairing system, if it is still linked to this account."""
    def row(device_id: str) -> Optional[dict]:
        found = _linked(request, device_id)
        if found is None:
            return None
        if owner.startswith("user:") and found.get("user_id") not in (None, owner[5:]):
            return None
        return found
    return row


def can_load(request: Request, owner: str) -> bool:
    """May the account's backups go into the server's themes: the account may edit themes."""
    from sonorium.auth import accounts as accounts_mod
    if not owner.startswith("user:"):
        return True  # no accounts: everyone may edit themes
    accounts = _accounts(request)
    user = accounts.user(owner[5:]) if accounts is not None else None
    if user is None:
        principal = _principal(request)
        return principal.user_id == owner[5:] and principal.can("th.edit")
    return not user.get("disabled") and "th.edit" in accounts_mod.role_perms(user.get("role"))


def _error(e) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e), headers=None)


def _error_response(e) -> JSONResponse:
    return JSONResponse({"detail": str(e), **e.extra}, status_code=e.status)


async def _json_capped(request: Request):
    """The request's JSON, at most backups.MAX_JSON_BYTES read (413 beyond)."""
    import json
    from sonorium.core.backups import MAX_JSON_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_JSON_BYTES:
        raise HTTPException(status_code=413, detail=f"At most {MAX_JSON_BYTES} bytes")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_JSON_BYTES:
            raise HTTPException(status_code=413, detail=f"At most {MAX_JSON_BYTES} bytes")
    try:
        return json.loads(bytes(raw).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")


def _content_range(header: Optional[str], size: int) -> tuple[int, Optional[int]]:
    """(first byte, bytes in this request or None) from "bytes <first>-<last>/<size>"; (0, None) without one."""
    if not header:
        return 0, None
    unit, _, spec = header.strip().partition(" ")
    span, _, total = spec.partition("/")
    first, dash, last = span.partition("-")
    try:
        if unit.lower() != "bytes" or not dash or int(total) != size:
            raise ValueError
        start, end = int(first), int(last)
    except ValueError:
        raise HTTPException(status_code=400, detail=f'Content-Range must be "bytes <first>-<last>/{size}"')
    if start < 0 or end < start or end >= size:
        raise HTTPException(status_code=416, detail=f"The range must be inside the file's {size} bytes")
    return start, end - start + 1


def add_backup_routes(router, *, session_manager=None, root=None) -> None:
    """The backup routes, on create_api_router()'s /api router. root: the backups folder (tests)."""
    from sonorium.core import backups as B

    def store_of(request: Request):
        if root is not None:
            return B.store_for(Path(root))
        service = _service(request)
        folder = getattr(service, "folder", None)
        return B.store_for(Path(folder) / B.FOLDER if folder else None)

    def writer(request: Request, device_id: str) -> tuple[str, str, dict]:
        """(account, the calling phone's id, its row) for a write: only a phone, only its own."""
        principal = _principal(request)
        owner = owner_of(request)
        if not principal.device_id:
            raise HTTPException(status_code=403, detail="Only a linked phone backs up")
        if device_id not in (SELF, principal.device_id):
            raise HTTPException(status_code=403, detail="A phone writes only its own backups")
        return owner, principal.device_id, _linked(request, principal.device_id) or {}

    def reader(request: Request, device_id: str) -> tuple[str, str]:
        owner = owner_of(request)
        if device_id == SELF:
            device_id = _principal(request).device_id or ""
        try:
            B.check_id(device_id, "The device id")
        except B.BackupError as e:
            raise _error(e)
        return owner, device_id

    def key_ok(key: str) -> str:
        try:
            return B.check_id(key, "The theme key")
        except B.BackupError as e:
            raise _error(e)

    def theme_access():
        from sonorium.core import backup_load, theme_edit, theme_metadata as tm
        mgr = getattr(session_manager, "theme_metadata_manager", None)

        def folder_for(theme_id):
            if mgr is not None:
                folder = mgr.get_folder_for_id(theme_id)
                if folder is not None:
                    return folder
            from sonorium.core.theme_package import theme_folders
            from sonorium.paths import audio_dir
            for found_id, folder in theme_folders(audio_dir()):
                if found_id == theme_id:  # that id exactly: no name or folder guessing
                    return folder
            return None

        def metadata_for(folder):
            cached = mgr.get_metadata_by_folder(folder) if mgr is not None else None
            return cached if cached is not None else theme_edit.load(folder)

        def save(theme_id, folder, metadata):
            if mgr is not None and mgr.get_folder_for_id(theme_id) == folder:
                return mgr.save_metadata(theme_id, metadata)
            return tm.save_theme_folder(folder, metadata)

        def preset_saved(theme_id, preset_id):
            follow = getattr(session_manager, "preset_saved", None)
            if callable(follow):
                try:
                    follow({theme_id}, preset_id)
                except Exception as e:
                    logger.warning(f"Preset '{preset_id}' loaded, but playing channels could not follow it: {e}")

        return backup_load.ThemeAccess(folder_for, metadata_for, save, preset_saved)

    def load(request: Request, store, owner: str, only=None) -> list[dict]:
        from sonorium.core import backup_load
        if not can_load(request, owner):
            return []
        rows = _device_rows(request, owner)

        def device_name(device_id, device):
            return ((rows(device_id) or {}).get("name") or device.get("name") or "Phone")[:40]
        return backup_load.load_account(store, owner, theme_access(), device_name, only)

    @router.get("/me/backups")
    async def list_backups(request: Request) -> dict:
        """
        Your backups, by phone (newest first): {"storage": {"used_bytes", "limit_bytes" (null: no limit),
        "warning" (90 % used)}, "settings": {"load_into_themes", "can_load"}, "devices": [{"device_id",
        "name", "platform", "model", "linked" (false: unlinked since), "this_device", "updated",
        "size_bytes", "playlists" (count, null: none), "themes": [{"key", "kind" ("mix" or "theme"),
        "name", "updated", "size_bytes", mix: "presets", "sequences" (counts); theme: "file_count",
        "content_hash"; "uploading" while a whole-theme upload is under way}]}]}. Only you see them.
        """
        owner = owner_of(request)
        store = store_of(request)
        me = _principal(request).device_id
        devices = await run_in_threadpool(store.listing, owner, _device_rows(request, owner))
        for d in devices:
            d["this_device"] = d["device_id"] == me
        settings = store.settings(owner)
        settings["can_load"] = can_load(request, owner)
        return {"storage": await run_in_threadpool(store.storage, owner), "settings": settings, "devices": devices}

    @router.get("/me/backups/settings")
    async def get_backup_settings(request: Request) -> dict:
        """
        Yours: {"load_into_themes": true|false, "can_load"}. With load_into_themes on, the presets and
        sequences your phones back up appear in the matching server themes as ordinary presets, named
        "<name> (<phone>)". can_load: false when your role may not edit themes (nothing is loaded then).
        """
        owner = owner_of(request)
        settings = store_of(request).settings(owner)
        settings["can_load"] = can_load(request, owner)
        return settings

    @router.put("/me/backups/settings")
    async def put_backup_settings(request: Request) -> dict:
        """
        {"load_into_themes": true|false}. Switching it on needs permission to edit themes (403 without)
        and loads every mix you have at once; the answer lists what was loaded ("loaded": [{"theme_id",
        "presets", "sequences"} or {"theme_id", "skipped"}]). Switched off, nothing more is loaded;
        what was loaded stays as ordinary presets.
        """
        owner = owner_of(request)
        body = await _json_capped(request)
        if not isinstance(body, dict) or not isinstance(body.get("load_into_themes"), bool) or set(body) - {"load_into_themes"}:
            raise HTTPException(status_code=400, detail='Expected {"load_into_themes": true or false}')
        on = body["load_into_themes"]
        if on and not can_load(request, owner):
            raise HTTPException(status_code=403, detail="Loading into themes needs permission to edit themes")
        store = store_of(request)
        settings = store.set_load_into_themes(owner, on)
        loaded = await run_in_threadpool(load, request, store, owner) if on else []
        return {**settings, "can_load": can_load(request, owner), "loaded": loaded}

    @router.get("/me/backups/{device_id}/playlists")
    async def get_backup_playlists(device_id: str, request: Request) -> dict:
        """A phone's backed-up playlists, as it sent them: {"playlists", "updated"}. 404: none."""
        owner, device_id = reader(request, device_id)
        doc = store_of(request).playlists(owner, device_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="No playlists backed up from this device")
        return doc

    @router.put("/me/backups/{device_id}/playlists")
    async def put_backup_playlists(device_id: str, request: Request):
        """
        The calling phone backs up its playlists: {"playlists": [...] or {...}} (plain JSON, stored as
        sent; replaces the last one). Answers {"playlists": count, "updated", "storage"}. Only a linked
        phone, only its own ({device_id} = its id or "self"). 507: over your backup space.
        """
        owner, device_id, info = writer(request, device_id)
        body = await _json_capped(request)
        store = store_of(request)
        try:
            result = await run_in_threadpool(store.put_playlists, owner, device_id, body, info)
        except B.BackupError as e:
            return _error_response(e)
        return {**result, "storage": store.storage(owner)}

    @router.get("/me/backups/{device_id}/themes/{key}")
    async def get_backup_theme(device_id: str, key: str, request: Request) -> dict:
        """
        One theme's backup from a phone. A mix: {"key", "kind": "mix", "name", "updated", "mix":
        {"presets", "sequences", "sequence_defaults", "tracks", "groups", "plugins"}} as the phone sent
        it. A whole theme: {"key", "kind": "theme", "name", "updated", "content_hash", "size_bytes",
        "file_count"} (its files: .../package). 404: none.
        """
        owner, device_id = reader(request, device_id)
        doc = store_of(request).theme(owner, device_id, key_ok(key))
        if doc is None or (doc.get("kind") == "theme" and not doc.get("files")):
            raise HTTPException(status_code=404, detail="No backup of this theme from this device")
        if doc.get("kind") == "theme":
            return {k: doc.get(k) for k in ("key", "kind", "name", "updated", "content_hash", "size_bytes")} | {
                "file_count": len(doc.get("files") or [])}
        return {k: doc.get(k) for k in ("key", "kind", "name", "updated", "mix")}

    @router.put("/me/backups/{device_id}/themes/{key}")
    async def put_backup_mix(device_id: str, key: str, request: Request):
        """
        The calling phone backs up its mix of the server theme whose id is {key}: {"name", "presets":
        {id: {"name", "tracks", "groups", "plugins"}}, "sequences": {id: {...}}, "sequence_defaults",
        "tracks": {track key: settings}, "groups", "plugins"} (each optional; the shapes of presets.json
        and metadata.json, docs/THEME_FORMAT.md). Replaces this phone's last backup of the theme; other
        phones' stay. Nothing in the server theme changes, unless you have load_into_themes on: then its
        presets and sequences are loaded ("loaded" in the answer). Only a linked phone, only its own.
        400: not a mix; 413: over 8 MB; 507: over your backup space.
        """
        owner, device_id, info = writer(request, device_id)
        key = key_ok(key)
        body = await _json_capped(request)
        store = store_of(request)
        try:
            summary = await run_in_threadpool(store.put_mix, owner, device_id, key, body, info)
        except B.BackupError as e:
            return _error_response(e)
        loaded = []
        if store.settings(owner).get("load_into_themes"):
            loaded = await run_in_threadpool(load, request, store, owner, (device_id, key))
        return {**summary, "loaded": loaded, "storage": store.storage(owner)}

    @router.delete("/me/backups/{device_id}/themes/{key}")
    async def delete_backup_theme(device_id: str, key: str, request: Request) -> dict:
        """Delete one theme's backup from a phone (presets already loaded into a server theme stay)."""
        owner, device_id = reader(request, device_id)
        store = store_of(request)
        if not await run_in_threadpool(store.delete_theme, owner, device_id, key_ok(key)):
            raise HTTPException(status_code=404, detail="No backup of this theme from this device")
        return {"status": "ok", "storage": store.storage(owner)}

    @router.delete("/me/backups/{device_id}")
    async def delete_backup_device(device_id: str, request: Request) -> dict:
        """Delete every backup of one phone (presets already loaded into server themes stay)."""
        owner, device_id = reader(request, device_id)
        store = store_of(request)
        if not await run_in_threadpool(store.delete_device, owner, device_id):
            raise HTTPException(status_code=404, detail="No backups from this device")
        return {"status": "ok", "storage": store.storage(owner)}

    @router.get("/me/backups/{device_id}/themes/{key}/package")
    async def get_backup_package(device_id: str, key: str, request: Request):
        """
        A whole-theme backup as a theme package, for restoring it: {"key", "name", "updated",
        "content_hash", "size_bytes", "files": [{"path", "size", "sha256"}]}; the same content hash as
        GET /api/themes/{theme_id}/package. Each file: .../files/{path}. 404: none.
        """
        owner, device_id = reader(request, device_id)
        try:
            return store_of(request).package(owner, device_id, key_ok(key))
        except B.BackupError as e:
            raise _error(e)

    @router.put("/me/backups/{device_id}/themes/{key}/package")
    async def put_backup_package(device_id: str, key: str, request: Request):
        """
        The calling phone starts (or resumes) backing up a theme made on it, {key} being the phone's own
        theme id: {"name", "files": [{"path", "size", "sha256"}]}, every file of the theme as a theme
        package lists them (metadata.json and presets.json included). Paths: "/"-separated inside the
        theme, at most 255 characters and 4 levels, no "." or ".." or hidden parts; audio (.mp3 .wav
        .flac .ogg), .json, .md, .txt and images (.png .jpg .jpeg .webp) only. Answers {"complete",
        "content_hash", "missing": [{"path", "size", "sha256", "received"}], "storage"}: send each missing
        file with PUT .../files/{path}, from byte "received". Files the account already has (same
        sha256, from any of its phones) aren't sent again. The last complete backup stays until the new
        one is complete. 400: a bad list; 507: it wouldn't fit in your backup space.
        """
        owner, device_id, info = writer(request, device_id)
        key = key_ok(key)
        body = await _json_capped(request)
        store = store_of(request)
        try:
            status = await run_in_threadpool(store.put_manifest, owner, device_id, key, body, info)
        except B.BackupError as e:
            return _error_response(e)
        return {**status, "storage": store.storage(owner)}

    @router.get("/me/backups/{device_id}/themes/{key}/upload")
    async def get_backup_upload(device_id: str, key: str, request: Request):
        """What the calling phone's whole-theme upload still needs: {"complete", "content_hash", "missing"}."""
        owner, device_id, _ = writer(request, device_id)
        try:
            return store_of(request).upload_status(owner, device_id, key_ok(key))
        except B.BackupError as e:
            raise _error(e)

    @router.put("/me/backups/{device_id}/themes/{key}/files/{path:path}")
    async def put_backup_file(device_id: str, key: str, path: str, request: Request):
        """
        One file of the calling phone's whole-theme upload, by its path in the list: the raw bytes.
        Whole, or from where it stopped: "Content-Range: bytes <first>-<last>/<size>" with <first> the
        "received" count (409 with "received" from anywhere else). A file is checked against its sha256
        when its last byte is in (422: it doesn't match, send it again); once every file is in, the
        theme's backup is complete. Answers {"path", "received", "complete", "missing", "content_hash"}.
        """
        owner, device_id, _ = writer(request, device_id)
        key = key_ok(key)
        store = store_of(request)
        # Looked up in the upload's list only: no path from the request ever reaches the file system
        pending = ((store.theme(owner, device_id, key) or {}).get("pending") or {}).get("files") or []
        entry = next((f for f in pending if f["path"] == path), None)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"No file '{path}' in this upload")
        start, length = _content_range(request.headers.get("content-range"), entry["size"])
        try:
            entry, part = store.begin_file(owner, device_id, key, path, start)
        except B.BackupError as e:
            return _error_response(e)
        written, done = 0, False
        try:
            with open(part, "ab") as out:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    if start + written + len(chunk) > entry["size"] or (length is not None and written + len(chunk) > length):
                        out.close()
                        Path(part).unlink(missing_ok=True)
                        raise HTTPException(status_code=413, detail=f"More bytes than '{path}' has ({entry['size']})")
                    out.write(chunk)
                    written += len(chunk)
            done = True
        finally:
            if not done:
                store.abort_file(entry["sha256"])
        try:
            result = await run_in_threadpool(store.end_file, owner, device_id, key, entry, part)
        except B.BackupError as e:
            return _error_response(e)
        return {**result, "storage": store.storage(owner)}

    @router.get("/me/backups/{device_id}/themes/{key}/files/{path:path}")
    async def get_backup_file(device_id: str, key: str, path: str, request: Request):
        """One file of a whole-theme backup, by its path in the package; Range and ETag (its SHA-256) supported."""
        from sonorium.core import theme_package
        from sonorium.web.theme_package_routes import _etag, _etag_matches, _media_type
        owner, device_id = reader(request, device_id)
        try:
            entry, source = store_of(request).file_of(owner, device_id, key_ok(key), path)
        except B.BackupError as e:
            raise _error(e)
        etag = _etag(entry["sha256"])
        headers = {"ETag": etag, "Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        range_header = request.headers.get("range")
        if_range = request.headers.get("if-range")
        if range_header and if_range and if_range.strip() != etag:
            range_header = None
        try:
            span = theme_package.parse_range(range_header, entry["size"])
        except theme_package.RangeNotSatisfiable:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{entry['size']}"})
        start, end = span if span else (0, entry["size"] - 1)
        length = max(0, end - start + 1)
        if span:
            headers["Content-Range"] = f"bytes {start}-{end}/{entry['size']}"
        headers["Content-Length"] = str(length)
        return StreamingResponse(theme_package.iter_file(source, start, length), status_code=206 if span else 200,
                                 headers=headers, media_type=_media_type(entry["path"]))
