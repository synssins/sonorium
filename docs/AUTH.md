# Sign-in, linked phones and keys

How Sonorium decides who may do what. Think of it as a building: the core
is the front door with its lock and a guest list, the **Users** plugin hands
out keycards to the people who live there, and the **Integration** plugin
cuts spare keys that open only some rooms.

## The parts

| Part | Where | What it does |
|---|---|---|
| Core | `sonorium/auth/` | Checks every request against the route's permission, keeps tokens (as hashes), the server's identity and HTTPS certificate, Link App and phone linking, `/api/info`, mDNS |
| Users plugin | `plugins/builtin/users/` | Accounts and roles, browser sign-in, Require login, one-time sign-in codes, Users & devices, Home Assistant sidebar users |
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
   already signed the person in. Full access. The `X-Remote-User-*` headers
   pick the Sonorium account (made automatically, as an Admin) and are logged.
   The same headers on any other port mean nothing.
2. **A linked phone** (`Authorization: Bearer sd_...`): what its user may do,
   but never managing users, devices or keys. Linked without the Users
   plugin: full access, less those.
3. **A key** (`Bearer sk_...`, `X-Sonorium-Token`, or `?token=`): only the
   boxes checked on it, and only its channels if it has a list.
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
- **Home Assistant add-on:** HA's sidebar never shows Sonorium's sign-in. To
  sign in on the direct port as well, give your account a password first
  (Users & devices › Reset password…), then turn on Require login.
- **Passwords** are Argon2id hashes in `users.json`. Five failed sign-ins a
  minute from one address, then a pause; each failure is logged.
- **Browsers** stay signed in for 30 days, or until 14 days unused. The cookie
  is `HttpOnly`, `SameSite=Strict`, and `Secure` on HTTPS. Every change sends
  a CSRF token (`X-CSRF-Token`, from `GET /api/auth/me`).

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
linked phones and keys. The web UI is then only in HA's sidebar.

## Discovery

Docker (host networking) and the native installs announce on mDNS:

- **`sonorium.local`**, the server's LAN addresses, so a browser or the app
  can open `http://sonorium.local:8008` and `https://sonorium.local:8808`. If
  another device already answers for that name, Sonorium takes
  `sonorium-2.local`, then `sonorium-3.local` and so on (up to
  `sonorium-9.local`), and the log says which name it got.
- **`_sonorium._tcp`**, with TXT `id`, `name`, `api`, `http`, `https`,
  `pair`, `version`, `url` and `host` (the name above).

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
| `users.json` (0600) | Accounts (Argon2id hashes), Require login |
| `server.json` | The server ID |
| `tls/cert.pem`, `tls/key.pem` (0600) | The HTTPS certificate (ECDSA P-256, 20 years) |
| `plugin_secrets.json` (0600) | The plugin framework's older trigger and event-stream tokens (hashes) |

All in Sonorium's config folder. Uninstalling the Users plugin keeps
`users.json`, so switching it back on brings the accounts back.

## API

Core:

| Route | Who |
|---|---|
| `GET /api/info` | anyone: server id, name, version, `api`, install type, features, ports, `host` (the speakers' address), `hostname` (the mDNS name, or null), `login_required`, fingerprint |
| `GET /api/auth/me` | anyone: who you are, your permissions, CSRF token, bypass |
| `POST /api/link` | signed in (or open access): a Link App code and QR |
| `POST /api/pair` | anyone, HTTPS: code → phone token |
| `GET /api/devices`, `DELETE /api/devices/{id}` | your phones; an Admin: everyone's |
| `GET /api/stream/url` | the stream URLs on the direct port |

Users plugin: `POST /api/auth/setup`, `/api/auth/login`, `/api/auth/logout`,
`/api/auth/code-login`, `/api/auth/new-password`, `PUT /api/auth/require-login`,
`POST /api/pair/login`, `POST /api/devices/recovery-code`, `GET|POST /api/users`,
`PUT|DELETE /api/users/{id}`, `POST /api/users/{id}/password`, `POST /api/users/{id}/sign-out`,
`DELETE /api/users/{id}/sessions/{session_id}` (signs one browser out).

Integration plugin: `GET|POST /api/keys`, `PUT|DELETE /api/keys/{id}`.

Every route's permission is in `sonorium/auth/permissions.py` (or the
plugin's `PERMISSIONS`); `tests/test_auth_routes_coverage.py` fails when a
route has none, and the server refuses an API route it has no permission for.
