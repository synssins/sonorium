"""
What each caller may do, and the permission every route needs.

Abilities are the boxes on a key (Settings > Keys), grouped by area exactly
as the Keys dialog shows them. Roles are sets of abilities plus the
admin-only permissions no key can have:

    admin.users       Users & devices, Require login
    admin.keys        Keys (and the plugin framework's old token endpoints)
    admin.plugins     install, configure and remove plugins, schedules, notifications
    admin.connection  Settings > Connection (Home Assistant and MQTT credentials)
    self              link your own phone, sign out your own browsers and phones
    device            only a linked phone: ask for a one-time sign-in code

Every route is listed in ROUTES with one of:

    public      no sign-in at all (streams, the test tone, icons, sign-in endpoints)
    page        a web page shell (its data comes from the API, which is checked);
                not served on the direct ports when web_ui_only_through_ha is on
    <ability>   e.g. "ch.play"; "a|b" means either one is enough
    +legacy     the route also takes the plugin framework's own token (triggers,
                event stream), checked by the route itself

tests/test_auth_routes_coverage.py fails when a route has no entry here.
"""
from __future__ import annotations

# Areas and their abilities, in the order and wording of the Keys dialog.
AREAS = [
    ("Channels", "Channels", [
        ("ch.view", "View", "view"),
        ("ch.play", "Play and stop", "play and stop"),
        ("ch.change", "Change theme, preset and volume", "change"),
        ("ch.speakers", "Change speakers", "change speakers"),
        ("ch.add", "Add and remove", "add and remove"),
    ]),
    ("Themes and presets", "Themes", [
        ("th.view", "View", "view"),
        ("th.edit", "Edit", "edit"),
        ("th.upload", "Upload and delete", "upload and delete"),
    ]),
    ("Playlists and sequences", "Playlists", [
        ("pl.view", "View", "view"),
        ("pl.run", "Run", "run"),
        ("pl.edit", "Edit", "edit"),
    ]),
    ("Speakers", "Speakers", [
        ("sp.view", "View", "view"),
        ("sp.mute", "Mute and volume", "mute and volume"),
        ("sp.set", "Change settings", "change settings"),
    ]),
    ("Triggers", None, [("tr.run", "Run triggers", "Run triggers")]),
    ("Events", None, [("ev.read", "Read events", "Read events")]),
    ("Plugins", None, [("pg.use", "Use plugin pages and buttons", "Use plugin pages and buttons")]),
    ("Settings", "Settings", [
        ("st.view", "View", "view"),
        ("st.change", "Change", "change"),
    ]),
]

ABILITIES = frozenset(a for _, _, items in AREAS for a, _, _ in items)
ADMIN_ONLY = frozenset({"admin.users", "admin.keys", "admin.plugins", "admin.connection"})
SELF = "self"
DEVICE = "device"

ROLES = ("admin", "user")
ROLE_LABELS = {"admin": "Admin", "user": "User"}

ADMIN_PERMS = frozenset(ABILITIES | ADMIN_ONLY | {SELF})
USER_PERMS = frozenset({
    "ch.view", "ch.play", "ch.change", "ch.speakers", "ch.add",
    "th.view",
    "pl.view", "pl.run", "pl.edit",
    "sp.view", "sp.mute",
    "ev.read",
    SELF,
})
ROLE_PERMS = {"admin": ADMIN_PERMS, "user": USER_PERMS}

# A linked phone acts as its user, but never manages users, devices or keys
DEVICE_EXCLUDED = frozenset({"admin.users", "admin.keys", SELF})
# Nobody signed in while Require login is off: today's open access, except
# managing users and keys, which needs a sign-in once there are accounts
OPEN_EXCLUDED = frozenset({"admin.users", "admin.keys", SELF})

# Keys whose ability lists ever contained these are cleaned on load
NEVER_FOR_KEYS = ADMIN_ONLY | {SELF, DEVICE}


def role_perms(role: str) -> frozenset:
    return ROLE_PERMS.get(role, frozenset())


def device_perms(role: str) -> frozenset:
    return frozenset((role_perms(role) - DEVICE_EXCLUDED) | {DEVICE})


def clean_abilities(values) -> list[str]:
    """Only real key abilities, in dialog order."""
    wanted = set(values or [])
    return [a for _, _, items in AREAS for a, _, _ in items if a in wanted]


def ability_chips(abilities) -> list[str]:
    """The Keys list's "Allowed" chips: "Channels: view, play and stop", "Read events", ..."""
    have = set(abilities or [])
    chips = []
    for _, short, items in AREAS:
        on = [chip for a, _, chip in items if a in have]
        if not on:
            continue
        chips.extend(on if short is None else [f"{short}: {', '.join(on)}"])
    return chips


def catalogue() -> list[dict]:
    """The areas and boxes for the Keys dialog."""
    return [{"area": name, "abilities": [{"id": a, "label": label} for a, label, _ in items]}
            for name, _, items in AREAS]


# ---------------------------------------------------------------------------
# Every route: "METHOD path permission". HEAD uses the GET entry.
# ---------------------------------------------------------------------------
_ROUTES = """
GET     /                                               page
GET     /v1                                             st.view
GET     /logs                                           page
GET     /docs                                           page
GET     /docs/oauth2-redirect                           page
GET     /redoc                                          page
GET     /openapi.json                                   ch.view
GET     /logo.png                                       public
GET     /display.png                                    public
GET     /favicon.png                                    public
GET     /stream/channel{channel_id:int}                 public
GET     /stream/{id}                                    public
GET     /api/test-tone.mp3                              public

GET     /api/info                                       public
GET     /api/auth/me                                    public
POST    /api/auth/setup                                 public
POST    /api/auth/login                                 public
POST    /api/auth/logout                                public
POST    /api/auth/code-login                            public
POST    /api/auth/new-password                          public
PUT     /api/auth/require-login                         admin.users
POST    /api/pair                                       public
POST    /api/pair/login                                 public
POST    /api/link                                       self
POST    /api/devices/recovery-code                      device
DELETE  /api/devices/{device_id}                        self
GET     /api/users                                      admin.users
POST    /api/users                                      admin.users
PUT     /api/users/{user_id}                            admin.users
POST    /api/users/{user_id}/password                   admin.users
POST    /api/users/{user_id}/sign-out                   admin.users
DELETE  /api/users/{user_id}                            admin.users
GET     /api/keys                                       admin.keys
POST    /api/keys                                       admin.keys
PUT     /api/keys/{key_id}                              admin.keys
DELETE  /api/keys/{key_id}                              admin.keys
GET     /api/stream/url                                 ch.view

GET     /api/connection                                 admin.connection
PUT     /api/connection                                 admin.connection
DELETE  /api/connection/ha                              admin.connection
DELETE  /api/connection/mqtt                            admin.connection
GET     /api/ha-cards                                   st.view
GET     /api/ha-cards/download                          st.view
GET     /api/logs                                       st.view
GET     /api/logs/download                              st.view
GET     /api/install                                    ch.view
PUT     /api/install/features                           st.change
GET     /api/status                                     ch.view

GET     /api/themes                                     th.view
POST    /api/themes/refresh                             th.view
POST    /api/themes/{theme_id}/favorite                 th.edit
GET     /api/themes/{theme_id}                          th.view
GET     /api/themes/{theme_id}/tracks                   th.view
GET     /api/themes/{theme_id}/tracks/{track_name:path}/audio           th.view
PUT     /api/themes/{theme_id}/tracks/{track_name:path}/presence        th.edit
PUT     /api/themes/{theme_id}/tracks/{track_name:path}/muted           th.edit
PUT     /api/themes/{theme_id}/tracks/{track_name:path}/volume          th.edit
PUT     /api/themes/{theme_id}/tracks/{track_name:path}/playback_mode   th.edit
PUT     /api/themes/{theme_id}/tracks/{track_name:path}/seamless_loop   th.edit
PUT     /api/themes/{theme_id}/tracks/{track_name:path}/fades           th.edit
PUT     /api/themes/{theme_id}/tracks/{track_name:path}/ebb             th.edit
PUT     /api/themes/{theme_id}/tracks/{track_name:path}/exclusive       th.edit
GET     /api/themes/{theme_id}/groups                   th.view
PUT     /api/themes/{theme_id}/groups/{group}           th.edit
POST    /api/themes/{theme_id}/groups                   th.edit
POST    /api/themes/{theme_id}/groups/{group}/rename    th.edit
DELETE  /api/themes/{theme_id}/groups/{group}           th.edit
POST    /api/themes/{theme_id}/tracks/{track_name:path}/move            th.edit
POST    /api/themes/{theme_id}/tracks/reset             th.edit
GET     /api/themes/{theme_id}/links/source/{source_id} th.view
POST    /api/themes/{theme_id}/links                    th.edit
POST    /api/themes/{theme_id}/links/{key:path}/copy-in th.edit
DELETE  /api/themes/{theme_id}/links/{key:path}         th.edit
PUT     /api/themes/{theme_id}/rename                   th.edit
GET     /api/themes/{theme_id}/presets                  th.view
POST    /api/themes/{theme_id}/presets                  th.edit
POST    /api/themes/{theme_id}/presets/{preset_id}/load th.edit
GET     /api/themes/{theme_id}/presets/{preset_id}      th.view
PUT     /api/themes/{theme_id}/presets/{preset_id}      th.edit
DELETE  /api/themes/{theme_id}/presets/{preset_id}      th.edit
PUT     /api/themes/{theme_id}/presets/{preset_id}/default  th.edit
PUT     /api/themes/{theme_id}/presets/{preset_id}/rename   th.edit
POST    /api/themes/{theme_id}/presets/import           th.edit
GET     /api/themes/{theme_id}/presets/{preset_id}/export   th.view
GET     /api/categories                                 th.view
POST    /api/categories                                 th.edit
DELETE  /api/categories/{category_name}                 th.edit
POST    /api/themes/{theme_id}/categories               th.edit
POST    /api/themes/create                              th.upload
POST    /api/themes/{theme_id}/upload                   th.upload
PUT     /api/themes/{theme_id}/metadata                 th.edit
DELETE  /api/themes/{theme_id}                          th.upload
GET     /api/themes/{theme_id}/export                   th.view
POST    /api/themes/import                              th.upload

GET     /api/channels                                   ch.view
GET     /api/channels/{channel_id}                      ch.view
POST    /api/channels/{channel_id}/play                 ch.play
POST    /api/channels/{channel_id}/stop                 ch.play
POST    /api/channels/{channel_id}/volume               ch.change

GET     /api/sessions                                   ch.view
POST    /api/sessions                                   ch.add
GET     /api/sessions/{session_id}                      ch.view
PUT     /api/sessions/{session_id}                      ch.change
DELETE  /api/sessions/{session_id}                      ch.add
POST    /api/sessions/{session_id}/play                 ch.play
POST    /api/sessions/{session_id}/pause                ch.play
POST    /api/sessions/{session_id}/stop                 ch.play
POST    /api/sessions/{session_id}/volume               ch.change
PUT     /api/sessions/{session_id}/speakers-muted       ch.change|sp.mute
POST    /api/sessions/{session_id}/listen               ch.play
DELETE  /api/sessions/{session_id}/listen               ch.play
POST    /api/sessions/stop-all                          ch.play
GET     /api/sessions/{session_id}/playback             ch.view
POST    /api/sessions/{session_id}/sequence             pl.run
DELETE  /api/sessions/{session_id}/sequence             pl.run
PUT     /api/sessions/{session_id}/playlist             pl.run
DELETE  /api/sessions/{session_id}/playlist             pl.run
POST    /api/sessions/{session_id}/next                 pl.run

GET     /api/themes/{theme_id}/sequences                pl.view
POST    /api/themes/{theme_id}/sequences                pl.edit
GET     /api/themes/{theme_id}/sequences/{sequence_id}  pl.view
PUT     /api/themes/{theme_id}/sequences/{sequence_id}  pl.edit
DELETE  /api/themes/{theme_id}/sequences/{sequence_id}  pl.edit
GET     /api/themes/{theme_id}/sequence_defaults        pl.view
PUT     /api/themes/{theme_id}/sequence_defaults        pl.edit
GET     /api/themes/{theme_id}/sequences/{sequence_id}/transitions/{preset_id}  pl.view
PUT     /api/themes/{theme_id}/sequences/{sequence_id}/transitions/{preset_id}  pl.edit
GET     /api/themes/{theme_id}/sequences/{sequence_id}/while/{preset_id}        pl.view
PUT     /api/themes/{theme_id}/sequences/{sequence_id}/while/{preset_id}        pl.edit
GET     /api/themes/{theme_id}/transition_tracks        pl.view
GET     /api/playlists                                  pl.view
POST    /api/playlists                                  pl.edit
GET     /api/playlists/{playlist_id}                    pl.view
PUT     /api/playlists/{playlist_id}                    pl.edit
DELETE  /api/playlists/{playlist_id}                    pl.edit

GET     /api/groups                                     sp.view
POST    /api/groups                                     sp.set
GET     /api/groups/{group_id}                          sp.view
PUT     /api/groups/{group_id}                          sp.set
DELETE  /api/groups/{group_id}                          sp.set
GET     /api/groups/{group_id}/resolve                  sp.view
GET     /api/speakers                                   sp.view
GET     /api/speakers/hierarchy                         sp.view
POST    /api/speakers/refresh                           sp.view
GET     /api/network-speakers                           sp.view
POST    /api/network-speakers/refresh                   sp.view
GET     /api/speakers/{speaker_id}/settings             sp.view
PUT     /api/speakers/{speaker_id}/settings             sp.set
POST    /api/speakers/{speaker_id}/test                 sp.set
POST    /api/speakers/manual/check                      sp.set
POST    /api/speakers/manual                            sp.set
DELETE  /api/speakers/manual/{speaker_id}               sp.set
POST    /api/speakers/resolve                           sp.view
GET     /api/debug/speakers                             st.view
GET     /api/spaces                                     sp.view
POST    /api/spaces/floors                              st.change
POST    /api/spaces/areas                               st.change
PUT     /api/spaces/floors/{floor_id}                   st.change
PUT     /api/spaces/areas/{area_id}                     st.change
DELETE  /api/spaces/floors/{floor_id}                   st.change
DELETE  /api/spaces/areas/{area_id}                     st.change

GET     /api/settings                                   ch.view
PUT     /api/settings                                   st.change
GET     /api/settings/playback-values                   ch.view
PUT     /api/settings/playback-values                   st.change
GET     /api/settings/speakers                          sp.view
PUT     /api/settings/speakers                          sp.set
POST    /api/settings/speakers/enable                   sp.set
POST    /api/settings/speakers/disable                  sp.set
POST    /api/settings/speakers/enable-all               sp.set
POST    /api/settings/speakers/disable-all              sp.set
GET     /api/settings/speaker-areas                     sp.view
PUT     /api/settings/speaker-areas                     sp.set
POST    /api/settings/speaker-areas/create              sp.set
PUT     /api/settings/speaker-areas/{area_name}         sp.set
DELETE  /api/settings/speaker-areas/{area_name}         sp.set
POST    /api/settings/speaker-areas/{area_name}/add-speaker     sp.set
POST    /api/settings/speaker-areas/{area_name}/remove-speaker  sp.set

GET     /api/plugins                                    pg.use|admin.plugins
GET     /api/plugins/catalog                            admin.plugins
POST    /api/plugins/install-from-catalog               admin.plugins
GET     /api/plugins/{plugin_id}                        pg.use|admin.plugins
PUT     /api/plugins/{plugin_id}/enable                 admin.plugins
PUT     /api/plugins/{plugin_id}/disable                admin.plugins
GET     /api/plugins/{plugin_id}/settings               admin.plugins
PUT     /api/plugins/{plugin_id}/settings               admin.plugins
POST    /api/plugins/{plugin_id}/action                 pg.use
POST    /api/plugins/reload                             admin.plugins
POST    /api/plugins/upload                             admin.plugins
DELETE  /api/plugins/{plugin_id}                        admin.plugins

GET     /api/events/catalogue                           ev.read
GET     /api/events/token                               admin.keys
POST    /api/events/token                               admin.keys
DELETE  /api/events/token                               admin.keys
GET     /api/events/stream                              ev.read+legacy
GET     /api/plugins/{plugin_id}/triggers               admin.plugins
POST    /api/plugins/{plugin_id}/triggers/token         admin.keys
DELETE  /api/plugins/{plugin_id}/triggers/token         admin.keys
GET     /api/triggers/{plugin_id}/{trigger}             tr.run+legacy
POST    /api/triggers/{plugin_id}/{trigger}             tr.run+legacy
GET     /api/schedules                                  admin.plugins
PUT     /api/schedules/settings                         admin.plugins
GET     /api/plugins/{plugin_id}/schedules              admin.plugins
POST    /api/plugins/{plugin_id}/schedules              admin.plugins
DELETE  /api/plugins/{plugin_id}/schedules/{job_id}     admin.plugins
PUT     /api/plugins/{plugin_id}/schedules/{job_id}/enabled     admin.plugins
GET     /api/notifications                              admin.plugins
PUT     /api/notifications/rules                        admin.plugins
PUT     /api/notifications/rules/{rule_id}              admin.plugins
DELETE  /api/notifications/rules/{rule_id}              admin.plugins
POST    /api/notifications/test                         admin.plugins
GET     /api/extensions                                 pg.use
GET     /api/plugins/{plugin_id}/pages/{page_id}        pg.use
POST    /api/plugins/{plugin_id}/pages/{page_id}/actions/{action}   pg.use
GET     /api/plugins/{plugin_id}/connections/{connection_id}        admin.plugins
PUT     /api/plugins/{plugin_id}/connections/{connection_id}        admin.plugins
POST    /api/plugins/{plugin_id}/connections/{connection_id}/test   admin.plugins
"""


class RoutePermission:
    __slots__ = ("any_of", "public", "page", "legacy", "raw")

    def __init__(self, raw: str):
        self.raw = raw
        self.legacy = raw.endswith("+legacy")
        base = raw[:-len("+legacy")] if self.legacy else raw
        self.public = base == "public"
        self.page = base == "page"
        self.any_of = frozenset() if (self.public or self.page) else frozenset(base.split("|"))

    def allows(self, perms) -> bool:
        return self.public or self.page or bool(self.any_of & perms)

    def __repr__(self):
        return f"RoutePermission({self.raw!r})"


def _parse(text: str) -> dict:
    table = {}
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        method, path, perm = line.split()
        key = (method, path)
        if key in table:
            raise ValueError(f"Route listed twice: {method} {path}")
        table[key] = RoutePermission(perm)
    return table


ROUTES: dict[tuple[str, str], RoutePermission] = _parse(_ROUTES)
KNOWN = ABILITIES | ADMIN_ONLY | {SELF, DEVICE}
for _p in ROUTES.values():
    unknown = _p.any_of - KNOWN
    if unknown:
        raise ValueError(f"Unknown permission {unknown} in the route table")


def _compile(table: dict) -> list:
    from starlette.routing import compile_path
    return [(method, template, compile_path(template)[0], perm) for (method, template), perm in table.items()]


_COMPILED = None


def match(method: str, path: str) -> list[tuple[str, dict, RoutePermission]]:
    """
    Every table entry a request matches: (template, path params, permission).
    Matched against the table itself (not the app's routes), so it doesn't
    depend on how FastAPI keeps included routers. Where two templates match
    (/api/plugins/catalog and /api/plugins/{plugin_id}), each must allow it.
    """
    global _COMPILED
    if _COMPILED is None:
        _COMPILED = _compile(ROUTES)
    method = "GET" if method == "HEAD" else method
    found = []
    for m, template, regex, perm in _COMPILED:
        if m != method:
            continue
        hit = regex.match(path)
        if hit:
            found.append((template, hit.groupdict(), perm))
    return found


def for_route(method: str, path: str) -> RoutePermission | None:
    """The permission a route needs (path is the route's template, e.g. /api/sessions/{session_id})."""
    method = "GET" if method == "HEAD" else method
    return ROUTES.get((method, path))
