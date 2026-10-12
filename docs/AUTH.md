# Sign-in, linked phones and keys

How Sonorium decides who may do what. Think of it as a building: the core
is the front door with its lock and a guest list, the **Users** plugin hands
out keycards to the people who live there, and the **Integration** plugin
cuts spare keys that open only some rooms.

## The parts

| Part | Where | What it does |
|---|---|---|
| Core | `sonorium/auth/` | Checks every request against the route's permission, keeps tokens (as hashes), the server's identity and HTTPS certificate, Link App and phone linking, `/api/info`, mDNS |
| Users plugin | `plugins/builtin/users/` | Accounts and roles, browser sign-in, Require login, one-time sign-in codes, Users & devices, Home Assistant's users (add-on) |
| Integration plugin | `plugins/builtin/integration/` | Keys with only the abilities you check (Settings › Keys) |

Both plugins are built in and start switched on. Without the Users plugin,
Sonorium is open to anyone on your network, the way it was before sign-in
existed. Switching it off (or uninstalling it) asks first.

## Ports

| Port | Install | What it serves |
|---|---|---|
| 8008 (add-on: 8080 inside, mapped to 8008) | all | Web UI, API and every stream, over plain HTTP |
| 8808 | all | The same web UI and API over HTTPS, always on. The certificate is self-signed, so a browser warns about it; the Sonorium app pins it instead. Docker: `SONORIUM_HTTPS_PORT` changes it. Add-on: change the mapping on the Network tab |
| 8099 | add-on | Home Assistant's sidebar (ingress). Not mapped outside; only the Supervisor (172.30.32.2) may call it |

Streams (`/stream/...`) and the speaker test tone stay open: speakers can't
sign in. Anyone on the network can listen to a channel that is playing, but
can't change anything.

## Who is calling

For each request the core works out the caller, first match wins:

1. **HA's sidebar** (add-on, port 8099, from the Supervisor): Home Assistant
   already signed the person in. The `X-Remote-User-*` headers name the HA
   user; they get their Sonorium user and the role HA gives them (see Home
   Assistant's users). Without the Users plugin: full access. The same
   headers on any other port mean nothing.
2. **A linked phone** (`Authorization: Bearer sd_...`): what its user may do,
   but never managing users, devices or keys. Linked without the Users
   plugin: full access, less those.
3. **A key** (`Bearer sk_...`, `X-Sonorium-Token`, or `?token=`): only the
   boxes checked on it, and only its channels if it has a list. A key made
   by a user who is now switched off in Home Assistant is refused.
4. **A signed-in browser** (the `sonorium_session` cookie): its user's role.
5. **BYPASS_LOGIN on:** the web UI needs no sign-in (see Recovery).
6. **Nobody recognised:** with the Users plugin, open access while Require
   login is off, otherwise nothing but the sign-in pages. Without it, full
   access.

A revoked phone or key, or a signed-out browser, is refused on its next
request: the core looks the token up every time.

## Roles

| | Admin | User |
|---|---|---|
| Channels: see, play and stop, change theme, preset, volume and speakers, add and remove | Yes | Yes |
| Playlists and sequences: see, run, edit | Yes | Yes |
| Themes and presets | See, edit, upload, delete | See |
| Speakers | Everything | See, mute and volume |
| Settings, plugins, Connection, Users & devices, Keys | Yes | No (hidden in the menu, refused by the server) |
| Link their own phone | Yes | Yes |

Sonorium always keeps at least one Admin.

## Keys

A key allows only the items checked on it:

| Area | Boxes |
|---|---|
| Channels | View · Play and stop · Change theme, preset and volume · Change speakers · Add and remove · All channels / Only these |
| Themes and presets | View · Edit · Upload and delete |
| Playlists and sequences | View · Run · Edit |
| Speakers | View · Mute and volume · Change settings |
| Triggers | Run triggers |
| Events | Read events |
| Announcements | Make announcements (`POST /api/announce`, sounds over channels) |
| Plugins | Use plugin pages and buttons |
| Settings | View · Change |

Keys never manage users or keys. A key is shown once, when it's made; only
its hash is kept. Changing a key's boxes, name or expiry works on its next
request. Expiry: 7, 30 or 90 days, a year, or never.

The plugin framework's older tokens (one per plugin, per trigger, and for the
event stream) keep working. Triggers and the event stream always need a token
or a key, even while sign-in is off.

## Accounts and sign-in (Users plugin)

- **New install:** the first visit shows **Create the admin account**, and
  Require login is on. Streams keep playing meanwhile.
- **Existing install (an update):** nothing changes. Require login is off and
  a banner offers to set it up: create the admin account, then turn on
  **Require login**. From then on the direct ports need a sign-in, a linked
  phone or a key.
- **Home Assistant add-on:** HA's sidebar never shows Sonorium's sign-in.
  Require login needs no Sonorium password: on the direct port, sign in with
  your Home Assistant username and password (see Home Assistant's users).
  Docker and Windows need an admin with a password before Require login can
  be turned on.
- **Passwords** are Argon2id hashes in `users.json`. Five failed sign-ins a
  minute from one address, then a pause; each failure is logged.
- **Browsers** stay signed in for 30 days, or until 14 days unused. The cookie
  is `HttpOnly`, `SameSite=Strict`, and `Secure` on HTTPS. Every change sends
  a CSRF token (`X-CSRF-Token`, from `GET /api/auth/me`).

## Home Assistant's users (add-on)

On the add-on, everyone with a Home Assistant login is a Sonorium user, with
the role their HA account has. Think of HA as the building's front desk: it
already knows who works here and who is a manager, so Sonorium reads the
staff list instead of keeping its own.

| In Home Assistant | In Sonorium |
|---|---|
| Owner, or the Administrators group | Admin |
| Users, Read-only | User |
| Switched off ("Can log in" off) or removed | Switched off: browsers signed out, phones refused (401 `{"pair": true}`), keys they made refused |

- **Where the list comes from:** Home Assistant's own list of users
  (`config/auth/list` on HA's WebSocket API, reached through the Supervisor
  with the add-on's token; `homeassistant_api` in the add-on's config). HA's
  system users, such as the Supervisor, are left out. Sonorium asks for it at
  most once a minute, and at once when a Home Assistant user it hasn't seen
  opens Sonorium in HA's sidebar. A role change in HA applies on the user's
  next request after that, in the browser and on their phones. If Home
  Assistant doesn't answer, Sonorium keeps the roles it last knew and
  switches nobody off.
- **Checking a password:** the Supervisor's auth API (`POST /auth`;
  `auth_api` in the add-on's config).
- **One Sonorium user per HA user,** found by HA's user id, never by name:
  renaming a user in HA keeps their Sonorium user, phones and settings.
- **Sidebar:** every HA user sees Sonorium in HA's sidebar (`panel_admin:
  false`) and gets their role there.
- **Phones:** Link App opened in HA's sidebar links the phone to that HA
  user. In the app, an HA username and password also link a phone
  (`POST /api/pair/login`).
- **Signing in on the direct port** (8008, 8808) with an HA username and
  password works the same way (`POST /api/auth/login`). Sonorium passes the
  password to Home Assistant to check, and never keeps or logs it. The usual
  limit applies: five failures a minute from one address.
- **Users & devices:** HA users carry a **Home Assistant** label. Make
  Admin, Make User and Reset password are switched off for them: their role
  and password are set in Home Assistant. Forgot password and sign-in codes
  don't apply to them either.
- **Users made in Sonorium** (with a Sonorium password, for example on a
  development version) keep working. If one has the same username as an HA
  user, signing in with that name is the HA user's; the log warns once.
  Rename or remove the Sonorium user. Add user refuses a username an HA user
  has.

**Updating from an earlier version:** the users Sonorium made automatically
for HA's sidebar (all as Admins) become HA users and take their role from
HA, so an HA owner or administrator stays Admin and everyone else becomes a
User. A Sonorium password one of them had set is dropped: like everyone
else, they sign in on the direct port with their Home Assistant password.
One password per person, kept by HA.

## Zones (Users plugin)

Off by default: Settings › Advanced › **Zones** (an admin). With it off, every
channel is open, as above. Think of each channel as a room's radio.

- **Open zone:** a channel with no owner. Anyone who may play channels may
  change it. Channels that existed before Zones were turned on stay open until
  an admin gives them an owner (the channel editor's **Owner**).
- **A user's zone:** only its owner and admins change it (what it plays, its
  volume, its speakers, play and stop, delete). Other users can see it.
- **Kid mode** (per zone, by an admin): only an admin changes its speakers.
  Its **Hours** (also the admin's): outside them the owner can't play it or
  change what it plays; stopping always works. At the end of the hours, what
  the owner started fades out over 30 seconds and stops; what an admin started
  plays on.
- **A speaker plays for one channel at a time.** A speaker in use by a playing
  channel can't be added to another channel, and another channel that has it
  plays on its other speakers. Its **home** is the channel it was added to
  first: the home can take it back even while the other channel plays (that
  one keeps playing on the rest). When the home is deleted, the next channel
  that has the speaker becomes its home. An admin can choose another home
  (`PUT /api/zones/homes/{speaker_id}`).
- **Admins override all of this** (alarms, announcements). Home Assistant
  (MQTT), plugins and Sonorium's timers act as an admin; so do keys and open
  access (Require login off, nobody signed in).
- **Links:** an admin links channels (**Link channels** on Channels) to play
  one theme and preset, or one playlist, in all of them. Each keeps its own
  volume. **Unlink** puts each channel back to what it played (and stopped, if
  it was stopped). While linked, a user can't change what a linked channel
  plays. **Leave** takes a channel out (back to what it played) and
  **Rejoin** brings it back: its owner may if an admin turned on **Can leave a
  link** for them (Users & devices, ⋯); an admin always may, and can pull a
  channel back in.

Who is calling comes from the request (`current_caller()`); the Users
plugin's `zones.py` is the session manager's policy
(`sonorium/core/channel_policy.py`) while Zones are on. Kept in `zones.json`.

## Linking a phone (core)

**Link App** (top right of Channels, on a desktop-width screen) shows a QR code:

```
https://sonorium.app/link#v=1&id=<server id>&n=Sonorium&h=<host>[,<host2>,...]&p=<http port>&s=<https port>&fp=<certificate sha256>&c=<code>[&u=<username>]
```

It's an ordinary web address, so any camera app can read it: with the
Sonorium app installed, the app opens it; without it, the page at
sonorium.app/link says how to get the app. Everything after the `#` stays on
the phone: browsers never send that part to the website.

The app also accepts the older form, with the same parameters:

```
sonorium://pair?v=1&id=<server id>&n=Sonorium&h=<host>[,<host2>,...]&p=<http port>&s=<https port>&fp=<certificate sha256>&c=<code>[&u=<username>]
```

- `h` lists the addresses to try, in order: first the one speakers use
  (Settings › Connection's stream URL, or the detected one), never an ingress
  address; then the server's other LAN IPv4 addresses (not loopback or
  Docker's internal networks); last its mDNS name (`sonorium.local`) when it
  is announced. `p` is the HTTP port speakers use.
- `fp` lets the app check it is talking to this server before it sends
  anything (it pins the certificate).
- `c` works once, for 5 minutes; **New code** makes another and the old one
  stops working.
- `u` is there when someone is signed in: the phone acts as that user.

The app then calls `POST /api/pair` over HTTPS with `{code, device_name,
platform}` and gets `{device_id, token}` once. Five pairing attempts a
minute per address; each is logged.

**Without the QR code**, the app signs in with a username and password
(`POST /api/pair/login`, HTTPS, Users plugin). It can't check the
certificate in advance, so the app and Sonorium both show four words made
from the certificate's fingerprint (Sonorium: the Link App dialog; the app:
from the certificate it received, with the list in `sonorium/auth/wordlist.py`,
one word per byte of the first four). If they match, the app keeps that
certificate from then on.

A typed pairing code without a camera (an HMAC exchange over the
fingerprint) is planned; it isn't built yet.

## Forgotten password

On a linked phone, the app asks for a **Sign-in code** (`POST
/api/devices/recovery-code`): six digits, once, for 5 minutes, five wrong
tries at most. In the browser: **Forgot password?** › **Sign in with a code**
› **Choose a new password**. Other browsers are signed out; phones stay linked.

The app should ask for the phone's lock (PIN, fingerprint or face) before
showing a sign-in code: whoever holds an unlocked linked phone could
otherwise reset the account. That check is the app's job.

## Recovery: BYPASS_LOGIN

| Install | Where |
|---|---|
| Docker | `BYPASS_LOGIN=true` in the compose file's `environment:` |
| Add-on | `bypass_login: true` on the Configuration tab |

While it's on, the web UI needs no sign-in, a red banner shows on every page,
and the log warns at start. Linked phones and keys keep working. Reset your
account under Settings › Users & devices, then set it back to false and
restart.

## Add-on: web UI only through Home Assistant

`web_ui_only_through_ha: true` makes ports 8008 and 8808 serve only streams,
linked phones and keys. The web UI is then only in HA's sidebar. No Sonorium
password is needed for it.

## Discovery

Docker (host networking) and the native installs announce on mDNS:

- **`sonorium.local`**, the server's LAN addresses, so a browser or the app
  can open `http://sonorium.local:8008` and `https://sonorium.local:8808`. If
  another device already answers for that name, Sonorium takes
  `sonorium-2.local`, then `sonorium-3.local` and so on (up to
  `sonorium-9.local`), and the log says which name it got.
- **`_sonorium._tcp`**, with TXT `id`, `name`, `api`, `http`, `https`,
  `pair` (`1` while Require login is on), `version`, `url` and `host` (the
  name above).

It's a hint only: anyone on the network could announce a server, so the app
trusts a server only after linking. The add-on isn't on the host network, so
its announcement wouldn't reach your phone: it doesn't announce (the log says
so once at start); link it with the QR code.

The HTTPS certificate names `sonorium.local` to `sonorium-9.local`,
`localhost` and the LAN addresses the server had when the certificate was
made. A certificate is never made again once it exists (that would break
every linked phone's pin), so one made by an older version keeps working
without the extra names: the app checks the fingerprint, not the names.

## Files

| File | What's in it |
|---|---|
| `security.json` (0600) | The signing secret (CSRF) and every token's SHA-256: phones, browser sessions, keys |
| `users.json` (0600) | Accounts (Argon2id hashes; none for Home Assistant's users), Require login, Can leave a link |
| `zones.json` | Zones: on or off, owners, Kid mode and hours, speaker homes, links |
| `server.json` | The server ID |
| `tls/cert.pem`, `tls/key.pem` (0600) | The HTTPS certificate (ECDSA P-256, 20 years) |
| `plugin_secrets.json` (0600) | The plugin framework's older trigger and event-stream tokens (hashes) |

All in Sonorium's config folder. Uninstalling the Users plugin keeps
`users.json`, so switching it back on brings the accounts back.

## API

Core:

| Route | Who |
|---|---|
| `GET /api/info` | anyone: server id, name, version, `api`, install type, features, ports, `host` (the speakers' address), `hostname` (the mDNS name, or null), `login_required`, fingerprint, `boot_id` (new at every start), `ui` (the web UI's version and hook version) |
| `GET /api/auth/me` | anyone: who you are, your permissions, CSRF token, bypass |
| `POST /api/link` | signed in (or open access): a Link App code and QR |
| `POST /api/pair` | anyone, HTTPS: code → phone token |
| `GET /api/devices`, `DELETE /api/devices/{id}` | your phones; an Admin: everyone's (a phone: only itself) |
| `GET`, `PUT`, `DELETE /api/devices/self` | a linked phone: its own record, a new name, unlinking itself |
| `GET /api/stream/url` | the stream URLs on the direct port |

Users plugin: `POST /api/auth/setup`, `/api/auth/login`, `/api/auth/logout`,
`/api/auth/code-login`, `/api/auth/new-password`, `PUT /api/auth/require-login`,
`POST /api/pair/login`, `POST /api/devices/recovery-code`, `GET|POST /api/users`,
`PUT|DELETE /api/users/{id}`, `POST /api/users/{id}/password`, `POST /api/users/{id}/sign-out`,
`DELETE /api/users/{id}/sessions/{session_id}` (signs one browser out).
`PUT /api/users/{id}` also takes `{"can_leave_link": bool}`.

Zones (Users plugin): `GET /api/zones` (anyone who sees channels: on or off,
owners, Kid mode, each speaker's home and the channel using it, links; for an
admin also the users), `PUT /api/zones/switch` `{"on"}`, `PUT /api/zones/{channel_id}`
`{"owner", "kid_mode", "schedule": {"start": "HH:MM", "end": "HH:MM"}}`,
`PUT /api/zones/homes/{speaker_id}` `{"channel_id"}`, `POST /api/links`
`{"name", "channels", "theme_id", "preset_id"}` or `{..., "playlist_id"}`,
`PUT|DELETE /api/links/{link_id}` (settings changes: Settings › Change),
`POST /api/links/{link_id}/leave` and `/rejoin` `{"channel_id"}` (Play and stop,
then the owner's Can leave a link, or an admin). A refused change to a channel
answers 403 (not yours, Kid mode, outside the hours) or 409 (a speaker in use,
a linked channel) with the reason in `detail`.

Integration plugin: `GET|POST /api/keys`, `PUT|DELETE /api/keys/{id}`.

Every route's permission is in `sonorium/auth/permissions.py` (or the
plugin's `PERMISSIONS`); `tests/test_auth_routes_coverage.py` fails when a
route has none, and the server refuses an API route it has no permission for.
