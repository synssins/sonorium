"""
Plugin attributes on Sonorium's objects (core/plugin_data.py has the schema
and the checks): where each object's values live, reading and changing them,
and removing a plugin's values when it is uninstalled.

An object reference is a small dict:

    {"kind": "theme",   "id": theme_id}
    {"kind": "track",   "theme": theme_id, "id": track key ("Rain" or "Birds/Robin")}
    {"kind": "file",    "theme": theme_id, "id": "Group/Name"}  (a file in a group)
    {"kind": "preset",  "theme": theme_id, "id": preset_id}
    {"kind": "channel", "id": channel id}
    {"kind": "speaker", "id": speaker id}
    {"kind": "user",    "id": user id}
    {"kind": "group",   "theme": theme_id, "id": group name}
    {"kind": "sequence", "theme": theme_id, "id": sequence id}
    {"kind": "step",    "theme": theme_id, "id": "<sequence id>/<preset id>"}
    {"kind": "playlist", "id": playlist id}
    {"kind": "playlist_entry", "id": "<playlist id>/<entry id>"}
    {"kind": "speaker_group", "id": group id}
    {"kind": "area" | "key" | "connection", "id": its id}

A plugin uses self.attributes (PluginAttributes): declare(), schema(),
get(ref), set(ref, values), clear(ref). Outside programs use
GET /api/attributes, GET /api/attributes/{kind}?id=..&theme=.. and
PUT /api/attributes/{kind}/{plugin_id}?id=..&theme=.. (web/plugin_framework_api.py).
Every change publishes "attribute.changed".
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from sonorium.core import plugin_data
from sonorium.core.plugin_data import OBJECT_CLASSES, TRACK_CLASSES, PluginDataError
from sonorium.obs import logger
from sonorium.plugins.actions import ActionError


def _classes(kind: str) -> tuple:
    return (kind,)


class _Target:
    """One object's "plugins" block: read it, write it back."""

    def __init__(self, kind: str, ref_id: str, theme: Optional[str], read: Callable[[], dict],
                 write: Callable[[dict], None]):
        self.kind, self.id, self.theme = kind, ref_id, theme
        self.read, self.write = read, write


class AttributeService:
    """
    state_store: the StateStore (channels, speaker settings)
    session_manager: the SessionManager (its theme_metadata_manager; its registry for speakers)
    auth: the AuthService (accounts, for users), optional
    publish: the event bus's publish, optional
    """

    def __init__(self, state_store=None, session_manager=None, auth=None,
                 publish: Optional[Callable] = None, registry=None):
        self.state_store = state_store
        self.session_manager = session_manager
        self.auth = auth
        self.publish = publish
        self.registry = registry or plugin_data.registry

    # --- where each kind lives -----------------------------------------------------

    def _themes(self):
        manager = getattr(self.session_manager, "theme_metadata_manager", None)
        if manager is None:
            raise ActionError("unavailable", "Themes are not available")
        return manager

    def _theme(self, theme_id):
        if not isinstance(theme_id, str) or not theme_id:
            raise ActionError("invalid", "This object needs its theme ('theme')")
        manager = self._themes()
        metadata = manager.get_metadata(theme_id)
        if metadata is None:
            raise ActionError("not_found", f"Theme not found: {theme_id}")
        return manager, metadata

    def _save_theme(self, manager, metadata) -> None:
        if not manager.save_metadata(metadata.id, metadata):
            raise ActionError("unavailable", "The theme's files couldn't be saved")

    def _accounts(self):
        state = getattr(self.auth, "plugin_state", None) or {}
        accounts = state.get("users") if isinstance(state, dict) else None
        if accounts is None:
            raise ActionError("unavailable", "Users are not available")
        return accounts

    def _session(self, channel_id):
        store = self.state_store
        if store is None:
            raise ActionError("unavailable", "Channels are not available")
        # (a personal channel is never addressed as a channel)
        session = store.sessions.get(channel_id)
        if session is None or getattr(session, "owner", None):
            raise ActionError("not_found", f"Channel not found: {channel_id}")
        return session

    def target(self, ref: Any) -> _Target:
        """The object a reference names. ActionError invalid/not_found/unavailable."""
        if not isinstance(ref, dict):
            raise ActionError("invalid", "An object reference is {'kind', 'id'} (and 'theme' for theme objects)")
        kind, ref_id, theme = ref.get("kind"), ref.get("id"), ref.get("theme")
        if kind not in OBJECT_CLASSES:
            raise ActionError("invalid", f"Unknown object kind {kind!r} (one of {', '.join(OBJECT_CLASSES)})")
        if kind == "theme":
            ref_id = ref_id or theme
        if not isinstance(ref_id, str) or not ref_id:
            raise ActionError("invalid", "The object reference needs its 'id'")

        if kind == "theme":
            manager, metadata = self._theme(ref_id)

            def write(block):
                metadata.plugins = block
                self._save_theme(manager, metadata)
            return _Target(kind, ref_id, ref_id, lambda: metadata.plugins, write)

        if kind in TRACK_CLASSES:
            manager, metadata = self._theme(theme)
            if kind == "file" and "/" not in ref_id:
                raise ActionError("invalid", "A file is a track in a group: its id is 'Group/Name'")
            settings = (metadata.tracks or {}).get(ref_id)
            if settings is None:
                raise ActionError("not_found", f"No track '{ref_id}' in theme {theme}")

            def write(block):
                settings.plugins = block
                self._save_theme(manager, metadata)
            return _Target(kind, ref_id, theme, lambda: settings.plugins, write)

        if kind == "preset":
            manager, metadata = self._theme(theme)
            preset = (metadata.presets or {}).get(ref_id)
            if not isinstance(preset, dict):
                raise ActionError("not_found", f"No preset '{ref_id}' in theme {theme}")

            def write(block):
                if block:
                    preset["plugins"] = block
                else:
                    preset.pop("plugins", None)
                self._save_theme(manager, metadata)
            return _Target(kind, ref_id, theme, lambda: preset.get("plugins") or {}, write)

        if kind == "group":
            manager, metadata = self._theme(theme)
            settings = (metadata.groups or {}).get(ref_id)
            if not isinstance(settings, dict):
                raise ActionError("not_found", f"No group '{ref_id}' in theme {theme}")

            def write(block):
                if block:
                    settings["plugins"] = block
                else:
                    settings.pop("plugins", None)
                self._save_theme(manager, metadata)
            return _Target(kind, ref_id, theme, lambda: settings.get("plugins") or {}, write)

        if kind in ("sequence", "step"):
            from sonorium.core import sequences as seq_mod
            manager, metadata = self._theme(theme)
            folder = manager.get_folder_for_id(theme) if hasattr(manager, "get_folder_for_id") else None
            if folder is None:
                raise ActionError("not_found", f"Theme not found: {theme}")
            all_sequences = seq_mod.load_sequences(folder)
            sequence_id, _, preset_id = ref_id.partition("/") if kind == "step" else (ref_id, "", "")
            sequence = all_sequences.get(sequence_id)
            if sequence is None:
                raise ActionError("not_found", f"No sequence '{sequence_id}' in theme {theme}")
            if kind == "step" and preset_id not in (sequence.get("presets") or []):
                raise ActionError("not_found", f"No step '{preset_id}' in sequence '{sequence_id}'")

            def save():
                try:
                    seq_mod.save_sequences(folder, all_sequences)
                except OSError as e:
                    raise ActionError("unavailable", f"The theme's files couldn't be saved: {e}")

            if kind == "sequence":
                def write(block):
                    if block:
                        sequence["plugins"] = block
                    else:
                        sequence.pop("plugins", None)
                    save()
                return _Target(kind, ref_id, theme, lambda: sequence.get("plugins") or {}, write)

            def write_step(block):
                steps = dict(sequence.get("step_plugins") or {})
                if block:
                    steps[preset_id] = block
                else:
                    steps.pop(preset_id, None)
                if steps:
                    sequence["step_plugins"] = steps
                else:
                    sequence.pop("step_plugins", None)
                save()
            return _Target(kind, ref_id, theme, lambda: (sequence.get("step_plugins") or {}).get(preset_id) or {},
                           write_step)

        if kind in ("playlist", "playlist_entry"):
            if self.state_store is None:
                raise ActionError("unavailable", "Playlists are not available")
            playlist_id, _, index = ref_id.partition("/") if kind == "playlist_entry" else (ref_id, "", "")
            playlist = (self.state_store.playlists or {}).get(playlist_id)
            if not isinstance(playlist, dict):
                raise ActionError("not_found", f"Playlist not found: {playlist_id}")
            holder = playlist
            if kind == "playlist_entry":
                holder = next((e for e in playlist.get("entries") or []
                               if isinstance(e, dict) and e.get("id") == index), None)
                if holder is None:
                    raise ActionError("not_found", f"No entry '{index}' in playlist {playlist_id}")

            def write(block):
                if block:
                    holder["plugins"] = block
                else:
                    holder.pop("plugins", None)
                self.state_store.save()
            return _Target(kind, ref_id, None, lambda: holder.get("plugins") or {}, write)

        if kind == "speaker_group":
            group = (self.state_store.speaker_groups if self.state_store is not None else {}).get(ref_id)
            if group is None:
                raise ActionError("not_found", f"Speaker group not found: {ref_id}")

            def write(block):
                group.plugins = block or {}
                self.state_store.save()
            return _Target(kind, ref_id, None, lambda: group.plugins or {}, write)

        if kind in plugin_data.SIDE_TABLE_CLASSES:
            if self.state_store is None:
                raise ActionError("unavailable", "Settings are not available")
            table = self.state_store.settings.object_plugins

            def write(block):
                objects = table.setdefault(kind, {})
                if block:
                    objects[ref_id] = block
                else:
                    objects.pop(ref_id, None)
                if not objects:
                    table.pop(kind, None)
                self.state_store.save()
            return _Target(kind, ref_id, None, lambda: (table.get(kind) or {}).get(ref_id) or {}, write)

        if kind == "channel":
            session = self._session(ref_id)

            def write(block):
                session.plugins = block
                self.state_store.save()
            return _Target(kind, ref_id, None, lambda: session.plugins, write)

        if kind == "speaker":
            if self.state_store is None:
                raise ActionError("unavailable", "Speakers are not available")
            speakers = getattr(self.session_manager, "registry", None)
            get_speaker = getattr(speakers, "get_speaker", None)
            if callable(get_speaker) and get_speaker(ref_id) is None:
                raise ActionError("not_found", f"Speaker not found: {ref_id}")
            all_settings = self.state_store.settings.speaker_settings

            def write(block):
                settings = dict(all_settings.get(ref_id) or {})
                if block:
                    settings["plugins"] = block
                else:
                    settings.pop("plugins", None)
                if settings:
                    all_settings[ref_id] = settings
                else:
                    all_settings.pop(ref_id, None)
                self.state_store.save()
            return _Target(kind, ref_id, None, lambda: (all_settings.get(ref_id) or {}).get("plugins") or {}, write)

        # user
        accounts = self._accounts()
        user = accounts.users.get(ref_id)
        if not isinstance(user, dict):
            raise ActionError("not_found", f"User not found: {ref_id}")

        def write(block):
            if block:
                user["plugins"] = block
            else:
                user.pop("plugins", None)
            accounts.save()
        return _Target(kind, ref_id, None, lambda: user.get("plugins") or {}, write)

    # --- reading and changing --------------------------------------------------------

    def get(self, ref: Any, plugin_id: Optional[str] = None) -> dict:
        """
        {plugin_id: {name: value}}: each installed plugin's values on the object
        (defaults filled in, values that no longer fit left out). With
        plugin_id, that plugin's values only ({name: value}).
        """
        target = self.target(ref)
        block = target.read() or {}
        classes = _classes(target.kind)
        if plugin_id is not None:
            return self.registry.values(block, plugin_id, classes)
        result = {}
        for pid, decls in self.registry.declarations().items():
            if any(cls in decls for cls in classes):
                result[pid] = self.registry.values(block, pid, classes)
        return result

    def set(self, ref: Any, plugin_id: str, values: Any, *, origin: Optional[str] = None) -> dict:
        """
        Change a plugin's values on an object: {name: value, or None to remove
        it} (None for values: remove all of them). Checked against the schema
        and the size limit (ActionError "invalid" with every problem). Returns
        the plugin's values afterwards.
        """
        target = self.target(ref)
        before = target.read() or {}
        try:
            block = self.registry.update_block(before, plugin_id, target.kind, values)
        except PluginDataError as e:
            raise ActionError("invalid", str(e))
        if block != before:
            target.write(block)
            keys = sorted(values) if isinstance(values, dict) else None
            self._changed(target, plugin_id, keys, origin)
        return self.registry.values(block, plugin_id, _classes(target.kind))

    def _changed(self, target: _Target, plugin_id: str, keys, origin: Optional[str]) -> None:
        if self.publish is None:
            return
        data = {"kind": target.kind, "id": target.id, "plugin_id": plugin_id, "keys": keys}
        if target.theme is not None and target.kind != "theme":
            data["theme_id"] = target.theme
        try:
            self.publish("attribute.changed", data, origin=origin)
        except Exception as e:
            logger.debug(f"attribute.changed not published: {e}")

    # --- uninstall --------------------------------------------------------------------

    def forget_plugin(self, plugin_id: str) -> int:
        """
        The plugin was uninstalled: its schema goes, and its values are removed
        from every object that keeps them (themes and their tracks, files,
        presets, groups, sequences and steps; channels, speakers, users,
        playlists and their entries, speaker groups, areas, keys, connections
        and plugins). Returns how many objects changed.
        """
        self.registry.forget(plugin_id)
        changed = 0
        manager = getattr(self.session_manager, "theme_metadata_manager", None)
        for metadata in list((getattr(manager, "_metadata_cache", None) or {}).values()):
            touched = bool(metadata.plugins.pop(plugin_id, None) is not None)
            for settings in (metadata.groups or {}).values():
                plugins = settings.get("plugins") if isinstance(settings, dict) else None
                if isinstance(plugins, dict) and plugins.pop(plugin_id, None) is not None:
                    touched = True
                    if not plugins:
                        del settings["plugins"]
            for settings in (metadata.tracks or {}).values():
                plugins = getattr(settings, "plugins", None)
                if plugins and plugins.pop(plugin_id, None) is not None:
                    touched = True
            for preset in (metadata.presets or {}).values():
                plugins = preset.get("plugins") if isinstance(preset, dict) else None
                if isinstance(plugins, dict) and plugins.pop(plugin_id, None) is not None:
                    touched = True
                    if not plugins:
                        del preset["plugins"]
            if touched:
                changed += 1
                if not manager.save_metadata(metadata.id, metadata):
                    logger.warning(f"Plugin {plugin_id} removed, but theme '{metadata.name}' couldn't be saved "
                                   f"without its values (they go the next time it loads)")
            # after the theme's own save, which writes presets.json too
            changed += self._forget_in_sequences(manager, metadata, plugin_id)
        store = self.state_store
        if store is not None:
            touched_state = False
            sessions = list((store.sessions or {}).values()) + list((store.personal_sessions or {}).values())
            for session in sessions:
                if isinstance(getattr(session, "plugins", None), dict) and session.plugins.pop(plugin_id, None) is not None:
                    touched_state = True
                    changed += 1
            for holder in self._state_holders(store):
                plugins = holder.get("plugins") if isinstance(holder, dict) else getattr(holder, "plugins", None)
                if isinstance(plugins, dict) and plugins.pop(plugin_id, None) is not None:
                    touched_state = True
                    changed += 1
                    if not plugins and isinstance(holder, dict):
                        del holder["plugins"]
            if (store.settings.object_plugins.get("plugin") or {}).pop(plugin_id, None) is not None:
                touched_state = True  # values other plugins kept on this plugin's row
            for kind, objects in list((store.settings.object_plugins or {}).items()):
                for object_id, block in list(objects.items()):
                    if isinstance(block, dict) and block.pop(plugin_id, None) is not None:
                        touched_state = True
                        changed += 1
                        if not block:
                            del objects[object_id]
                if not objects:
                    del store.settings.object_plugins[kind]
            all_settings = store.settings.speaker_settings
            for speaker_id, settings in list(all_settings.items()):
                plugins = (settings or {}).get("plugins")
                if isinstance(plugins, dict) and plugins.pop(plugin_id, None) is not None:
                    touched_state = True
                    changed += 1
                    if not plugins:
                        del settings["plugins"]
                    if not settings:
                        del all_settings[speaker_id]
            if touched_state:
                store.save()
        try:
            accounts = self._accounts()
        except ActionError:
            accounts = None
        if accounts is not None:
            touched_users = False
            for user in accounts.users.values():
                plugins = user.get("plugins") if isinstance(user, dict) else None
                if isinstance(plugins, dict) and plugins.pop(plugin_id, None) is not None:
                    touched_users = True
                    changed += 1
                    if not plugins:
                        del user["plugins"]
            if touched_users:
                accounts.save()
        if changed:
            logger.info(f"Plugin {plugin_id} uninstalled: its attributes were removed from {changed} object(s)")
        return changed

    @staticmethod
    def _state_holders(store) -> list:
        """Playlists, their entries and speaker groups: what in state.json carries a "plugins" block."""
        holders = list((getattr(store, "speaker_groups", None) or {}).values())
        for playlist in (getattr(store, "playlists", None) or {}).values():
            if isinstance(playlist, dict):
                holders.append(playlist)
                holders.extend(e for e in playlist.get("entries") or [] if isinstance(e, dict))
        return holders

    @staticmethod
    def _forget_in_sequences(manager, metadata, plugin_id: str) -> int:
        """A theme's sequences and steps without the plugin's values (presets.json). Returns 1 if it changed."""
        from sonorium.core import sequences as seq_mod
        folder = manager.get_folder_for_id(metadata.id) if hasattr(manager, "get_folder_for_id") else None
        if folder is None:
            return 0
        try:  # the file as it is: a load would already leave out an uninstalled plugin's values
            doc = seq_mod._doc(folder)
        except Exception:
            return 0
        all_sequences = doc.get("sequences") if isinstance(doc.get("sequences"), dict) else {}
        touched = False
        for sequence in all_sequences.values():
            if not isinstance(sequence, dict):
                continue
            if isinstance(sequence.get("plugins"), dict) and sequence["plugins"].pop(plugin_id, None) is not None:
                touched = True
                if not sequence["plugins"]:
                    del sequence["plugins"]
            steps = sequence.get("step_plugins") if isinstance(sequence.get("step_plugins"), dict) else {}
            for preset_id, block in list(steps.items()):
                if isinstance(block, dict) and block.pop(plugin_id, None) is not None:
                    touched = True
                    if not block:
                        del sequence["step_plugins"][preset_id]
            if "step_plugins" in sequence and not sequence["step_plugins"]:
                del sequence["step_plugins"]
        if touched:
            try:
                seq_mod.write_json_atomic(folder / seq_mod.PRESETS_FILE, doc)
            except OSError as e:
                logger.warning(f"Plugin {plugin_id} removed, but theme '{metadata.name}' sequences couldn't be saved: {e}")
                return 0
        return 1 if touched else 0

    def for_plugin(self, plugin_id: str) -> "PluginAttributes":
        return PluginAttributes(self, plugin_id)


class PluginAttributes:
    """A plugin's own attributes (self.attributes): only under its own id."""

    def __init__(self, service: AttributeService, plugin_id: str):
        self._service = service
        self.plugin_id = plugin_id

    def declare(self, attributes: dict) -> dict:
        """The plugin's whole schema ({class: {name: field}}), replacing what it declared before. Returns it as kept."""
        return self._service.registry.declare(self.plugin_id, attributes)

    def schema(self) -> dict:
        return self._service.registry.declarations(self.plugin_id)

    def get(self, ref: dict) -> dict:
        """The plugin's values on an object, defaults filled in."""
        return self._service.get(ref, self.plugin_id)

    def set(self, ref: dict, values: dict) -> dict:
        """Change values ({name: value or None}); ActionError if they don't fit. Returns the values afterwards."""
        return self._service.set(ref, self.plugin_id, values, origin=f"plugin:{self.plugin_id}")

    def clear(self, ref: dict) -> dict:
        """Remove all of the plugin's values from an object."""
        return self._service.set(ref, self.plugin_id, None, origin=f"plugin:{self.plugin_id}")
