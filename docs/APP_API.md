# Sonorium app API

The reference for the Sonorium Android app: how it finds a server, trusts it,
links to it, controls it, follows its changes and plays its streams. Every
route, field and error here is taken from the server code (`sonorium_addon/`)
and was checked against a running server (1.6.2-dev.23, standalone on
Windows) unless a section says otherwise.

Related documents: [AUTH.md](AUTH.md) (sign-in, roles, keys),
[PLAYBACK.md](PLAYBACK.md) (sequences, playlists, fades),
[THEME_FORMAT.md](THEME_FORMAT.md) (theme files and the mixer),
[PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md) (event bus, plugins).

## Contents

0. [Words used here](#0-words-used-here)
1. [Finding servers](#1-finding-servers)
2. [Trust](#2-trust)
3. [GET /api/info](#3-get-apiinfo)
4. [Linking and sign-in](#4-linking-and-sign-in)
5. [Sign-in code (forgotten password)](#5-sign-in-code-forgotten-password)
6. [Permissions](#6-permissions)
7. [Remote control](#7-remote-control)
8. [Live updates](#8-live-updates)
9. [Listening on the phone and casting](#9-listening-on-the-phone-and-casting)
10. [Theme packages for offline playback](#10-theme-packages-for-offline-playback)
11. [Errors, rate limits, versions](#11-errors-rate-limits-versions)
12. [The app's interface: the same UI as the server](#12-the-apps-interface-the-same-ui-as-the-server)
13. [Gaps the app will hit](#13-gaps-the-app-will-hit)
14. [What was checked, and how](#14-what-was-checked-and-how)

---

## 0. Words used here

| In the web UI | In the API | What it is |
|---|---|---|
| **Channel** | **session** (`/api/sessions`, `session_id`, an 8-hex id such as `"7c18b15c"`) | What the user creates and names: a theme, a preset or sequence, speakers, a volume. The app's "channel". |
| (not shown) | **channel** (`/api/channels`, `channel_id`, an integer 1..N) | A numbered audio stream slot, `/stream/channel{N}`. A session gets one while it plays or someone listens, and gives it back when it stops. Default N = 6 (add-on option `max_channels`, 1-10). |
| Theme | theme (`theme_id`, a UUID) | A folder of audio files with `metadata.json` and `presets.json` |
| Preset | preset (`preset_id`, a slug such as `"slow_night_at_the_inn"`) | Saved track settings of one theme |
| Sequence | sequence (`sequence_id`) | Presets of one theme played in order |
| Playlist | playlist (`playlist_id`) | Themes played in order |
| Floors & Areas | spaces (`/api/spaces`) | Home Assistant's floors and areas, or Sonorium's own |
| Group (of speakers) | group (`/api/groups`) | A saved speaker selection |

The event stream calls a session a channel: its `channel_id` is the
**session id** (string), not the stream slot number.

All JSON bodies are UTF-8 JSON objects. Times are ISO 8601; most are UTC
with `+00:00`, a few session fields (`created_at`, `last_played_at`) are
naive local times without an offset.

---

## 1. Finding servers

The app can learn about a server four ways. Whichever it uses, it **trusts**
a server only after linking (section 2): the address only says where to try.

### 1.1 The Link App QR code (main path; the only one for the add-on)

In the web UI: **Link App** (top right of Channels, desktop width). The
server makes it with `POST /api/link` (section 4.2). Current format:

```
https://sonorium.app/link#v=1&id=<server id>&n=Sonorium&h=<host>[,<host2>,...]&p=<http port>[&s=<https port>]&fp=<cert sha256>&c=<code>[&u=<username>]
```

The app must also accept the older form, with the same parameters:

```
sonorium://pair?v=1&id=...&n=...&h=...&p=...&s=...&fp=...&c=...[&u=...]
```

Real example (local test server):

```
https://sonorium.app/link#v=1&id=00000000-0000-4000-8000-000000000001&n=Sonorium&h=192.0.2.25,sonorium-2.local&p=8008&s=8808&fp=0000000000000000000000000000000000000000000000000000000000000000&c=83KS6GG3&u=admin
```

| Param | Always | Meaning | What the app does |
|---|---|---|---|
| `v` | yes | Link format version, `1` | Refuse anything else ("update the app") |
| `id` | yes | Server ID (UUID), fixed for the life of the install | Key the stored server by it |
| `n` | yes | Server name. Always `Sonorium` today (no setting to change it) | Default label; let the user rename it locally |
| `h` | yes | Comma-separated addresses, in the order to try: the address speakers use first, then the server's other LAN IPv4 addresses, then its mDNS name (`sonorium.local`, or `sonorium-2.local` ... when taken) when it announces one | Try each in order on the HTTPS port; keep the whole list |
| `p` | yes | HTTP port speakers use (8008 by default) | Stream URLs (section 9) |
| `s` | no | HTTPS port (8808 by default). Missing when the server has no certificate (the `cryptography` package failed) or the add-on's mapping is switched off | Missing: the app can't link securely; show "HTTPS is off on this server" |
| `fp` | yes | SHA-256 of the server certificate's DER bytes, 64 lowercase hex chars | Pin it (section 2.1) |
| `c` | yes | One-time code, 8 chars from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`. Works once, for 5 minutes; **New code** replaces it | Send it to `POST /api/pair` |
| `u` | no | Username of whoever was signed in when the code was made; the phone will act as that user | Show "Linking as *u*" |
| `url` | no | The server's public address (e.g. `https://demo.example`), when it is behind a reverse proxy and its Stream URL is set to that address. `h` then holds only that host, `s` its port (https) | Connect to `url` and only `url`. The proxy shows its own certificate, so check it the normal way (the system's CAs and the host name) instead of pinning `fp` |

Parsing rules:

- Everything after `#` (web form) or `?` (legacy form) is a query string:
  split on `&`, then `=`, and percent-decode each value. `h` keeps literal
  commas (`safe=","`), so split `h` on `,` after decoding.
- The parameters after `#` never reach sonorium.app: browsers don't send the
  fragment. The app registers an App Link for `https://sonorium.app/link`
  (and the `sonorium` scheme) so a camera scan opens the app.
- Server code: `sonorium/auth/qr.py` (`pair_uri`, `parse_link`).

### 1.2 mDNS (Docker and native installs)

Docker (host networking), Linux and Windows installs announce:

- **`sonorium.local`** with every LAN IPv4 address. If another device
  already answers for that name, the server takes `sonorium-2.local`, then
  `-3` ... up to `sonorium-9.local`. With all taken it announces the service
  without a name (server `sonorium-<first 8 of id>.local`).
- **Service `_sonorium._tcp.local.`**, instance name
  `Sonorium <first 8 hex of server id>`, port = HTTP port, TXT:

| TXT key | Example | Meaning |
|---|---|---|
| `id` | `00000000-0000-4000-8000-000000000001` | Server ID: match it to a stored server |
| `name` | `Sonorium` | Always `Sonorium` |
| `api` | `1` | API version (section 11.3) |
| `http` | `8008` | HTTP port |
| `https` | `8808` | HTTPS port; empty string when HTTPS is off |
| `pair` | `1` or `0` | `1` while Require login is on (same as `/api/info.login_required`); it follows the setting within about 10 s. Older servers fixed it at start (often wrong), so the app still reads `login_required` from `/api/info` (G7). |
| `version` | `1.6.2-dev.23` | Server version |
| `url` | `http://192.0.2.25:8008` | The address speakers use |
| `host` | `sonorium-2.local` | The mDNS name it got (empty if none) |

Real browse result (two servers on one LAN):

```
Sonorium f4c26f39._sonorium._tcp.local.  sonorium-2.local.  8008  ['192.0.2.25']
  {'id': 'f4c26f39-...', 'name': 'Sonorium', 'api': '1', 'http': '8008', 'https': '8808', 'pair': '0', 'version': '1.6.2-dev.23', 'url': 'http://192.0.2.25:8008', 'host': 'sonorium-2.local'}
Sonorium 7ab54ced._sonorium._tcp.local.  sonorium.local.  8008  ['198.51.100.1', '192.0.2.20']
  {'id': '7ab54ced-...', ..., 'pair': '1', 'url': 'http://192.0.2.20:8008', 'host': 'sonorium.local'}
```

Note the second server lists an address that isn't on the phone's network
(`198.51.100.1`, another interface of the host). The app tries every address
and keeps the one that answers with the pinned certificate. Prefer the
address in `url` first.

Android: use `NsdManager` (service type `_sonorium._tcp`) and read the TXT
attributes; resolve `.local` names with the system resolver, which handles
mDNS on Android 12+, or fall back to the addresses the service gives.

mDNS is only a hint: anyone on the LAN can announce anything.

### 1.3 Typed address

The user types a host or IP, optionally with a port. The app:

1. Tries `https://<host>:8808/api/info` (or the typed port), accepting any
   certificate for this one call and recording its fingerprint.
2. If that fails, tries `http://<host>:8008/api/info` to read `ports.https`,
   then goes back to step 1 with that port.
3. Shows the server's version and the four security words (section 2.2),
   then signs in with username and password (section 4.3).

Never send a token or password over the HTTP port; the server refuses
linking on it (403).

### 1.4 Re-finding a server after its address changes

Store per server: `id`, `fingerprint`, every `h` address, `ports`, the
`hostname` from `/api/info`, and the device token. When a request fails to
connect:

1. Try the other stored addresses, then the stored `hostname` (`sonorium.local`).
2. Browse `_sonorium._tcp` and take the instance whose TXT `id` equals the
   stored server ID; try its addresses on its `https` port.
3. Accept a new address only if the TLS certificate's SHA-256 equals the
   stored fingerprint. A different fingerprint at a matching `id` is a
   different machine (or an attack): never send the token there.
4. Save the address that worked first in the list.

### 1.5 The Home Assistant add-on

The add-on isn't on the host network, so it doesn't announce on mDNS (its
log says so once). The QR code is the way in: `h` comes from the add-on's
stream URL and the host's own interfaces, which it asks the Supervisor for.
Ports are the add-on's **Network** mappings: HTTP 8008 (container 8080) and
HTTPS 8808. HA's sidebar (ingress, container port 8099) is never reachable
from the phone and the app never uses it.

If the host's address changes, the add-on can't be re-found by mDNS: the app
tries the stored addresses, then asks the user to scan a new code. The stored
token stays valid (the server ID and certificate don't change).

On the add-on, Home Assistant's users are Sonorium's users. A phone linked
from HA's sidebar, or signed in with an HA username and password (4.4), acts
as that HA user, with the role HA gives them: HA's owner and Administrators
are Admins, everyone else is a User. A change of role in HA applies on the
phone's next request (within a minute). A user switched off or removed in HA
gets 401 `{"pair": true}` (4.6).

---

## 2. Trust

### 2.1 HTTPS on 8808, pinned

- The app talks to the API **only** over HTTPS (default port 8808;
  `SONORIUM_HTTPS_PORT` in Docker, the Network tab in the add-on). Plain
  HTTP on 8008 is for streams only.
- The certificate is self-signed: ECDSA P-256, valid 20 years, made on first
  start and never remade (remaking it would break every pin). Its names are
  `sonorium.local` .. `sonorium-9.local`, `localhost`, `127.0.0.1` and the LAN
  addresses it was made with; a certificate from an older version has fewer
  names. **The app ignores names and the CA chain and checks only the
  fingerprint.**
- Fingerprint = lowercase hex SHA-256 of the leaf certificate's DER bytes.
  `/api/info.fingerprint` and the QR `fp` use the same value.
- Android: a custom `X509TrustManager` that hashes
  `chain[0].encoded` with SHA-256 and compares it to the stored value; plus a
  `HostnameVerifier` that accepts the pinned connection. OkHttp's
  `CertificatePinner` doesn't fit: it pins a key on top of a chain the
  system already trusts. Keep the hostname check off only for these pinned
  connections.

### 2.2 The four security words

When the app can't get the fingerprint out of band (typed address), the app
and the server both show four words made from it:

```
words = [WORDS[b] for b in bytes.fromhex(fingerprint)[:4]]
```

That is, the first four bytes of the fingerprint, one word each, as indexes
into a list of 256 words. The server shows them in the Link App dialog
(`POST /api/link` → `words`), in Users & devices, and returns them from
`GET /api/auth/me` and `POST /api/pair/login`. Example: fingerprint
`6f310f64...` → bytes 0x6f 0x31 0x0f 0x64 → `forest candy award fancy`.

The list (`sonorium/auth/wordlist.py`), index 0 first, 8 per line. The app
must use it unchanged, in this order:

```
acorn actor adult agent alarm album alley amber
angle ankle apple apron arrow atlas attic award
bacon badge bagel baker banjo barn basil beach
beard bench berry bike birch bison blade blank
bloom board boat bonus boot bread brick bride
brook brush bucket buddy bugle cabin cable cactus
camel candy canoe cargo carpet castle cedar chair
chalk cheese cherry chess chief cider cliff clock
cloud clover coach coast cobra comet coral cotton
couch crane crown cube daisy dance delta denim
desk diary dingo disco dock dolphin donut dragon
drum duck eagle earth easel echo elbow elder
ember engine fable falcon fancy farm feast fence
ferry fiddle field flag flame flute focus forest
fossil fox frost fruit gadget garden garlic gecko
giant ginger glass globe glove goose grape gravy
guitar hammer harbor harp hazel heron hiker honey
hornet hotel igloo inlet iris island ivory jacket
jaguar jelly jewel jockey judge juice jungle kayak
kettle kiosk kitten koala ladder lagoon lamp lemon
lilac lion lizard llama lobster locket lotus lunar
magnet mango maple marble meadow melon mint mirror
mitten monkey moose mosaic motor muffin napkin nectar
needle nickel noodle nutmeg oasis ocean olive onion
opera orbit orchid otter owl oyster paddle panda
parrot peach pebble pencil pepper piano pickle pilot
pine planet plum pony poppy pretzel puffin pumpkin
quail quartz quilt rabbit radar radish raven reef
ribbon river robin rocket rose ruby saddle salmon
sandal scarf shell silver sketch sled spoon squid
stone sugar summit tango teapot tiger tomato torch
tulip turtle velvet violin walnut whale willow zebra
```

### 2.3 Trust on first use (typed address + password)

1. The app connects to the typed address on HTTPS, records the certificate's
   fingerprint, and shows its four words with the text "Check that Sonorium
   shows the same words (Link App, or Settings › Users & devices)".
2. The user confirms, then the app sends `POST /api/pair/login` (section 4.3)
   on that same pinned connection.
3. The app compares `words` in the answer with what it showed (a sanity
   check; both came from the same server) and stores the fingerprint. From
   then on it's pinned exactly as if it came from a QR code.

If the user can't compare (no screen near), the app may still proceed, but
should say plainly that the connection is unverified until they check. A
typed one-time code with an HMAC proof over the fingerprint is planned but
not built (AUTH.md).

---

## 3. GET /api/info

`GET /api/info` · public (no token) · HTTP and HTTPS · never 401.

```json
{
  "id": "00000000-0000-4000-8000-000000000001",
  "name": "Sonorium",
  "version": "1.6.2-dev.23",
  "api": 1,
  "install": "windows",
  "features": {
    "connection_settings": {"available": true, "enabled": true, "overridable": false, "switchable": false, "default": true, "live": false},
    "network_speakers":    {"available": true, "enabled": true, "overridable": false, "switchable": true, "default": true, "live": false},
    "space_editing":       {"available": true, "enabled": true, "overridable": false, "switchable": false, "default": true, "live": false},
    "plugin_packages":     {"available": true, "enabled": false, "overridable": true, "switchable": true, "default": false, "live": false},
    "keys":                {"available": true, "enabled": true, "overridable": true, "switchable": true, "default": true, "live": true},
    "announcements":       {"available": true, "enabled": true, "overridable": true, "switchable": true, "default": true, "live": true}
  },
  "ports": {"http": 8008, "https": 8808},
  "host": "192.0.2.25",
  "url": "https://192.0.2.25:8808",
  "public_url": null,
  "internal_ports": {"http": 8008, "https": 8808},
  "hostname": "sonorium-2.local",
  "login_required": true,
  "pair": true,
  "fingerprint": "0000000000000000000000000000000000000000000000000000000000000000",
  "plugins": ["users"],
  "accounts": true,
  "boot_id": "54615c1e5e5d4331a7b9cd713cd45918",
  "ui": {"version": "1.6.2-dev.24", "host_api": 1},
  "personal_channel": true
}
```

| Field | Type | What the app does with it |
|---|---|---|
| `id` | string (UUID) | Must equal the QR `id` / stored ID. A mismatch: a different server. |
| `name` | string | The server's name: Settings › Advanced › Server name (`server_name` in `GET /settings`, 7.7); `"Sonorium"` when none is set. Show it in the server list. The mDNS TXT `name` is the same. |
| `version` | string | Show it in server details. Don't parse it for features; use `api` and the presence of fields. |
| `api` | int | API version, `1`. Section 11.3. |
| `install` | `"addon"`, `"docker"`, `"windows"`, `"linux"` | Wording ("Home Assistant app", "Docker"); `addon` means: no mDNS re-discovery, floors and areas come from HA. |
| `features.<name>` | `{available, enabled, overridable, switchable, default, live}` | `available`: offered on this install; `enabled`: running now; `overridable`: shown in Settings › Advanced; `switchable`: `PUT /api/install/features` may change it; `default`: on out of the box; `live`: a change applies at once (otherwise after a restart). `network_speakers.enabled` false: hide manual speakers and network scans. `space_editing.enabled` false: floors and areas are read-only (HA owns them). `connection_settings`: hidden on the add-on. `keys.enabled` false: keys are refused (401) and `/api/keys` answers 404; hide key controls. `announcements.enabled` false: `POST /api/announce` answers 409; hide announce controls. A feature missing from the list (an older server): treat it as on. |
| `ports.http` | int | The HTTP port speakers use (from the stream URL). Stream URLs use it. |
| `ports.https` | int or null | The HTTPS port as the LAN sees it. `null`: HTTPS is off (no certificate, or the add-on mapping is off); the app can't link. |
| `host` | string | The address speakers use (the stream URL's host). Add it to the stored addresses. Behind a reverse proxy: the public host. |
| `url` | string | The one address to reach the API at: `public_url` when set, else `https://<host>:<ports.https>` (`http://<host>:<ports.http>` when HTTPS is off). Don't assume 8808: use this. |
| `public_url` | string or null | The server's public address (scheme, host and port; no path) when it is behind a reverse proxy: the Stream URL set by hand (Settings › Advanced › Network, or `SONORIUM__STREAM_URL`) to an `https` address, or to an `http` one that isn't a LAN address or `.local` name. `ports` then follow it: `ports.https` is its port (https), `ports.http` its port (http) or the server's own HTTP port. Null: no proxy; everything as before. With it, connect only to it and check its certificate normally (no pin: section 1.1 `url`). |
| `internal_ports` | `{http, https}` | The ports Sonorium itself listens on (inside a container: before any mapping). For information; connect to `url`. `https` null when HTTPS is off. |
| `hostname` | string or null | The mDNS name (`sonorium.local`, `sonorium-2.local`, ...); null on the add-on or when not announced. Store it as a fallback address. |
| `login_required` | bool | True when **Require login** is on (and the recovery bypass is off). Informational: the app always links and always sends its token. |
| `pair` | bool | Always `true`: the server supports linking. |
| `fingerprint` | string or null | Certificate SHA-256 (hex). Null when HTTPS isn't available. Compare with the pinned value; for a typed address it's the value to show as words. |
| `plugins` | string[] | `["users"]` while accounts are loaded. Users are part of Sonorium now (not a plugin); `"users"` stays here for released apps. New code reads `accounts`. Keys are never listed (see `features.keys`). |
| `accounts` | bool | User accounts are loaded: roles apply, signing in with a username and password works. Missing (an older server): use `"users" in plugins`. |
| `boot_id` | string (32 hex) | New every time the server starts. A different value than last time = the server restarted: refetch everything; the event stream's ids carry it (8.3). Missing on servers before the field (G4). |
| `ui` | `{version, host_api}` | The web UI the server serves: its version, and `host_api`, the version of its `window.SONORIUM_HOST` hooks (12.2.2). Missing: the server's UI has no hooks; use the bundled copy (12.3). |
| `personal_channel` | bool | `true`: the server has the personal channel (7.11). Missing: it doesn't; hide the headphone button. |

**Detecting accounts:** `accounts` (older servers: `"users" in plugins`). Users are part of Sonorium, so a current server always has them; false only when the server runs without accounts loaded.
- Present: accounts exist (or will on first visit), roles apply, `POST
  /api/pair/login` and `POST /api/devices/recovery-code` exist.
- Absent: there are no accounts; the app links only by QR code, every linked
  phone has full access less managing devices and keys (section 6), and the
  password sign-in and sign-in code screens are hidden.

**Older servers** (before 1.6.2, no sign-in): `/api/info` answers 404. The
app says "This Sonorium is too old for the app. Update it to 1.6.2 or later."

Code: `sonorium/auth/routes.py` `info()`.

---

## 4. Linking and sign-in

### 4.1 Overview

```
QR scanned ──► pin fp ──► GET https://h:s/api/info (check id, fp) ──► POST /api/pair {code}
Typed address ─► GET https://host:8808/api/info ──► show words ──► POST /api/pair/login {username, password}
                                                       │
                                     {device_id, token} once ──► Keystore
Every request: Authorization: Bearer sd_...   over pinned HTTPS
401 with "pair": true ──► forget the token, back to linking
```

### 4.2 POST /api/link (web UI; for reference)

`POST /api/link` · permission `self` (a signed-in browser, HA's sidebar, or
anyone when no one needs to sign in). **Not callable by a linked phone**
(phones don't have `self`). The app never calls it; it reads its result from
the QR code.

Response:

```json
{
  "uri": "https://sonorium.app/link#v=1&id=...&n=Sonorium&h=192.0.2.25,sonorium-2.local&p=8008&s=8808&fp=43ac...&c=83KS6GG3&u=admin",
  "qr_svg": "<svg ...>",
  "code": "83KS6GG3",
  "username": "admin",
  "expires_in": 299,
  "words": ["coast", "meadow", "gecko", "ferry"],
  "host": "192.0.2.25",
  "hosts": ["192.0.2.25", "sonorium-2.local"],
  "http_port": 8008,
  "https_port": 8808
}
```

A new code for the same user cancels that user's previous code. Codes live
in memory: a server restart cancels them.

### 4.3 POST /api/pair (QR code)

`POST /api/pair` · public · **HTTPS only** · rate limited (section 11.2).

Request:

```json
{"code": "83KS6GG3", "device_name": "Pixel 8", "platform": "android", "model": "Pixel 8"}
```

| Field | Type | Notes |
|---|---|---|
| `code` | string, required | Case-insensitive; spaces and `-` are ignored |
| `device_name` | string | Shown in Users & devices. Trimmed to 60 chars; empty becomes `"Phone"`. Use the user-visible device name. |
| `platform` | string | `"android"`. Trimmed to 30. |
| `model` | string | `Build.MODEL`. Trimmed to 60. |

Response 200:

```json
{
  "device_id": "t_d9d7dc40fc00e64a",
  "token": "sd_MskZrrMRXSyuKdbOPwIJmKTeok_sYzkniFMFQymQS8k",
  "user": {"id": "u_e1a43c68a6633284", "username": "admin", "role": "admin", "role_label": "Admin"},
  "server_id": "00000000-0000-4000-8000-000000000001"
}
```

- `token`: `sd_` + 43 URL-safe base64 chars (32 random bytes). **Shown once;**
  the server keeps only its SHA-256. It doesn't expire; it stops working when
  the device is removed or its user is deleted.
- `user`: the user the phone acts as (the QR `u`), or `null` when the code
  was made with nobody signed in (Require login off and nobody signed in).
- `server_id`: check it equals the stored `id`.

Errors:

| Status | Body | When |
|---|---|---|
| 401 | `{"detail": "This code doesn't work. Show a new code in Sonorium."}` | Wrong, used or expired code |
| 403 | `{"detail": "Use the HTTPS port to link a phone."}` | Sent to the HTTP port while HTTPS is on |
| 429 | `{"detail": "Too many attempts. Try again in a minute."}` + `Retry-After: <s>` | Rate limit |
| 400 | `{"detail": "Invalid JSON body"}` / `"Expected a JSON object"` | Bad body |
| 403 | `{"detail": "Not allowed from another site"}` | Only browsers trigger this (an `Origin` header from another site) |

### 4.4 POST /api/pair/login (username and password)

`POST /api/pair/login` · public · **HTTPS only**.

Request:

```json
{"username": "admin", "password": "…", "device_name": "Pixel 8", "platform": "android", "model": "Pixel 8"}
```

Response 200: as `/api/pair` plus `words`:

```json
{
  "device_id": "t_7418cbe1ffd04972",
  "token": "sd_YDqZXX8BdWHDBtWQBpp6H6mJMxGC3bcccNtujcTF7f8",
  "user": {"id": "u_e1a43c68a6633284", "username": "admin", "role": "admin", "role_label": "Admin"},
  "server_id": "00000000-0000-4000-8000-000000000001",
  "words": ["forest", "candy", "award", "fancy"]
}
```

Errors: 401 `{"detail": "Wrong username or password."}` (usernames are
case-insensitive); 403 HTTP port; 429 after 5 failures a minute from one
address (`Retry-After`). Works whether or not Require login is on.

**Add-on:** the username and password are a Home Assistant user's. Sonorium
passes them to Home Assistant (the Supervisor) to check and keeps neither.
The phone then acts as that HA user, with their HA role (`user.ha` is
`true`). When Home Assistant doesn't answer: 503 `{"detail": "Home Assistant
didn't answer. Try again in a moment."}`. A user made in Sonorium (with a
Sonorium password) also works, unless an HA user has the same username: then
the HA user is the one signed in.

### 4.5 Using the token

- Store the token with the server record in Android Keystore-backed storage
  (EncryptedSharedPreferences / a Keystore-wrapped key). Never log it.
- Send it on every API call: `Authorization: Bearer sd_...`. The server also
  accepts `X-Sonorium-Token: sd_...` and `?token=sd_...`; the app uses the
  header (query tokens end up in logs, though the server masks them).
- No CSRF token, cookie or `Origin` handling is needed for a bearer token.
- `GET /api/auth/me` with the token tells the app who it is and what it may
  do (section 6):

```json
{
  "kind": "device",
  "user": {"id": "u_02433556b13baea2", "username": "member", "role": "user", "role_label": "User", "ha": false},
  "csrf": null, "reset": false,
  "perms": ["ch.add", "ch.change", "ch.play", "ch.speakers", "ch.view", "device", "ev.read",
            "pl.edit", "pl.run", "pl.view", "sp.mute", "sp.view", "th.view"],
  "bypass": false, "ingress": false, "web_ui": true,
  "words": ["forest", "candy", "award", "fancy"],
  "plugins": ["users"], "accounts": true,
  "starting": false, "closed": false,
  "require_login": true, "has_users": true, "needs_setup": false, "ha_users": false
}
```

`require_login`, `has_users`, `needs_setup` and `ha_users` are there while
accounts are loaded (`accounts` true). `ha_users` is `true` on the add-on, where
Home Assistant's users sign in; `user.ha` is `true` for one of them. Call `/api/auth/me` after linking, on every app start, and
and after an unexpected 403 (an admin may have changed the user's role).

### 4.6 The 401 `{"pair": true}` rule

Any API answer **401 whose body has `"pair": true`** means the token is no
longer valid. The app deletes the stored token and shows "This phone was
unlinked from Sonorium. Link it again." Bodies seen:

| Body | When |
|---|---|
| `{"detail": "This phone is not linked to Sonorium. Link it again.", "pair": true}` | Unknown or removed `sd_` token, its user was deleted, or (add-on) its Home Assistant user is switched off or removed in HA |
| `{"detail": "Link this phone again to sign it in.", "pair": true}` | A phone linked while nobody was signed in, after Require login was turned on |
| `{"detail": "A valid key or linked phone is needed", "pair": true}` | A token of no known kind |

Other 401s are not about the token (for example a wrong code). `/api/info`
never answers 401, so the app can always check the server is there.

### 4.7 Removing a phone

- **From the web UI:** Settings › Users & devices › Remove. The token stops
  working on its next request (401 `pair`).
- **API:** `GET /api/devices` and `DELETE /api/devices/{device_id}` (`self`):
  a signed-in user sees and removes their own phones, an admin everyone's.
  `GET` returns:

```json
{"devices": [{"id": "t_d9d7dc40fc00e64a", "name": "Pixel test", "user_id": "u_e1a43c68a6633284",
              "platform": "android", "model": "Pixel 8",
              "created": "2026-10-11T21:03:16.797243+00:00", "last_seen": "2026-10-11T21:03:16.932117+00:00",
              "username": "admin"}]}
```

  `DELETE` → `{"removed": true}`; 404 `{"detail": "No such device"}`; 403
  `{"detail": "Not allowed"}` (someone else's phone, not an admin).
- **A phone and its own record (G1):** linked phones don't have `self`, so
  `GET /api/devices` answers 403 to a device token. A phone uses
  `/api/devices/self` (permission `device`, only a linked phone; a browser
  gets 403):

  | Method | Body | Answer | Errors |
  |---|---|---|---|
  | `GET /api/devices/self` | | its row: `{"id", "name", "user_id", "platform", "model", "created", "last_seen"}` | |
  | `PUT /api/devices/self` | `{"device_name": "Chris's phone"}` (`name` is accepted too) | the row with the new name (trimmed to 60 chars) | 400 `"A device_name is needed"` (missing or blank) |
  | `DELETE /api/devices/self` | | `{"removed": true}`; the token stops working at once (the next call is 401 `pair`) | |

  `DELETE /api/devices/{device_id}` also takes a phone's token, for its own
  id only (403 `"Not allowed"` for any other phone, even of the same user).
  "Unlink" in the app: `DELETE /api/devices/self`, then forget the token.

  ```
  GET /api/devices/self  → {"id": "t_5301c0783ecca0fe", "name": "Verify phone", "user_id": "u_71679933bec644d1",
                            "platform": "android", "model": "Test", "created": "2026-10-11T22:08:11.520320+00:00",
                            "last_seen": "2026-10-11T22:08:11.549219+00:00"}
  ```
- **A phone linked with nobody signed in** (`user: null`, Require login
  off) has full access (less devices and keys) and gets 401 `pair` once
  Require login is on.

---

## 5. Sign-in code (forgotten password)

`POST /api/devices/recovery-code` · permission `device` (**only a linked
phone**; a browser gets 403) · no body.

Response 200:

```json
{"code": "052147", "expires_in": 299, "username": "member"}
```

- Six digits, works once, for 5 minutes, five wrong tries at most. Asking
  again replaces the previous code.
- Rate limit: 3 codes in a row, then one a minute per phone (429 +
  `Retry-After`).
- 403 `{"detail": "Only a phone linked to a user can show a sign-in code"}`:
  the phone has no user (linked while nobody was signed in).
- 409 `{"detail": "Set in Home Assistant"}`: the user is a Home Assistant
  user (add-on), whose password is changed in Home Assistant.

The user then opens the web UI: **Forgot password?** › **Sign in with a
code**, types the username and the code (`POST /api/auth/code-login`), and
chooses a new password. Their other browsers are signed out; phones stay
linked.

**UX rule (the app's job):** before showing the code, ask for the phone's
lock with `BiometricPrompt` using `BIOMETRIC_STRONG or DEVICE_CREDENTIAL`
(fingerprint, face, PIN, pattern or password). If the phone has no lock at
all (`KeyguardManager.isDeviceSecure` is false, or
`canAuthenticate()` answers `BIOMETRIC_ERROR_NONE_ENROLLED` for both), show
the code anyway, with a note: "Anyone holding this phone can reset your
Sonorium password. Set a screen lock to protect it." Never cache the code;
show it with a countdown from `expires_in`.

---

## 6. Permissions

Every route needs one permission (`sonorium/auth/permissions.py`); the
middleware refuses before the handler runs. The app reads its own list from
`GET /api/auth/me` → `perms` and hides what it can't do.

### 6.1 Abilities

| Ability | Covers |
|---|---|
| `ch.view` | See channels, stream URLs, settings (read), `/api/status` |
| `ch.play` | Play, pause, stop, listen, stop all |
| `ch.change` | Theme, preset, volume, name of a channel; speakers muted (also `sp.mute`) |
| `ch.speakers` | Change a channel's speakers or speaker group |
| `ch.add` | Create and delete channels |
| `th.view` | Themes, tracks, presets, categories, track audio, theme export, theme packages (10) |
| `th.edit` | Track mixer, presets, groups, categories, favorites, rename |
| `th.upload` | Create, upload into, import, delete themes |
| `pl.view` / `pl.run` / `pl.edit` | Sequences and playlists: see / run on a channel / create and edit |
| `sp.view` | Speakers, hierarchy, floors and areas (read), speaker groups (read), scans |
| `sp.mute` | A channel's speakers-muted switch; a speaker's own volume and mute (G6) |
| `sp.set` | Speaker settings, enabled speakers, manual speakers, groups, test sound |
| `st.view` / `st.change` | Logs, HA cards / app settings, Advanced values, floors and areas, install features |
| `ev.read` | The event stream and catalogue |
| `pg.use` | Plugin list, plugin pages and actions |
| `tr.run` | Plugin webhook triggers |
| `an.send` | Announcements and sounds over channels (`/api/announce`, `/api/sounds`); Admins only by role |
| `admin.plugins` | Install, enable, configure plugins; schedules; the reference render (10.3) |
| `admin.connection` | Settings › Connection (HA URL and token, MQTT) |
| `admin.users`, `admin.keys`, `self` | Users, keys, own devices: **never** on a phone |
| `device` | Only a linked phone (the sign-in code) |

### 6.2 What a phone token may do

A phone always gets its user's (or the open) abilities **minus**
`admin.users`, `admin.keys` and `self`, **plus** `device`.

| Situation | Phone's abilities (verified) |
|---|---|
| Phone of an **Admin** | Everything except `admin.users`, `admin.keys`, `self`: `admin.connection admin.plugins an.send ch.* device ev.read pg.use pl.* sp.* st.* th.* tr.run` |
| Phone of a **User** | `ch.add ch.change ch.play ch.speakers ch.view device ev.read pl.edit pl.run pl.view sp.mute sp.view th.view` |
| Phone with no user, Require login **off** | As an Admin's phone (open access) |
| Phone with no user, Require login **on** | Nothing: 401 `{"pair": true}` |

So a User's phone can do everything on channels, playlists and sequences,
but only see themes, speakers and settings. Routes marked **Admin** below
answer 403 `{"detail": "Only an admin can do this"}` to a User's phone.

Not built yet (PLAN-mobile-api.md §10): channel ownership. Today any User
can change or delete any channel.

The app needs no special handling for keys (`sk_...`, Settings › Keys, part
of Sonorium and switched on or off in Settings › Advanced); they're for
scripts and devices.

---

## 7. Remote control

All routes are under `https://<host>:<https>/api`, with the bearer token.
**Admin** = needs an ability only Admins have (`th.edit`, `th.upload`,
`sp.set`, `st.change`, `admin.*`). Request bodies are JSON unless noted.
Errors follow section 11.1. Shapes were checked live unless marked
*(code only)*.

### 7.1 Channels (sessions)

#### The channel object (`SessionResponse`)

```json
{
  "id": "7c18b15c",
  "name": "Phone test",
  "name_source": "custom",
  "theme_id": "df297db5-a98d-4372-8029-2c49d1e280e4",
  "preset_id": "slow_night_at_the_inn",
  "speaker_group_id": null,
  "adhoc_selection": {"include_floors": [], "include_areas": ["office"], "include_speakers": [],
                      "exclude_areas": [], "exclude_speakers": []},
  "volume": 40,
  "is_playing": false,
  "speakers": ["media_player.office"],
  "speaker_summary": "Office",
  "channel_id": 1,
  "sequence_id": null,
  "preset_choice": "slow_night_at_the_inn",
  "playlist_id": null,
  "sequence": null,
  "playlist": null,
  "speakers_muted": false,
  "announcements": true,
  "listening": true,
  "outputs": [],
  "recent": [{"at": "2026-10-11T21:03:18.046860+00:00", "text": "Speakers unmuted"}],
  "created_at": "2026-10-11T21:03:17.031646",
  "last_played_at": null
}
```

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Session id |
| `name`, `name_source` | string, `"custom"` / `"auto"` | Name, and whether the user typed it or it follows the speakers |
| `theme_id`, `preset_id` | string or null | Current theme and preset |
| `speaker_group_id` | string or null | A saved group, or |
| `adhoc_selection` | object or null | Its own selection: floors, areas, speakers to include, areas and speakers to exclude |
| `volume` | int 0-100 | Channel volume |
| `is_playing` | bool | Playing **on its speakers** |
| `speakers` | string[] | Resolved speaker ids |
| `speaker_summary` | string | e.g. `"No speakers"`, `"Office"` |
| `channel_id` | int or null | Stream slot while it has one |
| `sequence_id`, `playlist_id` | string or null | What runs |
| `preset_choice` | string or null | What the preset menu shows: `"seq:<id>"` while a sequence is set, else `preset_id` |
| `sequence`, `playlist` | object or null | Status (below) |
| `speakers_muted` | bool | The speakers' stream is silenced (listeners on the phone still hear it) |
| `announcements` | bool | Announcements play on the channel (off: none plays, from Home Assistant, MQTT, `POST /api/announce` or a plugin) |
| `listening` | bool | Someone listens with `?local=1` (the web UI's Listen here, or the phone) |
| `outputs` | output[] | Devices the app reports playing the channel (G2, section 9.5); `[]` when none |
| `recent` | `[{at, text}]` | Latest changes, newest first |
| `created_at`, `last_played_at` | string (naive local time) or null | |

Status objects (PLAYBACK.md):

```json
"sequence": {"id": "gentle_storm", "name": "Gentle storm", "running": true, "single_pass": false,
             "step": 2, "steps": 3, "at_end": "reverse", "current_preset_id": "rain",
             "current_preset_name": "Rain", "next_change_at": "2026-10-10T21:14:03+00:00",
             "seconds_until_change": 412},
"playlist": {"id": "1a2b3c4d", "name": "Evening", "running": true, "entry_index": 1, "position": 1,
             "entries": 3, "theme_id": "a93e...", "theme_name": "Storm",
             "next_change_at": "2026-10-10T21:40:00+00:00", "seconds_until_change": 1969}
```

#### Routes

| Method, path | Perm | Body | Answer | Errors |
|---|---|---|---|---|
| `GET /sessions` | ch.view | | channel[] | |
| `POST /sessions` | ch.add | `{theme_id?, preset_id?, speaker_group_id?, adhoc_selection?, custom_name?, volume?}` | 201, channel | 404 unknown theme/sequence, 400 bad value, 422 volume out of 0-100 |
| `GET /sessions/{id}` | ch.view | | channel | 404 `"Session not found"` |
| `PUT /sessions/{id}` | ch.change (+ ch.speakers to change speakers) | any of the POST fields; only those sent change | channel | 404, 409 sequence with no presets, 403 `"Not allowed to change speakers"` |
| `DELETE /sessions/{id}` | ch.add | | 204, no body | 404 |
| `POST /sessions/{id}/play` | ch.play | none | `{"status": "playing", "channel_id": 1, "sequence_id": null, "playlist_id": null}` | 400 `"No theme selected"`, `"Theme not found"`, `"No speakers selected"`, 404, 409 `"6 channels are already playing (the most at once is 6). Stop one first."` (Max channels) |
| `POST /sessions/{id}/pause` | ch.play | none | `{"status": "paused"}` | 404 |
| `POST /sessions/{id}/stop` | ch.play | none | `{"status": "stopped"}` | 404 |
| `POST /sessions/stop-all` | ch.play | none | `{"stopped": 2}` | |
| `POST /sessions/{id}/volume` | ch.change | `{"volume": 40}` (0-100) | `{"volume": 40}` | 404, 422 |
| `PUT /sessions/{id}/speakers-muted` | ch.change or sp.mute | `{"muted": true}` | `{"speakers_muted": true}` | 404 |
| `PUT /sessions/{id}/announcements` | ch.change | `{"on": false}` | `{"announcements": false}` | 404, 400 |
| `POST /sessions/{id}/listen` | ch.play | none, or `{"hold_seconds": 0-300}` (or `?hold_seconds=`) | `{"channel_id": 1, "stream_path": "/stream/channel1", "hold_seconds": 15}` | 404, 400 `"No theme selected"`/`"Theme not found"`/`"hold_seconds must be between 0 and 300"`, 409 `"No free channel"` |
| `DELETE /sessions/{id}/listen` | ch.play | none | `{"status": "ok"}` | 404 |
| `GET /sessions/{id}/outputs` | ch.view | | `{"outputs": [output]}` | 404 |
| `POST /sessions/{id}/outputs` | ch.play | `{kind?, name?, address?, receiver_id?}` | 201, output + stream fields (9.5) | 404, 400 bad field / no theme, 409 `"No free channel"` |
| `PUT /sessions/{id}/outputs/{output_id}` | ch.play | none | heartbeat: output + stream fields | 404 `"Output not found"` (expired or dropped: POST again) |
| `DELETE /sessions/{id}/outputs/{output_id}` | ch.play | none | `{"removed": true}` | 404 |
| `GET /sessions/{id}/playback` | ch.view | | `{session_id, sequence_id, preset_choice, playlist_id, sequence, playlist}` | 404 |
| `GET /now-playing` | ch.view | | `{at, channels: [...]}`: the playing channels' tracks as the mixer has them (see below) | |
| `POST /sessions/{id}/sequence` | pl.run | `{"sequence_id": "gentle_storm"}` | playback object | 400 missing id, 404, 409 |
| `DELETE /sessions/{id}/sequence` | pl.run | | playback object | 404 |
| `PUT /sessions/{id}/playlist` | pl.run | `{"playlist_id": "1a2b3c4d"}` | playback object | 400, 404, 409 |
| `DELETE /sessions/{id}/playlist` | pl.run | | playback object | 404 |
| `POST /sessions/{id}/next` | pl.run | none | `{"skipped": "playlist"\|"sequence"\|"preset"\|"end", ...playback}` | 404, 409 `"The channel is not playing"`, 409 `"Nothing to go to next"` |
| `POST /sessions/{id}/previous` | pl.run | none | `{"skipped": "playlist"\|"sequence"\|"preset"\|"end", ...playback}` | 404, 409 `"The channel is not playing"`, 409 `"Nothing to go back to"` |

Notes:

- **Theme:** `PUT /sessions/{id}` `{"theme_id": "..."}`; a playing channel
  crossfades. With a new theme and no `preset_id`, the theme's default preset
  is used.
- **Preset:** `PUT` `{"preset_id": "rain"}` (3 s glide; stops a running
  sequence). `""` = no preset. `"seq:<sequence_id>"` starts that sequence
  (same as `POST .../sequence`).
- **Next** (`POST .../next`): with a playlist, its next entry (`"end"`
  when a playlist that doesn't repeat was on its last entry: the channel
  stops); else with a sequence, its next step (a 3 s glide); else the
  theme's next preset (a 3 s glide), with Default settings (`preset_id`
  null) first in the list and Default settings again after the last preset.
  409 `"Nothing to go to next"` only when the theme has no presets.
  `skipped` says which.
- **Previous** (`POST .../previous`, the mirror of Next): with a playlist,
  its previous entry (from the first entry it wraps to the round's last if
  the playlist repeats, else the first entry starts again); else with a
  sequence, its previous step (a 3 s glide; from the first step it wraps to
  the last when At the end is restart, else that step starts again); else
  the theme's previous preset, with Default settings (`preset_id` null)
  first in the list and the last preset before it. `skipped` says which.
- **Speakers:** `PUT` `{"speaker_group_id": "..."}` or `{"adhoc_selection":
  {...}}`. A playing channel starts and stops speakers live.
- **Play/stop/pause** answer at once; the fade and speaker commands follow in
  the background. Watch `channel.started` / `channel.stopped` events or poll.
  `play` sets `is_playing` true immediately (optimistic).
- `play` needs at least one speaker. The phone listening alone uses
  `listen` (section 9), not `play`.
- **Now playing** (`GET /now-playing`, the web UI's Status page): one entry
  per playing channel (speakers playing or a browser listening) with `id`,
  `name`, `theme_name`, `preset_name`, `sequence` (`{name, step, steps}` or
  null), `speakers`, `speakers_muted`, `listening`, `buffer_seconds` (the
  speakers' estimated buffer, the longest of them) and `sections`
  (`[{title, rows}]`). A row: `{type: "track"|"group", key, name, state:
  "on"|"fade"|"wait"|"rest"|"off", mode, now, seconds, level, band}`; `now`
  holds `{t}` where the countdown `seconds` goes ("Next in {t}"); `level` and
  `band` are 0..1 (`band`: an Ebb & Flow track's range); a group has
  `tracks`. Poll it only while the screen shows it.
- **Advanced dialog of a sequence step** (`/themes/{id}/sequences/{seq}/transitions/{preset}`,
  `/while/{preset}`, `/transition_tracks`) is in PLAYBACK.md; the app can
  leave it to the web UI.

#### Low-level stream slots (don't drive these)

| Method, path | Perm | Answer |
|---|---|---|
| `GET /channels` | ch.view | `[{"id": 1, "name": "Channel 1", "state": "idle", "current_theme": null, "current_theme_name": null, "client_count": 0, "stream_path": "/stream/channel1"}, ...]` |
| `GET /channels/{n}` | ch.view | one of those; 404 `"Channel 9 not found"` |
| `POST /channels/{n}/play`, `/stop`, `/volume` | ch.play / ch.change | Legacy: they bypass channels (sessions) entirely; `/volume` is a placeholder that does nothing. **Don't use.** |

`client_count` drifts after abrupt disconnects (seen: 4 after three clients
left); don't show it as a listener count.

### 7.2 Themes

| Method, path | Perm | Answer |
|---|---|---|
| `GET /themes` | th.view | theme summary[] (below) |
| `POST /themes/refresh` | th.view | `{"status": "ok", "themes_count": 3, "message": "Refreshed 3 themes"}` (rescans folders) |
| `GET /themes/{id}` | th.view | `{"id", "name", "track_count", "url", "tracks": [{"name"}]}` |
| `GET /themes/{id}/tracks` | th.view | `{theme_id, theme_name, short_file_threshold, tracks: [track]}` |
| `GET /themes/{id}/tracks/{key}/audio` | th.view | The audio file (FileResponse, `audio/mpeg`, `audio/wav`, ...) |
| `GET /themes/{id}/groups` | th.view | `{"groups": [...]}` (THEME_FORMAT.md 2.5) |
| `GET /categories` | th.view | `{"categories": ["Weather", "Christmas", "Fantasy"]}` |
| `POST /themes/{id}/favorite` | th.edit, **Admin** | toggles favorite |
| `PUT /themes/{id}/rename` | th.edit, **Admin** | `{"name"}`; 409 when another theme has the name (below) |
| `POST /themes/{id}/duplicate` | th.upload, **Admin** | Body `{"name"}`. A full, independent copy (below): `{"status": "ok", "theme_id", "theme": {"id", "name", "folder", "description", "icon", "categories", "is_favorite": false}}`; 409 when another theme has the name, 404 unknown theme |
| `PUT /themes/{id}/tracks/{key}/{presence|muted|volume|playback_mode|seamless_loop|fades|ebb|exclusive}` | th.edit, **Admin** | Changes the theme's own track settings (every channel on it). *(code only)* |
| `POST /themes/create`, `/themes/{id}/upload`, `/themes/import`, `DELETE /themes/{id}`, `PUT /themes/{id}/metadata` | th.upload / th.edit, **Admin** | Theme management; leave it to the web UI |
| `GET /themes/{id}/export` | th.view | zip (section 10) |
| `GET /themes/packages`, `GET /themes/{id}/package`, `GET /themes/{id}/files/{path}` | th.view | Theme packages (section 10) |
| `POST /themes/{id}/render` | admin only | Reference render, WAV (10.3) |

Theme summary (`GET /themes`):

```json
{
  "id": "df297db5-a98d-4372-8029-2c49d1e280e4",
  "name": "Tavern",
  "total_tracks": 8,
  "enabled_tracks": 8,
  "url": "http://192.0.2.25:8008/stream/tavern",
  "description": "A night at a medieval inn.",
  "icon": "🍺",
  "is_favorite": true,
  "has_audio": true,
  "categories": ["Fantasy"],
  "short_file_threshold": 15.0,
  "problems": []
}
```

- **Icons:** `icon` is an emoji string (may be `""`; the web UI then shows
  🎵). There are no icon images for themes. Speaker groups have an `icon`
  such as `"mdi:speaker-group"` (Material Design Icons name).
- `url` is a legacy per-theme stream: **don't play it** (section 9).
- `problems`: strings describing broken `metadata.json`/`presets.json`
  (THEME_FORMAT.md); show a warning badge when non-empty.
- Empty theme folders appear with `has_audio: false`, `total_tracks: 0` and
  no `short_file_threshold`.
- **Theme names are unique:** two names are the same with the spaces around
  them trimmed and case ignored. Create, rename, import and duplicate answer
  409 `{"detail": "A theme with this name already exists"}` for a name
  another theme has; nothing is written.
- **Duplicate** copies the theme folder: audio files and groups,
  `metadata.json` (track and group settings, links, categories, description,
  icon) and `presets.json` (presets and sequences). The copy gets a new id,
  its own folder (named from the new name, as Create theme does) and
  `is_favorite: false`. Linked files stay links to the same theme's files.
  The web UI suggests the name `"<name> 01"`, the lowest free number from 01.

Track object (`GET /themes/{id}/tracks`):

```json
{"name": "fireplace_663", "presence": 1.0, "muted": false, "is_enabled": true, "duration_seconds": 16.2,
 "is_short_file": false, "volume": 1.0, "playback_mode": "continuous", "seamless_loop": false,
 "exclusive": false, "fade_in": 0.0, "fade_out": 0.0, "volume_low": 0.2, "pause_between": false,
 "pause_min": 30.0, "pause_max": 300.0, "rise": 0.0, "group": null, "intrusion_group": false}
```

#### Presets

| Method, path | Perm | Answer |
|---|---|---|
| `GET /themes/{id}/presets` | th.view | below |
| `GET /themes/{id}/presets/{pid}` | th.view | `{"id", "name", "tracks": {key: {...}}, "groups": {...}}` |
| `GET /themes/{id}/presets/{pid}/export` | th.view | the preset as JSON `{"name", "tracks": {...}}` |
| `POST /themes/{id}/presets` | th.edit, **Admin** | `{"name"}` → `{"status": "ok", "preset_id", "name", "is_default"}` (saves the theme's current track settings) |
| `PUT /themes/{id}/presets/{pid}` · `DELETE` · `PUT .../default` · `PUT .../rename` · `POST .../load` · `POST /themes/{id}/presets/import` | th.edit, **Admin** | Preset management *(code only)* |

```json
{
  "theme_id": "df297db5-a98d-4372-8029-2c49d1e280e4",
  "presets": [{"id": "slow_night_at_the_inn", "name": "Slow night at the inn", "is_default": true,
               "track_count": 8, "has_intrusion": false}],
  "default_has_intrusion": false,
  "sequences": [ /* each sequence object plus "choice": "seq:<id>", "has_intrusion" */ ]
}
```

A channel's preset menu = `presets` then `sequences` (by `choice`).

#### Sequences of a theme

| Method, path | Perm | Body / answer |
|---|---|---|
| `GET /themes/{id}/sequences` | pl.view | `{theme_id, sequence_defaults: {hold_min, hold_max, ramp}, sequences: [sequence]}` |
| `GET /themes/{id}/sequences/{sid}` | pl.view | sequence |
| `POST /themes/{id}/sequences` | pl.edit | `{name, presets: [ids], at_end?, hold_min?, hold_max?, ramp?}` → 201 sequence; 400 bad values, 409 duplicate name |
| `PUT /themes/{id}/sequences/{sid}` | pl.edit | any of those fields |
| `DELETE /themes/{id}/sequences/{sid}` | pl.edit | `{"status": "ok", "sequence_id", "stopped_on": [session ids]}` |
| `GET|PUT /themes/{id}/sequence_defaults` | pl.view / pl.edit | `{theme_id, hold_min, hold_max, ramp}` (seconds) |

Sequence: `{id, name, presets, at_end ("reverse"|"restart"|"stop"), hold_min,
hold_max, ramp, owner, transitions, while_steps, effective: {hold_min,
hold_max, ramp}, pass_min, pass_max}`. Timing fields are null when the theme
default applies; `effective` is what plays. (No sequences ship with the
bundled themes, so this shape is from code and PLAYBACK.md.)

Note: `pl.edit` is a User ability, so a User's phone can edit a theme's
sequences.

### 7.3 Playlists

| Method, path | Perm | Body / answer |
|---|---|---|
| `GET /playlists` | pl.view | `{"playlists": [playlist]}` |
| `GET /playlists/{id}` | pl.view | playlist; 404 `"Playlist not found"` |
| `POST /playlists` | pl.edit | `{name, entries, order?, repeat?, play_min?, play_max?, fit_sequences?}` → 201 playlist |
| `PUT /playlists/{id}` | pl.edit | any of those; playing channels continue with the new version |
| `DELETE /playlists/{id}` | pl.edit | `{"status": "ok", "playlist_id", "stopped_on": [session ids]}` |

Playlist:

```json
{"id": "1a2b3c4d", "name": "Evening",
 "entries": [{"theme_id": "a93e...", "preset_id": null, "sequence_id": "gentle_storm"}],
 "order": "in_order", "repeat": true, "play_min": 1800.0, "play_max": 3600.0,
 "fit_sequences": false, "owner": null}
```

`order`: `"in_order"` or `"shuffle"`. `play_min`/`play_max` in seconds
(defaults 1800/3600). 400 bad values, 409 duplicate name. *(Shape from
code; the test server had no playlists beyond an empty list.)*

### 7.4 Speakers

| Method, path | Perm | Answer |
|---|---|---|
| `GET /speakers` | sp.view | speaker[] (flat) |
| `GET /speakers/hierarchy` | sp.view | `{"floors": [{floor, areas: [{area, speakers}]}], "unassigned_areas": [...], "unassigned_speakers": [speaker]}` |
| `POST /speakers/refresh` | sp.view | `{"floors", "unassigned_areas", "unassigned_speakers", "total_speakers"}` (counts; rescans HA and, standalone, the network) |
| `GET /network-speakers` | sp.view | `{"speakers": [...], "total_speakers", "last_scan", "found"}`; 404 on the add-on unless Network speakers is switched on |
| `POST /network-speakers/refresh` | sp.view | `{"total_speakers", "last_scan", "found"}` |
| `POST /speakers/resolve` | sp.view | body = a speaker selection → `{"speakers": [ids], "count"}` (preview for a picker) |
| `GET /speakers/{id}/settings` | sp.view | `{"speaker_id", "settings": {name?, room?, volume_offset?, play_via?}, "speaker": speaker}` |
| `PUT /speakers/{id}/settings` | sp.set, **Admin** | `{name?, room?, volume_offset? (-20..20), play_via?}`; null resets one |
| `POST /speakers/{id}/test` | sp.set, **Admin** | plays a 3 s chime at 20 % → `{"speaker_id", "playing": true, "seconds": 4.0}`; 409 busy, 502 failed |
| `PUT /speakers/{id}/volume` | sp.mute | `{"volume": 0-100}` → `{"speaker_id", "volume"}`; 404 `"Speaker not found"`, 422 out of range, 502 `"The speaker didn't take the change"` |
| `PUT /speakers/{id}/muted` | sp.mute | `{"muted": true}` → `{"speaker_id", "muted"}`; 404, 422, 502 as above |
| `GET /settings/speakers` | sp.view | `{"enabled_speakers": [ids], "hierarchy": {...}}` (Settings › Speakers) |
| `PUT /settings/speakers`, `POST /settings/speakers/enable|disable|enable-all|disable-all` | sp.set, **Admin** | switch speakers on/off |
| `POST /speakers/manual/check`, `POST /speakers/manual`, `DELETE /speakers/manual/{id}` | sp.set, **Admin** | speakers by address (standalone) |

Speaker:

```json
{"entity_id": "net:chromecast:a944ea78-85c9-670a-b8bb-295712de07b1", "name": "Office display",
 "area_id": null, "area_name": null, "floor_id": null, "floor_name": null,
 "ip_address": "192.0.2.10", "source": ["discovered"], "type": "cast", "online": true,
 "address": "192.0.2.10", "original_name": "Office display", "default_area_id": null,
 "volume_offset": 0, "merged": [], "play_via": "ha"}
```

`entity_id` is the speaker id everywhere (HA: `media_player.x`; network:
`net:<type>:<uuid>`). Only speakers in `enabled_speakers` can be picked for
a channel.

**Speaker mute and volume (G6):** `PUT /speakers/{id}/volume` and `/muted`
act on the speaker itself, now (a User's phone may: `sp.mute`). The
channel's `volume` and `speakers_muted` don't change, and the speaker's
`volume_offset` isn't added. A merged speaker is reached the way its
`play_via` says. Home Assistant speakers use HA's `volume_set` and
`volume_mute`; network speakers have no mute of their own, so muting sets
their volume to 0 and unmuting gives back the volume they had (set through
this route; 50 when none is known). Once set this way, `GET /speakers`
lists `"volume"` and `"muted"` for that speaker; changes made elsewhere (the
speaker's own buttons, HA) aren't followed, and a channel starting sets its
speakers to the channel volume as before. In memory: a restart forgets them.

```
PUT /api/speakers/media_player.office/volume {"volume": 25} → {"speaker_id": "media_player.office", "volume": 25}
PUT /api/speakers/media_player.office/muted  {"muted": true} → {"speaker_id": "media_player.office", "muted": true}
GET /api/speakers → [{"entity_id": "media_player.office", "name": "Office", ..., "volume": 25, "muted": true}]
```

### 7.5 Speaker groups

| Method, path | Perm | Body / answer |
|---|---|---|
| `GET /groups` | sp.view | group[] |
| `GET /groups/{id}` | sp.view | group; 404 `"Group not found"` |
| `GET /groups/{id}/resolve` | sp.view | `{"speakers", "count", "summary"}` |
| `POST /groups` | sp.set, **Admin** | `{name, icon?, include_floors?, include_areas?, include_speakers?, exclude_areas?, exclude_speakers?}` → 201 group |
| `PUT /groups/{id}` | sp.set, **Admin** | any of those |
| `DELETE /groups/{id}` | sp.set, **Admin** | 204; 409 when channels use it |

Group: `{id, name, icon, include_floors, include_areas, include_speakers,
exclude_areas, exclude_speakers, speakers, speaker_count, summary,
created_at, updated_at}`.

### 7.6 Floors and areas

| Method, path | Perm | Answer |
|---|---|---|
| `GET /spaces` | sp.view | `{"editable": true, "floors": [{id, name, source, local_id, speakers, areas: [...]}], "unassigned_areas": [...]}` |
| `POST /spaces/floors`, `POST /spaces/areas`, `PUT /spaces/floors/{id}`, `PUT /spaces/areas/{id}`, `DELETE ...` | st.change, **Admin** | the new `/spaces` answer; 404 `"Floors and areas are managed in Home Assistant"` when `editable` is false (add-on) |

`source` is a list of `"ha"` and/or `"local"`; `speakers` is a count.

### 7.7 Settings the app may need

| Method, path | Perm | Answer |
|---|---|---|
| `GET /settings` | ch.view | `{"default_volume": 60, "crossfade_duration": 3.0, "max_groups": 20, "show_in_sidebar": true, "auto_create_quick_play": true, "master_gain": 60, "server_name": ""}` (`server_name`: `""` = "Sonorium") |
| `PUT /settings` | st.change, **Admin** | any of those fields → the same object. `server_name` is trimmed; longer than 40 characters or with control characters: 400 |
| `GET /stream/url` | ch.view | section 9 |
| `GET /install` | ch.view | `{"install", "label", "version", "features"}` |
| `GET /status` | ch.view | `{"version", "current_theme", "themes": [...], "v2_enabled", "sessions", "speaker_groups", "channels", "active_channels", "playlist_sessions"}` |
| `GET /logs` | st.view | the log buffer (Admin) |

`default_volume` is the volume a new channel starts with.

### 7.8 Advanced values

`GET /settings/playback-values` (ch.view) · `PUT` (st.change, **Admin**).

```json
{"values":   {"sparse_min_interval": 180.0, "sparse_max_interval": 1800.0, "sparse_interval_variance": 0.3,
              "group_gap": 120.0, "loop_crossfade": 1.5, "group_crossfade_min": 1.0, "group_crossfade_max": 60.0,
              "track_fade": 6.0, "switch_fade": 3.0, "ebb_long_rest": 0.12, "ebb_turn": 0.35,
              "short_file_threshold": 15.0,
              "sound_max_length": 60.0, "duck_amount": 15.0, "duck_attack": 0.5, "duck_release": 1.5,
              "wake_wait": 20.0, "wake_lead": 1.0, "wake_tail": 1.0},
 "defaults": { "...same keys..." }}
```

Seconds, except chances and randomness as fractions (0.3 = 30 %) and
`duck_amount` in decibels. `sound_max_length` 0 is no limit (7.10). PUT takes
`{key: number | null}` (null = default); 400 for unknown keys, non-numbers,
negatives, chances above 1, or min above max. Pro's on-phone mixer should
read these from the server it downloaded themes from.

### 7.9 Plugins

| Method, path | Perm | Answer |
|---|---|---|
| `GET /plugins` | pg.use or admin.plugins | `[{id, name, version, description, author, enabled, builtin, auth_provider, settings, ui_schema, settings_schema, api_version, compatible, problem}]`: a plugin that can't run here has `compatible: false` and `problem` (why) |
| `GET /plugin-framework` | pg.use or admin.plugins | `{"api_version": "1.1", "capabilities": {name: {since, description}}, "offered": [names], "tts_providers": [...]}` |
| `GET /plugins/{id}` | pg.use or admin.plugins | one plugin |
| `POST /plugins/{id}/action` | pg.use | `{"action": "import", "data": {...}}` → the plugin's result |
| `GET /extensions` | pg.use | `{"pages": [...], "connections": [...]}` |
| `PUT /plugins/{id}/enable|disable`, `GET|PUT /plugins/{id}/settings`, upload, catalog, reload, delete | admin.plugins, **Admin** | management |

A User's phone has neither `pg.use` nor `admin.plugins`: hide Plugins. **Never
offer switching off `users`** (`auth_provider: true`) from the app: it opens
the server to the whole network. The app may simply not show plugins in the
Free MVP.

### 7.10 Announcements, sounds and ramps

Sounds play over a channel's mix: the channel ducks (gets quieter), the
sound plays, the channel comes back. Announcements are admin-level: they
override a zone's owner and quiet hours, and start a stopped channel's
speakers for their length (the channel doesn't show as playing).

| Method, path | Perm | Body / answer |
|---|---|---|
| `POST /announce` | an.send | `{"channels": [id] or "all", "text": "Dinner is ready", "url": "https://.../chime.mp3", "volume": 0-200, "duck": true \| false \| {"db", "attack", "release"}, "provider", "language", "start_stopped": true}` → `{"chime": result or null, "speech": result or null}`. `text` needs a text to speech provider (Sonorium gives Home Assistant's): 502 when it fails. 409 while announcements are off (`features.announcements.enabled` false) |
| `GET /sounds` | ch.view | `{"sounds": [{sound_id, channel_id, name, state, priority, policy, length, position, volume, duck, capped, label, origin}]}` |
| `POST /sounds` | an.send | `{"channels", "url", "volume", "duck", "policy": "now" \| "queue" \| "replace", "priority", "max_seconds", "start_stopped": false, "override": false, "label"}` → result |
| `DELETE /sounds/{sound_id}` | an.send | `{"cancelled": true}`; 404 when it isn't playing or queued |
| `GET /sessions/{id}/ramp` | ch.view | `{"channel_id", "level", "ramp": {id, level, from, to, curve, elapsed, seconds, then} or null}` |
| `POST /sessions/{id}/ramp` | ch.change | `{"to": 0-4, "seconds", "from", "curve": "linear" \| "db" \| "ease" \| "exp", "then": "stop" \| "pause" \| null, "start": false}` → `{ramp_id, from, to, seconds, curve, then}` |
| `DELETE /sessions/{id}/ramp?reset=false` | ch.change | `{"cancelled": bool, "level"}`: holds the level (reset: back to full) |
| `GET /audio-outputs` | ch.view | `{"types": [{id, title, plugin_id, available, message}]}` (output types plugins register) |
| `GET /audio-outputs/{type}/devices` | sp.view | `{"devices": [{id, name, ...}]}` |
| `GET /sessions/{id}/audio-outputs` | ch.view | `{"attachments": [{attachment_id, channel_id, type, device, options, running, latency, error}]}` |
| `POST /sessions/{id}/audio-outputs` | ch.speakers | `{"type", "device", "options"}` → attachment (201); the mix starts if it isn't running |
| `DELETE /sessions/{id}/audio-outputs/{attachment_id}` | ch.speakers | `{"removed": true}` |

A play result: `{"sound_id", "length" (s), "capped" (cut at the longest
sound), "channels": {id: {"state": "playing" | "queued" | "starting" |
"skipped", "message"}}}`. `starting`: a stopped channel is being started
for it; `skipped` says why ("The channel is stopped", "Speakers muted",
"The channel has no speakers", a zone's reason).

A ramp is a gain on the channel's mix, smooth to the sample; the speakers'
volume isn't touched. `db` sounds even to the ear (the default).

Network speakers buffer a few seconds of audio: a sound reaches them that
much later than the stream.

### 7.11 The personal channel

Each account has one personal channel: its own theme, preset, sequence or
playlist, volume and Announcements switch, heard only on that account's own
devices (the app's headphone button). Think of it as a pair of headphones
plugged into the server: the mix is made on the server, every device of the
account hears the same mix, and no speaker ever plays it.

- **Made on first use** (the first `GET /api/me/channel`), with no theme and
  the default volume. It is kept across restarts (state.json,
  `"personal_channels"`).
- **Local only:** it never has speakers. `PUT` with `speaker_group_id` or
  `adhoc_selection` answers 400; `POST /api/sessions/{its id}/play` and every
  other `/api/sessions/{its id}/...` route answer 404.
- **Hidden:** not in `GET /api/sessions` (for anyone, its owner included),
  `GET /api/now-playing`, `GET /api/status`, `/api/stream/url`, Home
  Assistant or MQTT entities, dashboard cards, zones or links. It doesn't
  count towards the 20-channel limit and `POST /api/sessions/stop-all`
  leaves it alone. There is no admin view of other accounts' personal
  channels.
- **Its events** (`channel.listening`, `channel.changed`, `sequence.step`,
  `playlist.*` of a channel, `sound.*`) carry `"personal": true` and go only
  to the event streams of its own account (8.1); plugins don't get them.
- **Announcements:** announcements address channels by id or name, and a
  personal channel has neither from outside: it can't be named. An
  announcement to `"all"` (Home Assistant, MQTT, `POST /api/announce`, a
  plugin) also plays on every personal channel **being listened to right
  now** whose `announcements` is on. One nobody listens to is left out (there
  is nobody to hear it). `POST /api/sounds` (sounds, not announcements) never
  reaches a personal channel.
- **Volume:** stored with the channel for the app to set on its player. The
  stream itself is at full level, as every channel's stream is (a channel's
  volume is its speakers').

#### Whose channel: the account

| Caller | Account |
|---|---|
| A signed-in user, or a phone linked by (or for) a user | That user: `user:<user id>` |
| Nobody signed in (Require login off), a phone with no user, a key, BYPASS_LOGIN | None: 401 `{"detail": "Sign in to use your personal channel"}` |

On the add-on, the Home Assistant user is the
account (a phone linked with an HA username is that user).

#### Which device: one device, counted once

Listening is counted per device, by who calls:

1. a linked phone: its device id (from its `sd_` token);
2. else a signed-in browser: its sign-in session;
3. else a key: the key;
4. else the caller's address.

A device that calls `POST .../listen` again is still one device.
`DELETE .../listen` removes only the device that calls it.

#### Routes

| Method, path | Perm | Body | Answer | Errors |
|---|---|---|---|---|
| `GET /api/me/channel` | ch.view | | the channel object (7.1) plus `personal`, `devices`, `this_device` | 401 no account |
| `PUT /api/me/channel` | ch.change | any of `theme_id`, `preset_id` (`""` none, `"seq:<id>"` a sequence), `sequence_id` (null: stop it), `playlist_id` (null: stop it), `volume` (0-100), `announcements` (bool), `custom_name`; only those sent change | as GET | 400 speakers, 404 unknown sequence or playlist, 409 it can't run (no presets, no entries), 422 bad value, 401 |
| `POST /api/me/channel/listen` | ch.play | none, or `{"hold_seconds": 0-300}` (or `?hold_seconds=`) | `{"session_id", "personal": true, "channel_id", "stream_path", "listen_path", "hold_seconds", "devices"}` | 400 `"No theme selected"` / `"Theme not found"` / bad hold, 409 `"No free channel"`, 401 |
| `DELETE /api/me/channel/listen` | ch.play | none | `{"status": "ok", "removed": bool, "devices": n}` (`removed` false: this device wasn't listening) | 401 |

Extra fields on the channel object:

| Field | Type | Meaning |
|---|---|---|
| `personal` | bool | Always `true` |
| `devices` | int | Devices of the account listening now |
| `this_device` | bool | The calling device is one of them |

`GET /api/me/channel` (captured on a test server, two devices listening):

```json
{
  "id": "88de7e63",
  "name": "Personal",
  "name_source": "custom",
  "theme_id": "df297db5-a98d-4372-8029-2c49d1e280e4",
  "preset_id": "slow_night_at_the_inn",
  "speaker_group_id": null,
  "adhoc_selection": null,
  "volume": 40,
  "is_playing": false,
  "speakers": [],
  "speaker_summary": "No speakers",
  "channel_id": 1,
  "sequence_id": null,
  "preset_choice": "slow_night_at_the_inn",
  "playlist_id": null,
  "sequence": null,
  "playlist": null,
  "speakers_muted": false,
  "announcements": true,
  "listening": true,
  "outputs": [],
  "recent": [{"at": "2026-10-12T03:24:19.609835+00:00", "text": "Theme: Tavern"}],
  "created_at": "2026-10-12T03:24:19.601693",
  "last_played_at": null,
  "personal": true,
  "devices": 2,
  "this_device": true
}
```

`POST /api/me/channel/listen` with `{"hold_seconds": 30}`:

```json
{"session_id": "88de7e63", "personal": true, "channel_id": 1, "stream_path": "/stream/channel1",
 "listen_path": "/stream/channel1?local=1&listener=2607ae52a0aad8de", "hold_seconds": 30.0, "devices": 1}
```

#### Listening

1. `PUT /api/me/channel` with a `theme_id` first (a new personal channel has
   none: listen answers 400 `"No theme selected"`).
2. `POST /api/me/channel/listen`. The mix starts if no device of the account
   listens yet (it fades in); otherwise this device joins the same mix.
3. Play `http://<host>:<ports.http><listen_path>` (9.3: progressive MP3).
   `listen_path` is the shared stream plus this device's own tag
   (`listener=`), so the server can tell when this device's stream closed.
   Every device gets the same `stream_path`; the tags differ. The tag means
   nothing outside this server run; ask again after `POST .../listen`.
4. Stop: `DELETE /api/me/channel/listen`. When the last device leaves, the
   mix fades out and its slot is freed.

A device is also let go `hold_seconds` (default **15 s**, at most 300; each
device its own) after its stream closed, checked every 10 s, as Listen here
does (9.3). A device whose tagged stream never connected counts from its
`POST .../listen`; one that plays `stream_path?local=1` without its tag is
kept while such an untagged stream plays. Settings changed with `PUT` apply
live to what every device hears (a new theme crossfades, a preset glides).

Events: `channel.listening` comes each time the number of listening devices
changes (`devices`), with `listening` false and `playing` false when the
last one left (8.2).

---

## 8. Live updates

### 8.1 The event stream (SSE)

`GET /api/events/stream` · `ev.read` (every phone has it) · HTTPS with the
bearer token · `text/event-stream`.

Query parameters:

| Param | Default | Meaning |
|---|---|---|
| `types` | `*` | Comma-separated patterns: `*`, `channel.*`, `channel.changed`, ... |
| `last_event_id` | | Same as the `Last-Event-ID` header: replay after this id |
| `max_events` | 0 | >0: close after that many events (tests) |

Recommended for the app: `types=channel.*,sequence.*,playlist.*,speaker.*,theme.*,group.*,spaces.*,settings.*,sonorium.started,error`.

A personal channel's events (7.11) reach only the streams of its own
account: the stream's caller (its token) decides, and replay after
`Last-Event-ID` is filtered the same way. A stream opened with the old
events token (no account) never gets them.

Wire format (real capture):

```
retry: 5000

id: 54615c1e5e5d4331a7b9cd713cd45918:8
event: channel.changed
data: {"id": 8, "boot_id": "54615c1e5e5d4331a7b9cd713cd45918", "type": "channel.changed", "time": "2026-10-11T21:03:18.042403+00:00", "origin": "sonorium", "data": {"channel_id": "7c18b15c", "name": "Phone test", "changes": {"volume": 40}}}

id: 54615c1e5e5d4331a7b9cd713cd45918:11
event: channel.stopped
data: {"id": 11, "boot_id": "54615c1e5e5d4331a7b9cd713cd45918", "type": "channel.stopped", "time": "2026-10-11T21:03:22.197558+00:00", "origin": "sonorium", "data": {"channel_id": "7c18b15c", "name": "Phone test", "reason": "stopped"}}

: keep-alive
```

- `id:` is `<boot_id>:<n>` (G4); the JSON keeps `id` as the number `n` and
  adds `boot_id`. Older servers sent a bare `n`.

- A `: keep-alive` comment every 15 s. Treat 45 s of silence as a dead
  connection.
- `origin`: `"sonorium"` or `"plugin:<id>"`.
- At most 20 streams open at once per server (503 `"Too many event streams
  open"`); a slow reader loses the oldest of 500 queued events.
- Errors: 401 `{"detail": "A valid token is needed"}` (no or bad token; note:
  no `pair` flag here, so on 401 check the token with `/api/auth/me`),
  400 bad pattern, 503 plugin system not ready / too many streams.
- OkHttp: use `okhttp-sse` (`EventSources`) with the pinned client; disable
  the read timeout or set it above 45 s.

### 8.2 Event catalogue (version 1)

`GET /api/events/catalogue` returns it live. Channel events use the
**session id** as `channel_id`.

| Event | `data` fields | App reaction |
|---|---|---|
| `sonorium.started` | `version` | Server restarted: reload everything, reset the event id (8.3) |
| `channel.created` | `channel_id`, `name`, `theme_id` | Fetch `GET /sessions/{id}` |
| `channel.deleted` | `channel_id`, `name` | Remove it |
| `channel.started` | `channel_id`, `name`, `theme_id`, `preset_id`, `volume`, `speakers` | Mark playing |
| `channel.stopped` | `channel_id`, `name`, `reason`: `stopped`, `paused`, `idle`, `ended` | Mark stopped |
| `channel.changed` | `channel_id`, `name`, `changes`: any of `theme_id`, `preset_id`, `sequence_id`, `playlist_id`, `volume`, `speakers_muted`, `name`, `speakers` | Apply `changes`, or refetch the channel |
| `channel.listening` | `channel_id`, `name`, `listening` (true when the first listener comes, false when the last one goes), `devices` (how many listen now), `playing` (the channel's mix runs: speakers, listeners or outputs); on a personal channel also `personal: true`, and it comes on every change of `devices` | Set the channel's `listening` (G3); personal channel: its device count |
| `channel.output_added` | `channel_id`, `name`, `output_id`, `kind`, `output_name`, `address` | Add to the channel's `outputs` (G2) |
| `channel.output_removed` | `channel_id`, `name`, `output_id`, `kind`, `output_name`, `address`, `reason`: `removed` (by the app), `expired` (no heartbeat), `replaced` (the same device reported again), `stopped`, `paused`, `idle`, `deleted` | Remove it; if it is the device this phone casts to, stop the Cast session (9.5) |
| `sequence.step` | `channel_id`, `name`, `theme_id`, `sequence_id`, `step`, `steps`, `preset_id` | Refetch `/sessions/{id}/playback` (gives `next_change_at`) |
| `playlist.entry` | `channel_id`, `name`, `playlist_id`, `position`, `entries`, `theme_id` | Same |
| `playlist.started` | `channel_id`, `name`, `playlist_id`, `playlist_name` | A playlist started on the channel (comes after `channel.changed {playlist_id}`) |
| `playlist.stopped` | `channel_id`, `name`, `playlist_id` | The channel stopped its playlist (by hand, another theme, the playlist deleted) |
| `playlist.created` | `playlist_id`, `playlist_name` | Fetch `GET /playlists/{id}` |
| `playlist.changed` | `playlist_id`, `playlist_name`, `changes` (the fields sent: `name`, `entries`, `order`, `repeat`, `play_min`, `play_max`, `fit_sequences`) | Refetch it |
| `playlist.deleted` | `playlist_id`, `playlist_name`, `stopped_on` (channel ids that were playing it) | Remove it |
| `group.changed` | `group_id`, `group_name`, `change`: `created`, `changed`, `deleted` | Refetch `GET /groups` |
| `spaces.changed` | `source`: `registry` (the floors and areas listed changed: added, renamed, moved or removed in Settings › Floors & Areas or in Home Assistant) or `custom_areas` (Settings › Speakers' custom areas) | Refetch `GET /spaces` |
| `settings.changed` | `section`: `general` (`GET /settings`), `playback_values` (`GET /settings/playback-values`), `speakers` (enabled speakers), `speaker` (one speaker's settings, with `speaker_id`), `features` (`GET /install`); `keys`: the settings sent (never their values) | Refetch that section |
| `speaker.added` / `speaker.removed` | `speaker_id`, `name` (+ `type` on added) | Refetch speakers |
| `speaker.unavailable` / `speaker.available` | `speaker_id`, `name` | Grey out / restore |
| `theme.created` | `theme_id`, `folder` | Refetch themes |
| `theme.changed` | `theme_id` (may be null), `folder` | Refetch that theme's presets and sequences; recheck theme packages' `content_hash` (10.4) |
| `theme.deleted` | `theme_id` | Remove it |
| `plugin.enabled` / `plugin.disabled` | `plugin_id`, `name` | If `users`: refetch `/api/info` and `/api/auth/me` |
| `schedule.fired`, `trigger.received`, `error` | see PLUGIN_DEVELOPMENT.md | `error` can be shown as an in-app message |
| `sound.started` / `sound.ended` | `channel_id`, `name`, `sound_id`, `label`, `origin` (+ `reason`, `capped`, `played` on ended) | Show an "announcing" badge on the channel |
| `ramp.started` / `ramp.finished` / `ramp.cancelled` | `channel_id`, `name`, `ramp_id`, levels and `seconds`, `curve`, `then`, `reason` | Optional: show a fade on the channel |
| `command.received` | `plugin_id`, `map_id`, `code`, `actions`, `learned` | ignore |
| `plugin.<id>.<name>` | the plugin's | ignore |

Not announced as events (poll or refetch): a sequence's `next_change_at`
drift; Settings › Connection (saving it restarts the server:
`sonorium.started`). Volume set through Home Assistant or MQTT does produce
`channel.changed`.

### 8.3 Reconnect and replay

- The server sends `retry: 5000`: reconnect after 5 s, with backoff up to
  ~60 s while the server is unreachable.
- Send `Last-Event-ID: <last id seen>` on reconnect: the server replays
  matching events it still holds (the last 200), oldest first, then goes
  live.
- **Event numbers restart at 1 when the server restarts**, so ids carry the
  run: `<boot_id>:<n>` (G4). Send the last id back as it came. Same run: the
  events after `n` are replayed. An id from an earlier run: every event of
  the new run the server still holds is replayed (from `n` = 1), and the
  frames' `boot_id` differs from the stored one: refetch all state, then
  apply events. A bare `n` (older clients) is read as an id of the current
  run.
- Rule for the app: keep `boot_id` from `/api/info`; on reconnect, if the
  first frame's `boot_id` (or `/api/info.boot_id`) differs, refetch
  everything. Servers without `boot_id`: refetch `GET /sessions` once on
  every reconnect.
- If more than 200 events happened while away, replay is incomplete without
  any marker; refetch after a long absence.

### 8.4 Polling fallback

When SSE can't be used (battery saver, widget refresh):

| What | Call | Suggested rate |
|---|---|---|
| Channels and their status | `GET /api/sessions` (includes `sequence`/`playlist` status) | 5 s foreground, never in background |
| One channel's countdown | `GET /api/sessions/{id}/playback` | on demand |
| Speakers online | `GET /api/speakers` | 30 s or on screen open |
| Themes | `GET /api/themes` | on screen open or `theme.*` |
| Server alive | `GET /api/info` (public, cheap) | on resume |

---

## 9. Listening on the phone and casting

### 9.1 Stream URLs

`GET /api/stream/url` · ch.view:

```json
{"base": "http://192.0.2.25:8008",
 "channels": {"7c18b15c": "http://192.0.2.25:8008/stream/channel1"}}
```

- `base` is the direct HTTP address speakers use (Settings › Connection's
  stream URL, or the detected one). Never an ingress URL, in every install.
- **Stream links.** While Require login is on, a stream (`/stream/...`) plays
  for a signed-in caller (cookie, `Authorization: Bearer`, or `?token=`), for
  a caller on the LAN (a private or loopback address; Settings › Advanced ›
  Limits › LAN speakers without a link, on by default), or with a stream
  link: `?t=<expiry>.<signature>`, which the server adds to the addresses it
  hands out that aren't on the LAN (`channels` here, `stream_url` of 9.5, the
  personal channel's `listen_path`). A link works for Stream link time
  (12 h by default) for that path only; play the URL as given, query
  included. Anyone else gets 401 `{"login": true}`. Open streams are limited
  per address and in all (429). `/stream/virtual/...` is its own secret.
- `channels` maps **session id → URL**. Only URLs of the form
  `/stream/channel{N}` are live channel streams. A session without a slot is
  listed with a legacy `/stream/<theme id>` URL: **ignore those**; they play
  the theme's own settings, not the channel.
- The app normally builds the URL itself: `base` + `stream_path` from
  `POST /sessions/{id}/listen`.

### 9.2 Format

| | |
|---|---|
| Path | `GET /stream/channel{N}`, N = 1..max_channels; 404 `Channel N not found` (text) |
| Auth | **None.** Streams are public on both ports, so speakers can play them. |
| Port | HTTP (8008) for the phone and speakers. Also served on 8808, but Cast and most speakers can't use a self-signed HTTPS URL. |
| Codec | MP3 (`audio/mpeg`), MPEG-1 Layer III, 44.1 kHz, **mono**, 128 kbps CBR (measured: 125-126 kbps) |
| Framing | `Transfer-Encoding: chunked`, no `Content-Length`, no ICY metadata, no Range/seek |
| Latency | A new listener joins at the live point with about 1.2 s buffered on the server (50 chunks of 1024 samples) |
| Concurrent listeners | No limit in code; each listener has its own MP3 encoder (CPU per listener). Verified 3 at once, all at full rate. |
| `?local=1` | A "listen here" listener: keeps the mix running without speakers (9.3) and **hears it even while the channel's speakers are muted**. Only for the phone's own player, never for a Cast device (9.4). |

### 9.3 Listening flow (phone)

A channel's mix only runs while its speakers play or someone listens. A
plain `GET` on a slot that isn't playing returns 200 with **zero bytes** and
closes at once.

1. `POST /api/sessions/{id}/listen` with `{"hold_seconds": 120}` →
   `{"channel_id": 1, "stream_path": "/stream/channel1", "hold_seconds": 120}`.
   This starts the mix if the speakers aren't playing (it fades in), and
   takes a slot (409 `"No free channel"` when all are busy). `hold_seconds`
   (0-300, default 15) is how long the mix waits for the phone's stream to
   come back after it drops (G3); the app should send about 120.
2. Play `http://<host>:<ports.http><stream_path>?local=1` with Media3
   ExoPlayer (progressive MP3; set a large `LoadControl` buffer, e.g. 5-10 s,
   for Wi-Fi blips). This needs cleartext HTTP to LAN addresses in the
   network security config.
3. While listening, keep the connection open. Pausing in the app = stop the
   player **and** `DELETE /api/sessions/{id}/listen`.
4. Stop: `DELETE /api/sessions/{id}/listen`. If the speakers aren't playing
   and nobody else listens, the mix fades out and the slot is freed.

Disconnects and reconnects:

- The server counts a `?local=1` listener as gone `hold_seconds` (default
  **15 s**, `LISTEN_RELEASE_SECONDS`) after its last connection closed; it
  checks every 10 s, so the release comes up to 10 s later. Within the hold,
  the app just reconnects to the same URL. With several listeners the
  longest hold asked for counts. Connect right after `POST .../listen`: the
  hold counts from then.
- After longer, or when the GET returns zero bytes / ends immediately, call
  `POST .../listen` again (the slot number may change) and reconnect.
- `channel.listening {listening}` (8.2) is published when the first
  listener comes and when the last one goes.
- If the user pressed **Stop** for the speakers on another device, a channel
  that is also being listened to keeps its mix for the listener.
- A slot whose only listeners are speakers (no `?local=1`) and that nobody
  pulls from for **90 s** is stopped (`channel.stopped`, reason `idle`).
- Every listener restarts at the live point; there's no catch-up.

The phone does **not** appear as a speaker; the channel shows
`"listening": true` instead.

### 9.4 Casting handoff (Free)

The phone tells a Cast device to play the server's stream URL; the audio goes
server to speaker, and the phone tells the server about it (9.5).

1. `POST /api/sessions/{id}/outputs` with the Cast device (9.5). This starts
   the channel's mix if nothing else runs it and answers with `stream_url`.
2. Load `stream_url` into the Cast Default Media Receiver (Media3
   `CastPlayer`; `MimeTypes.AUDIO_MPEG`, stream type LIVE). It is
   `http://<the address speakers use>:<ports.http>/stream/channel{N}`: an IP,
   not `sonorium.local` (Cast devices may not resolve `.local`).
3. Every 60 s while it plays: `PUT .../outputs/{output_id}`. When the user
   stops casting: `DELETE .../outputs/{output_id}`.
4. On `channel.output_removed` for this output (the channel was stopped or
   paused elsewhere, or the device pulled no audio for 90 s), stop the Cast
   session.

**Don't give the Cast device `?local=1`.** The plain URL is the speakers'
stream: it follows the channel's speakers-muted switch, as every speaker
does. `?local=1` is the phone's own listener and ignores that switch (checked
live: with speakers muted the plain stream is silent, `?local=1` is not).
Older servers without outputs (404 on `POST .../outputs`): `POST .../listen`
and the plain URL; the mix then stops once the phone's own `?local=1`
connection has been gone `hold_seconds`.

### 9.5 Reporting "Cast device X plays channel N" (G2)

While an output is reported, the channel's mix runs with or without its
speakers, and it isn't released when the phone's own listener goes. The web
UI shows each on the channel's card with Stop casting (DELETE below).

`POST /api/sessions/{id}/outputs` · ch.play

```json
{"kind": "cast", "name": "Office display", "address": "192.0.2.10", "receiver_id": "a944ea78-85c9-670a-b8bb-295712de07b1"}
```

| Field | Type | Notes |
|---|---|---|
| `kind` | string | Default `"cast"`. A short lowercase word (`[a-z][a-z0-9_-]{0,19}`) |
| `name` | string | The device's name, trimmed to 60. Default `"Cast device"` |
| `address` | string or null | Its IP, up to 100 chars |
| `receiver_id` | string or null | The Cast device id (`CastDevice.deviceId`), up to 100 chars |

The same device reported again (same `receiver_id`, or same `kind` and
`address`), on this channel or another, replaces its earlier report
(`channel.output_removed`, reason `replaced`): a device plays one stream. At
most 10 outputs per channel (400).

Answer 201 (live capture):

```json
{"output_id": "o_c4ad496bead1", "kind": "cast", "name": "Pretend display", "address": "192.0.2.10",
 "receiver_id": "verify-1", "since": "2026-10-11T22:11:21.316793+00:00", "by_device_id": "t_0ddd6ea32a01f2a7",
 "expires_in": 180, "channel_id": 1, "stream_path": "/stream/channel1",
 "stream_url": "http://192.0.2.25:8008/stream/channel1", "heartbeat_seconds": 60, "expire_seconds": 180}
```

- `by_device_id`, `by_device_name`: the linked phone that reported it and
  its name (null for a browser or key). The web UI shows "cast from <name>". `expires_in`: seconds left without a heartbeat.
- Errors: 404 `"Session not found"`; 400 a bad field, `"No theme selected"`,
  `"Theme not found"`; 409 `"No free channel"`.

`PUT /api/sessions/{id}/outputs/{output_id}` (ch.play, no body): the
heartbeat; same answer (with the channel's current `channel_id`). Send it
every `heartbeat_seconds`; an output not heard from for `expire_seconds`
(three missed) is dropped (`reason: expired`) and the PUT answers 404
`"Output not found"`: POST it again.

`DELETE /api/sessions/{id}/outputs/{output_id}` (ch.play) →
`{"removed": true}`; 404 when unknown. The mix fades out and the slot is
freed when nothing else keeps it (speakers, a listener, another output).

`GET /api/sessions/{id}/outputs` (ch.view) → `{"outputs": [output]}`; the
channel object has the same list as `outputs`.

Outputs are dropped when the channel is stopped (`reason: stopped`),
paused (`paused`), deleted (`deleted`), or goes idle because nothing pulled
its audio for 90 s (`idle`). They live in memory: a restart forgets them (a
new `boot_id`); report again.

---

## 10. Theme packages for offline playback

A client that keeps its own copy of a theme (to play it without the server)
downloads it as a **package**: a list of files with their sizes and SHA-256,
then each file on its own. The package is exactly what the zip export holds,
file for file. Code: `sonorium/core/theme_package.py`,
`sonorium/web/theme_package_routes.py`.

### 10.1 What a package holds

- Every file in the theme folder: audio, group subfolders, `ATTRIBUTION.md`,
  `MANIFEST.json`, anything else. Left out: the theme's own `metadata.json`
  and `presets.json` and their backup or broken copies
  (`metadata.json.bak`, `presets.json.broken-…`).
- Each **linked file** (it lives in another theme's folder) at the path it
  has in this theme: `<key><.ext>`, inside its group's folder when it has
  one (`Low/Drone.wav`). Its bytes are the source theme's file.
- `metadata.json` and `presets.json` **as exported**: the 2.0 layout, the
  links dropped (their settings stay under the same keys), written as JSON
  with an indent of 2, UTF-8, non-ASCII characters as they are.

Paths use `/` and are relative to the theme folder.

### 10.2 The content hash

```
content_hash = "sha256:" + hex(SHA-256(UTF-8(lines)))
lines        = for each file, sorted by path: path + "\t" + size + "\t" + sha256 + "\n"
```

- Sorted by path as Unicode code points (the same order as sorting the UTF-8
  bytes; not a locale collator).
- `size`: bytes, decimal, no padding. `sha256`: the file's SHA-256, lowercase
  hex. The result is `sha256:` and 64 lowercase hex digits.

The same files give the same hash on every server; any change to any byte,
any name, any file added or removed gives a new one (settings too: they are
in `metadata.json` and `presets.json`). A copy is current when its hash
equals the listing's.

The server keeps each file's SHA-256 in memory with its size and
modification time and works it out again only when one of them changes, so
the listing reads only what changed since the last time.

### 10.3 Routes

**List every theme.** `GET /api/themes/packages` · th.view

```json
{"packages": [
  {"theme_id": "df297db5-a98d-4372-8029-2c49d1e280e4", "name": "Tavern", "spec_version": 2,
   "content_hash": "sha256:436fcf22b1c30111cc71c47518aaabcaf96703074aa6cb8861ab92005149a160",
   "size_bytes": 4463664, "file_count": 12, "track_count": 8,
   "updated_at": "2026-10-12T12:57:38.441712+00:00",
   "license": {"name": "Creative Commons Sampling Plus 1.0",
               "url": "https://creativecommons.org/licenses/sampling+/1.0/", "requires_attribution": true},
   "source": {"site": "ambient-mixer.com", "url": "https://rpg.ambient-mixer.com/d-d-fantasy-inn-pub-tavern",
              "template_id": "2905", "creator": "", "harvested_at": "2025-12-14T15:09:58.276253"},
   "redistributable": true}
]}
```

- Sorted by name. `track_count`: the theme's audio tracks, linked files
  included. `updated_at`: the newest modification time of the package's
  files (UTC).
- `license` and `source`: the objects in the theme's `MANIFEST.json` as they
  are (THEME_FORMAT.md 6.4), or `null`.
- `redistributable`: `license.redistributable` when `MANIFEST.json` sets it;
  otherwise `true` for Creative Commons and public-domain licences, `false`
  for "All rights reserved" and proprietary ones, `null` (unknown) for any
  other licence or none.

**One theme's package.** `GET /api/themes/{id}/package` · th.view

```json
{"theme_id": "df297db5-a98d-4372-8029-2c49d1e280e4", "name": "Tavern", "spec_version": 2,
 "content_hash": "sha256:436fcf22b1c30111cc71c47518aaabcaf96703074aa6cb8861ab92005149a160",
 "size_bytes": 4463664,
 "files": [
   {"path": "ATTRIBUTION.md", "size": 1619, "sha256": "441f840fa0cca87759037157b831b01e6ec7542975becd5a2c34858d7cb1ba71"},
   {"path": "fireplace_663.mp3", "size": 259951, "sha256": "07820aba53a09bb4a541dd4b0ae7b9d73e50ff29a2f907e7b41b9eeed3a783e7"},
   {"path": "metadata.json", "size": 6596, "sha256": "718483a1d27d9609834f9ec4c88881a798dd2c2fc270d27458bc59eff6b10d6e"},
   {"path": "presets.json", "size": 1942, "sha256": "fa29b7180811abca1017a5a333e82fbd8b2b4dddb77711920fa392180282fdfd"}
 ],
 "metadata": {"spec_version": 2, "id": "df297db5-…", "name": "Tavern", "tracks": {…}, "groups": {…}, …},
 "presets": {"presets": {…}},
 "playback_values": {"sparse_min_interval": 180.0, "sparse_max_interval": 1800.0, "group_gap": 120.0, …}}
```

(`files` shortened: Tavern has 12.) `files` is sorted by path. `metadata`
and `presets` are the exported documents, the same content as the
`metadata.json` and `presets.json` files. `playback_values`: the server's
Settings › Advanced values now (every key, as `GET
/api/settings/playback-values` `values`; 7.8); store them with the package
so the copy plays as it does on this server.

**One file.** `GET /api/themes/{id}/files/{path}` · th.view

- `{path}` is a `path` from `files`, URL-encoded per segment
  (`Lute/Lute%20song%201.mp3`). Anything not in the list is 404; the path is
  only ever looked up in the list, so it can't reach other files.
- Answers: `ETag: "<sha256>"`, `Content-Length`, `Accept-Ranges: bytes`,
  `Cache-Control: no-cache`; `Content-Type` by extension (`audio/mpeg`,
  `audio/wav`, `audio/flac`, `audio/ogg`, `application/json`, `text/markdown`,
  else `application/octet-stream`).
- `Range: bytes=a-b`, `bytes=a-`, `bytes=-n`: 206 with `Content-Range`;
  a range past the end: 416 with `Content-Range: bytes */<size>`. Several
  ranges in one header: the whole file (200).
- `If-Range: "<sha256>"` with a Range: the range only while the file is
  still that one, else the whole file (200). `If-None-Match: "<sha256>"`:
  304 while unchanged.
- To resume after a drop: `Range: bytes=<bytes you have>-` with `If-Range`
  set to the ETag of the first part. Check every file's SHA-256 against the
  list when it is complete.

**The zip, for people.** `GET /api/themes/{id}/export` · th.view · answer
`application/zip`, `Content-Disposition: attachment; filename="<folder>.zip"`,
`Content-Length`. Holds `<folder>/<path>` for every file of the package.
Written to a temporary file first (deleted after sending), so its size is
known and the server doesn't hold the theme in memory. The theme is found
in the configured audio folder on every install.

**Reference render.** `POST /api/themes/{id}/render` · admin only (perm
`admin.plugins`: an Admin, an Admin's phone, the console credential; never a
User or a key)

```
{"preset_id": "busy", "seconds": 30, "seed": 42}     → 200 audio/wav, Content-Length
```

- The theme mixed by `sonorium.engine` (docs/ENGINE.md): mono, 44 100 Hz,
  16-bit PCM WAV, `seconds × 44100` samples. Same theme files, values, preset
  and seed: the same bytes every time.
- `preset_id`: omitted or `null` = the theme's default preset; `""` = no
  preset. `seconds`: 1 to 120 (default 30). `seed`: integer (default 0).
  `master_gain`: optional, 0-100 (default 60, the out-of-the-box Master
  output gain). Playback values: the server's current Settings › Advanced
  values (the package's `playback_values`).
- A downloaded copy rendered with the engine, the package's
  `playback_values` and the same preset, seed and master gain gives the same
  bytes: compare a client's own mixer with it.
- 400 bad body; 404 unknown theme or preset; 422 a theme with no audio;
  429 with `Retry-After` past the limit (11.2) or while another render runs.

### 10.4 Knowing when to download again

Compare each theme's `content_hash` in `GET /api/themes/packages` with the
stored copy's. Check again on these events (8.2):

- `theme.changed` (`theme_id`, `folder`): settings or presets saved, a file
  uploaded (also over an existing one), a group or track moved, a link added
  or removed. Recheck that theme.
- `theme.created`: a new theme. `theme.deleted`: gone.
- `sonorium.started`, and after reconnecting the event stream (8.3): recheck
  every theme (files copied into a theme folder by hand while the server
  runs announce nothing).

A linked file changes with its source theme: a `theme.changed` for the
source can change the hash of every theme linking to it, so recheck the
listing as a whole.

### 10.5 Licence and source

From the theme's `MANIFEST.json` (optional): `source` {`site`, `url`,
`template_id`, `creator`, `harvested_at`} and `license` {`name`, `url`,
`requires_attribution`, optional `redistributable`} (THEME_FORMAT.md 6.4),
in the listing as above. `ATTRIBUTION.md` (optional, human-readable) and
`metadata.json` `attribution` / `description` (free text) come with the
files. The three bundled themes are CC Sampling Plus 1.0
(ambient-mixer.com).

Theme format: [THEME_FORMAT.md](THEME_FORMAT.md) (spec v2,
`spec_version: 2`). Preset JSON on its own: `GET
/api/themes/{id}/presets/{pid}/export`.

---

## 11. Errors, rate limits, versions

### 11.1 Error format

FastAPI's: `{"detail": "<message for the user>"}` with an HTTP status.
Validation errors (422) have a list:

```json
{"detail": [{"type": "less_than_equal", "loc": ["body", "volume"], "msg": "Input should be less than or equal to 100", "input": 200, "ctx": {"le": 100}}]}
```

Flags the sign-in layer adds to error bodies:

| Status | Body | Meaning for the app |
|---|---|---|
| 401 | `"pair": true` | Token invalid: relink (4.6) |
| 401 | `"login": true` (`"setup": true`) | No token sent (or sign-in needed): the app shouldn't see this once linked |
| 403 | `{"detail": "Only an admin can do this"}` | The phone's user is a User |
| 403 | `{"detail": "Not allowed"}` | Never allowed for a phone (e.g. `/api/devices`), or an unknown `/api/...` route (the server refuses API routes it has no permission for, rather than 404) |
| 403 | `"reset": true` | (Browsers only) choose a new password first |
| 503 | `{"detail": "Sonorium is starting", "starting": true}` | Retry in a few seconds |
| 503 | `{"detail": "Home Assistant didn't answer. Try again in a moment."}` | (Add-on) sign-in with an HA username: HA couldn't check the password |
| 503 | `{"detail": "Sign-in didn't start. See Logs.", "closed": true}` | Server misconfigured; show the message |
| 404 | `{"detail": "Not Found"}` | Route of a plugin that's off (e.g. `/api/pair/login` without Users) |

Exceptions to watch:

- Older theme, preset, category and channel routes in `api.py` used to
  answer **200 with `{"error": "..."}`**. They now answer an error status
  (G10): 404 `"... not found"`, 409 `"... already exists"`, 503 `"... not
  initialized/available"`, 500 `"Failed ..."`, else 400, with the body
  `{"detail": msg, "error": msg}`. Older servers: still check for an `error`
  key on 200 answers from `/themes/...`, `/categories` and `/channels/...`.
- `GET /stream/channel{N}` errors are plain text (404 `Channel N not found`,
  503 `Channel system not initialized`).
- 409 is used for conflicts: duplicate names, nothing to skip, a busy
  speaker test, no free slot, a group in use.

### 11.2 Rate limits

| What | Limit | Key | Answer |
|---|---|---|---|
| `POST /api/pair` | burst 5, then 1 per 12 s; **every** attempt counts, successful or not | client IP | 429, `Retry-After: <s>` |
| `POST /api/pair/login` (and browser sign-ins) | 5 failures, then 1 per 12 s; successes don't count | client IP | 429, `Retry-After` |
| `POST /api/devices/recovery-code` | burst 3, then 1 per minute | phone | 429, `Retry-After` |
| `POST /api/themes/{id}/render` | burst 3, then 1 per 20 s; one render at a time on the server | user, phone or key, else client IP | 429, `Retry-After` |
| `GET /api/events/stream`, `GET /api/timeline/stream` | 20 open streams per server; Event streams per user (8) per user, phone or key | | 503; 429 |
| Failed sign-ins (`/api/auth/login`, `/api/pair/login`, `/api/auth/code-login`) | also Sign-in tries per username (10 a minute), from any address | username | 429, `Retry-After` |
| `GET /stream/...` | Streams per address (16), Streams in all (64) | client IP | 429 |
| `GET /api/themes/{id}/export` | Exports at once (2) | | 429 |
| Request bodies | Sign-in request size (16 KB) for `/api/auth/*`, `/api/pair`, `/api/link`; Request size (1 MB) for the rest; Upload size (2048 MB) for uploads, theme and plugin zips and backup files | | 413 |
| Everything else | none | | |

The limits in brackets are defaults: Settings › Advanced › Limits (`GET/PUT
/api/settings/playback-values`; 0 is no limit). Limits are in memory and
reset when the server restarts. Behind a reverse proxy the client IP is the
proxy's unless it is listed in Trusted proxies (Settings › Advanced ›
Network, or `SONORIUM__TRUSTED_PROXIES`): then it is the caller's, from
`X-Forwarded-For`, and `X-Forwarded-Proto` tells HTTP from HTTPS.

### 11.3 Versioning and compatibility

- `GET /api/info` → `api` (integer, currently `1`, `API_VERSION` in
  `sonorium/auth/routes.py`). It changes only for breaking changes to routes
  the app uses. Additions (new fields, new routes, new events) don't bump it.
- The app declares the API versions it supports. `api` higher than the app
  knows: "Update the Sonorium app". Lower: "Update Sonorium".
- `/api/info` missing (404 on HTTP 8008, or nothing on 8808): a server
  before 1.6.2, without sign-in or HTTPS. Not supported; ask the user to
  update. (Those servers had no authentication; the app must not fall back
  to plain HTTP control.)
- Feature detection inside `api: 1`: check for fields and routes, never
  parse `version`. Examples: `hostname` in `/api/info` (since 1.6.2-dev.22),
  `hosts` in `/api/link`, `plugins` contains `users`.
- Unknown JSON fields must be ignored; unknown event types too
  (`CATALOGUE_VERSION` is 1 and only grows).
- Legacy QR form `sonorium://pair?` (1.6.2-dev.21 and earlier dev builds)
  must stay accepted.
- Event names, error flags (`pair`, `login`, `starting`) and the token
  prefix `sd_` are part of the contract.

---

## 12. The app's interface: the same UI as the server

The app (Free and Pro) shows the server's web UI unchanged, hosted in a
WebView. Free points it at a Sonorium server's API; Pro points it at a server
when one is found, or at the on-phone engine when none is. A few parts stay
native (12.5).

### 12.1 What the UI is

| File | Lines | What |
|---|---|---|
| `sonorium/web/templates/index.html` | 1276 | The whole page: sidebar, phone top bar, every view and dialog as static HTML |
| `sonorium/web/static/js/api.js` | 32 | `BASE_PATH` and `api(method, endpoint, body)` |
| `sonorium/web/static/js/slots.js` | 349 | `window.Sonorium`: `/api/auth/me`, plugin UI bundles, the global `fetch` wrapper (CSRF, 401 reload), Link App dialog, menu permissions; loads app.js |
| `sonorium/web/static/js/app.js` | 10355 | Everything else: Channels, Theme Editor, playlists, speakers, settings pages |
| `sonorium/web/static/js/logs.js` | 142 | The log viewer (Settings › Logs and `/logs`) |
| `sonorium/web/static/css/styles.css` | 4210 | All styles and the design tokens (12.6) |
| `sonorium/web/static/js/ext/users.js`, `zones.js`, `static/css/users.css`, `zones.css` | | Sign-in screens, Users & devices, Zones (loaded by `slots.js` with the other `ext/` scripts; `/api/ui/plugins` is now empty) |
| `sonorium/web/static/js/ext/keys.js`, `static/css/keys.css` | | Settings › Keys (hidden while `features.keys.enabled` is false) |
| `/logo.png`, `/favicon.png` (server routes), `static/img/qr-logo.png` | | Images |
| `static/ha/sonorium-cards.js` | | Home Assistant dashboard cards; not part of the UI |

Icons are inline SVG in the HTML and JS; there's no icon font or image set.

**No server-side templating.** `web_ui()` (`api.py:1004`) returns
`index.html` read as text, unchanged. The page works out its own base path
at run time: the inline script at `index.html:7-25` sets
`window.SONORIUM_BASE` from `location.pathname` (for HA ingress), then adds
the stylesheet and favicon with that prefix; `index.html:1242-1273` loads
`logs.js`, `api.js`, `slots.js`, which loads the plugin bundles and then
`app.js`. The logo is set from `SONORIUM_BASE` (`index.html:39`, `:47`). So
the same files can be packaged as they are; only the base and the transport
need to change (12.2).

**Breakpoints** (`styles.css`): `max-width: 760px` is the phone layout (15
blocks: phone top bar and dropdown menu, table rows become cards, full-screen
dialogs and help); `768px` (header wraps, 44 px touch targets), `600px` and
`640px` (small phones), `900px`/`1000px`/`1100px` (narrow desktop and
tablet), `min-width: 761px`/`769px` (desktop-only rules), and
`prefers-reduced-motion`. A phone in portrait gets the 760 px layout; a
tablet in landscape gets the desktop layout with the sidebar.

### 12.2 How every request reaches the server today

All relative to `BASE_PATH` / `SONORIUM_BASE` (same origin), authenticated by
the `sonorium_session` cookie, with `X-CSRF-Token` added to changes.

`fetch` calls (all pass through the `slots.js` wrapper, 12.2.1):

| File:line | Request |
|---|---|
| `api.js:19-20` | `api()`: `${BASE_PATH}/api${endpoint}`, JSON. Used by almost every screen. Throws on non-2xx and on a 200 body with `error` |
| `slots.js:70` | `GET /api/auth/me` (`credentials: 'same-origin'`), retried every 2 s while `starting` |
| `slots.js:84` | `GET /api/ui/plugins` |
| `app.js:4155` | `POST /api/themes/{id}/upload` (multipart) |
| `app.js:5213` | `GET /api/themes/{id}/export` (zip, saved through a blob link) |
| `app.js:5264` | `POST /api/themes/import` (multipart) |
| `app.js:7709` | `POST /api/plugins/upload` (multipart) |
| `app.js:9211` | `GET /api/themes/{id}/tracks/{key}/audio` (decoded with WebAudio for the transition preview) |
| `logs.js:49` | `GET /api/logs?after=` (polled) |
| `users.js:67`, `:103` | `ext/users.js`'s own `fetch` for sign-in calls and `/api/auth/me` |

Not `fetch` (the wrapper doesn't see them):

| File:line | Request | Kind |
|---|---|---|
| `app.js:831` | `new Audio(BASE_PATH + stream_path + '?local=1')` | **Listen here**: the channel stream |
| `app.js:4841`, `:4982-4984` | `new Audio(.../tracks/{key}/audio)` | Track preview |
| `app.js:5100-5102` | `new Audio(BASE_PATH + '/stream/' + themeId)` | Theme preview (legacy per-theme stream) |
| `app.js:9982`, `:10001`, `:10030` | `new Audio(BASE_PATH + '/stream/' + themeId + '?preview=')` | Theme Editor mix preview |
| `app.js:554` | `<a href=".../api/logs/download">` | Download |
| `app.js:5902` | `<a href=".../api/ha-cards/download">` | Download |
| `app.js:600` | `SonoriumLogs.mount(el, BASE_PATH)` | Passes the base to logs.js |
| `index.html:16`, `:23`, `:39`, `:47`, `:1249`; `slots.js:60`, `:89-96` | stylesheet, favicon, logo, scripts, plugin bundles | Static files |
| `slots.js:287`, `users.js:107`, `:117`, `:128`, `:297`, `:330`, `app.js:7929` | `window.location.reload()` | Reload after sign-in changes |

There's no EventSource: the UI polls (`app.js:8204` channels every 15 s,
`app.js:8205` a 1 s countdown, `app.js:6339` the speaker scan line).

The calls the UI made to routes this server doesn't have (`POST
/api/heartbeat` every 3 s, `/update/*`, `/settings/audio-device(s)`,
`/network-speakers/enabled` and `/{id}/play|stop`, left from the Windows
app) are gone (G13); `tests/test_web_ui_routes.py` fails if the UI calls a
route without a permission. Line numbers in this section are from
1.6.2-dev.23, before that change.

#### 12.2.1 What has to become configurable

1. **API base URL.** In the app: the loopback origin (12.3), or
   `https://<server>:8808` if the bridge forwards calls.
2. **Auth.** `Authorization: Bearer sd_...` instead of the cookie and CSRF.
   With a bearer token the server skips the CSRF check, and the wrapper only
   adds `X-CSRF-Token` when `/api/auth/me` returned one (a phone gets
   `csrf: null`); the rest stays as it is.
3. **Stream URLs stay `http://<server>:<ports.http>`**: the channel stream
   and theme previews (`/stream/...`) are plain HTTP on 8008 and need no
   token. Track audio is an API route (`th.view`) and goes with the API calls.
4. **Listen here** must go to the native player (12.5), not an `<audio>`
   element in the WebView, so it keeps playing with the screen off.
5. **Downloads** (`<a href>` and blob links) need a native handler.
6. **401 reload** (`slots.js:283-290`): a reload doesn't help a phone. On a
   401 with `pair: true` the host must take over (relink screen).

#### 12.2.2 The host hooks (G14, built)

One optional object the app injects before any script runs
(`WebViewCompat.addDocumentStartJavaScript`). In a browser it doesn't exist
and the UI behaves exactly as before. Contract version 1:
`GET /api/info` → `ui.host_api: 1` (`SONORIUM_HOST_API` in `static/js/api.js`).

```js
window.SONORIUM_HOST = {
  mode: 'server',                        // informational: 'server' | 'offline'
  baseUrl: '',                           // prefix for /api, /static, /plugin-ui, /logo.png ('' = same origin, the loopback proxy)
  streamBase: 'http://192.0.2.20:8008',// prefix for /stream/... (Listen here, theme previews); unset: baseUrl
  authHeader() { return null; },         // e.g. {Authorization: 'Bearer sd_...'}; null when the proxy adds it
  // each of these is optional; without one the page does what a browser does
  listen(sessionId, url, info) {},       // Listen here: url = streamBase + stream_path + '?local=1';
                                         //   info = {name, themeName, channelId, volume (0-100)}
  stopListening(sessionId) {},           // Listen here off (the page also sends DELETE .../listen)
  setListenVolume(sessionId, volume) {}, // the card's volume slider while listening (0-100)
  download(url, filename) {},            // absolute URL; filename '' = take it from Content-Disposition
  unlinked(body) {},                     // a 401 with {"pair": true} from the server (called once)
  server: { id: '...', name: 'Living room', address: '192.0.2.20' },
                                         // the server the app is connected to: name = what the user named it
                                         //   in the app (null when not named), address = the host it connects to
  showServers() {},                      // the server button and Settings › Servers: show the app's server list
  on(event, handler) {},                 // the page registers: 'listening' {sessionId, playing}, 'refresh'
};
```

What the page does with it:

| Where | With the hook | Without (browser) |
|---|---|---|
| `index.html` head script | `SONORIUM_BASE = baseUrl` (trailing `/` dropped) | from the page's path (HA ingress) |
| `slots.js` fetch wrapper | calls to the page's origin or `baseUrl`'s origin get `authHeader()`; a 401 `{"pair": true}` calls `unlinked(body)` instead of reloading | CSRF header, reload on a lost sign-in |
| Listen here (`app.js` `chToggleListen`) | `POST .../listen`, then `listen(sessionId, url, info)`; the button shows on; off calls `stopListening(id)` and `DELETE .../listen` | `new Audio(url)` |
| Theme preview, Theme Editor mix preview | `streamBase + /stream/<theme>` | `BASE_PATH + /stream/<theme>` |
| Log download, theme export, HA cards download | `download(url, filename)` | `<a href>` / blob link |
| `on('listening', {sessionId, playing})` | the card's Listen button follows the native player (e.g. paused from the lock screen) | |
| `on('refresh')` | reloads the channels and redraws | |
| Server button (`server` and `showServers()` both set) | a green button labelled `server.name`, or `server.address` when the name is null; a long label ends in "…" (the full label is its tooltip). Phone: right end of the top bar; short screens (height ≤ 480): last in the one-row header; desktop: last in the page header, after New Channel and ?. A click closes the menu and calls `showServers()` | hidden |
| Menu, Settings › Servers (the last Settings item; same condition) | shown; a click closes the menu and calls `showServers()` | hidden |

Both the button and Settings › Servers carry `data-host="servers"`: when the
page has an element with that attribute, the app doesn't add its own Servers
item.

When the native player stops by itself, the app sends `DELETE
/api/sessions/{id}/listen` itself and tells the page with
`listening {playing: false}`.

Not routed through the hooks: track preview audio (`/api/themes/.../audio`,
an API route: it goes to `baseUrl` like other API calls, so with a direct
`baseUrl` it needs the proxy for its token), the plugin bundles (from
`baseUrl`), and `logs.html` (a separate page).

Checked by `tests/web_host_check.mjs` (run by `tests/test_web_host.py`).

### 12.3 Packaging in the app

**Transport (recommended): a loopback proxy in the app.**

The app runs a small HTTP server on `127.0.0.1:<random port>` (Ktor or
NanoHTTPD) inside its foreground service, and the WebView loads
`http://127.0.0.1:<port>/`.

- Server mode: `/api/*`, `/plugin-ui/*`, `/logo.png`, `/favicon.png` are
  forwarded to `https://<server>:8808` with the app's **pinned OkHttp
  client**, adding `Authorization: Bearer` and stripping cookies. `/stream/*`
  is forwarded to `http://<server>:8008` (or the UI uses `streamBase`
  directly). `/static/*` and `/` come from the chosen UI copy (below).
- Offline mode (Pro): the same port is answered by the on-phone engine
  (12.4). The UI can't tell the difference.
- Why not `shouldInterceptRequest`: WebView doesn't give the request body
  of POST/PUT to `shouldInterceptRequest`, so it can't forward API writes.
  A JS bridge that replaces `fetch` works too, but misses `<audio>`, `<img>`
  and downloads and needs base64 for uploads. The proxy covers everything,
  including multipart uploads.
- Lock it down: bind to 127.0.0.1 only; require a per-launch secret on every
  request (a cookie set with `CookieManager` for that origin before loading,
  checked by the proxy), since other apps on the phone can reach loopback.
- Pinning lives in OkHttp (section 2.1); WebView never talks to the server
  directly, so its lack of pinning doesn't matter.

**Which copy of the UI: hybrid.**

| Option | For | Against |
|---|---|---|
| Bundled in the APK | Works offline; app version pinned; reviewed by Play | Can mismatch a newer or older server; plugin bundles (`/api/ui/plugins`) come from the server |
| Loaded from the server | Always matches the server's API and its plugins | Nothing offline; UI changes ship without an app update |

Recommendation:

- **Server mode: load the server's own UI** through the proxy (`/`,
  `/static/...`), when `/api/info.api` is one the app supports. It always
  matches the server's API and its plugin bundles, and needs no version
  logic.
- **Bundled copy**: for offline Pro, and as the fallback in server mode when
  the server's UI doesn't load (network error, or an `api` the app doesn't
  know). The bundle carries the server version it was taken from.
- The app needs the hooks in 12.2.2 to be in the server's copy: use the
  server's UI when `/api/info.ui.host_api` is a version the app supports,
  else (no `ui` field, or an unknown `host_api`) the bundled copy.

**WebView settings** (androidx.webkit):

- `javaScriptEnabled = true`, `domStorageEnabled = true` (the UI keeps the
  current view in `localStorage`).
- `allowFileAccess = false`, `allowContentAccess = false`; no
  `file://` URLs. `setSafeBrowsingEnabled(true)`.
- `mediaPlaybackRequiresUserGesture` doesn't matter: audio is native.
- `shouldOverrideUrlLoading`: stay on the loopback origin; open anything
  else in the browser.
- Inject `SONORIUM_HOST` with `WebViewCompat.addDocumentStartJavaScript`,
  limited to the loopback origin, and talk to it with
  `WebViewCompat.addWebMessageListener` (origin-checked), not
  `addJavascriptInterface`.
- Network security config: cleartext allowed for `127.0.0.1` and the
  server's LAN address (streams).
- `WebViewAssetLoader` is the alternative for serving the bundled copy
  (`https://appassets.androidplatform.net/...`), but then API calls need the
  bridge rather than the proxy; with the proxy, serve the bundle from it.

### 12.4 Pro offline mode

The on-phone engine answers the same routes the UI calls, on the loopback
port. Minimum surface, by page (from `app.js` `init()` at `app.js:29-50` and
each screen's calls):

| Page | Routes |
|---|---|
| Start (every page) | `GET /api/auth/me`, `GET /api/ui/plugins` (return `{"plugins": []}`), `GET /api/sessions`, `/api/themes`, `/api/categories`, `/api/speakers/hierarchy`, `/api/groups`, `/api/settings/speakers`, `/api/channels`, `/api/settings`, `/api/status`, `/api/plugins`, `/api/install`, `/api/playlists`, `/api/settings/playback-values`, `/api/connection` (404 offline) |
| Channels | `POST/PUT/DELETE /api/sessions[/{id}]`, `.../play`, `.../stop`, `.../listen` (POST, DELETE), `.../speakers-muted`, `.../next`, `PUT/DELETE .../playlist`, `GET /api/themes/{id}/presets` |
| Theme Editor | `GET /api/themes/{id}/tracks`, `/groups`, `/presets`, `/presets/{p}`, `/sequence_defaults`, `/sequences/{s}/while/{p}`, `/transition_tracks`; `PUT /api/themes/{id}/metadata`, `/rename`, `/sequence_defaults`, `/presets/{p}[...]`, `/sequences/{s}[...]`; `POST /api/themes/{id}/presets`, `/presets/{p}/load`, `/presets/import`, `/groups`, `/tracks/reset`, `/sequences`, `/favorite`, `/categories`; `DELETE` presets and sequences; track audio and `/stream/{theme}?preview=` |
| Themes list | `POST /api/themes/refresh`, `/themes/create`, `/themes/import`, `/themes/{id}/upload`; `DELETE /api/themes/{id}`; `GET /api/themes/{id}/export` |
| Playlists | `GET/POST/PUT/DELETE /api/playlists[/{id}]` |
| Speakers | `POST /api/speakers/refresh`, `GET/POST /api/network-speakers[/refresh]`, `POST /api/speakers/{id}/test`, `PUT /api/speakers/{id}/settings`, `POST /api/settings/speakers/enable-all|disable-all`, `POST /api/speakers/manual[/check]`, `DELETE /api/speakers/manual/{id}` |
| Groups | `GET/POST/PUT/DELETE /api/groups[/{id}]` |
| Floors & Areas | `GET /api/spaces`, `POST/PUT/DELETE /api/spaces/...` |
| Audio, Advanced | `PUT /api/settings`, `PUT /api/settings/playback-values`, `PUT /api/install/features` |

No meaning offline:

| Feature | How the UI hides it today |
|---|---|
| Settings › Connection (HA and MQTT) | Its menu item shows only when `GET /api/connection` succeeds (`app.js:5805-5813`). Offline: 404. |
| Home Assistant (floors and areas from HA, HA speakers, dashboard cards) | Floors and areas are editable when `features.space_editing.enabled` (`app.js:5410`, from `GET /api/install`, i.e. `runtime.features()`). Offline: `space_editing` on, `connection_settings` hidden; `/api/ha-cards` 404 (the Cards card shows its "not connected" state). |
| Users, keys, linked devices, Link App | Built-in plugin pages, loaded from `GET /api/ui/plugins`; menu items are hidden by `perms` from `/api/auth/me` (`slots.js:29-33` `NAV_PERMS`). Offline: no plugin bundles, and `/api/auth/me` returns `{"kind": "local", "perms": [...all but admin.users, admin.keys, self...], "plugins": [], "csrf": null, ...}`. |
| Plugins (install, catalog) | Settings › Plugins needs `admin.plugins`: leave it out of offline `perms`. |
| Logs | `st.view`; the engine may serve its own log, or leave it out of `perms`. |

`runtime.features()` and `/api/install.features` are the switch for
install-specific features; offline adds no new flags if the engine reports
`install: "android"` with `connection_settings: hidden`, `space_editing: on`,
`network_speakers: on`.

Two ways to build the engine:

| | (a) Kotlin port behind the loopback server (PLANdroid.md) | (b) Sonorium's Python backend on the phone (e.g. Chaquopy) |
|---|---|---|
| Behaviour | Must reproduce the mixer and every route above; the golden render test (P5) keeps it honest | Same code: routes, mixer, sequences, playlists match by construction |
| Size | Small | Python runtime + numpy + PyAV (FFmpeg) + FastAPI/uvicorn: tens of MB per ABI; check APK/AAB limits per ABI split |
| Battery and CPU | MediaCodec decoding, native mixing | numpy mixing in Python at 44.1 kHz per track, PyAV encode per listener; needs measuring on a mid-range phone |
| Feasibility checks | Effort: a large port | PyAV and numpy wheels for Android (Chaquopy ships numpy; PyAV/FFmpeg would need building), `zeroconf`/`pychromecast` on Android, file paths, background limits, Play policy on downloaded code (none: the code is in the APK) |
| Speakers | Cast via Google's SDK; DLNA/Sonos/HEOS ported | Sonorium's own discovery and players, if their libraries run on Android |

### 12.5 Native and web parts

Native: media session and lock-screen/notification controls, background
playback (`MediaSessionService` with ExoPlayer for Listen here), the Cast
button and route picker (Media3 `CastPlayer`), the QR scanner, the
biometric prompt (section 5), Play Billing, server discovery, linking and
the server list. The WebView shows everything else.

Messages through the bridge (`addWebMessageListener`, JSON):

| Direction | Message | When |
|---|---|---|
| web → app | `listen {sessionId, url, name, themeName}` | Listen here pressed |
| web → app | `stopListening {sessionId}` | Listen here off |
| web → app | `cast {sessionId, url}` | A Cast action in the channel menu (if added) |
| web → app | `download {url, filename}` | Export, log download |
| web → app | `unlinked {detail}` | 401 `pair` |
| web → app | `showServers`, `showSignInCode` | Menu items the app adds (server list, sign-in code) |
| app → web | `listening {sessionId, playing}` | Native player state, so the card's Listen button stays right (also after lock-screen pause) |
| app → web | `refresh` | After resume, a server switch, or an event from the app's own SSE connection |
| app → web | `server {name, mode}` | Server switched or went offline (Pro) |

The app keeps one SSE connection (section 8) for the media notification
(channel name, preset, sequence step) and passes `refresh` to the web UI
when a channel changes, so the UI doesn't wait for its 15 s poll.

### 12.6 Design rules for the native parts

Read [UI_STYLE.md](UI_STYLE.md) before any native screen. In short:

- **Colours** (`styles.css:3-18`, dark only): background `#1a1a2e`,
  secondary background `#16213e`, cards `#1f2937` (hover `#283548`),
  sidebar and top bar `#0f172a`, text `#f8fafc`, secondary text `#94a3b8`,
  muted text `#64748b`, accent `#06d6a0` (primary buttons, active states;
  hover `#05c090`), second accent `#8b5cf6`, warning `#f59e0b`, danger
  `#ef4444`, borders `#334155`. No new colours.
- **Type**: the system sans-serif stack (on Android, Roboto); body 0.875 rem
  (14 px) in buttons and fields, labels and badges smaller (0.75 rem /
  0.6875 rem, weight 600); monospace only for counts and logs.
- **Spacing**: page padding 1.5 rem (24 px); 24 px between cards and
  sections; a card is at most 420 px wide (one phone screen), a settings
  column 864 px; top bar 56 px on phones, touch targets at least 44 px.
- **Buttons**: rounded 8 px, padding 10 × 16 px, weight 500. Primary =
  accent background with dark text; Secondary = card background, 1 px
  border; Danger = red with white text; small = 6 × 12 px, 12 px text.
  Footers: on the right, Secondary · Primary · Cancel.
- **Other components**: badges (20 px high, radius 4 px), chips (pill,
  accent at 12 % with a 40 % border), toggles, short sliders with the value
  as a %, ⋯ menus at the end of rows.
- **Help panel**: a `?` in the title bar opens a panel under it, 440 px wide
  on desktop and full-screen on a phone (`styles.css:3539-3554`). Short,
  literal lines.
- **Phone layout**: top bar with the hamburger; the menu opens downward;
  same structure and order as desktop; table rows become cards.
- **Wording**: plain language, short labels, no explanatory paragraphs in
  the UI. Use Chris's wording exactly as given. Don't restate a control's
  name as its description. No emphasizing qualifiers at the end of
  sentences ("...at all", "...every time"); choose wording that already
  makes the point. Terms: channel, preset, sequence, playlist, floor, area,
  group (see the terminology used in the web UI).
- Structural changes start as Claude Design boards for Chris's approval.

---

## 13. Gaps the app will hit

Prioritised. "Free" = needed for the Free MVP (remote and listening); "Pro"
= needed for standalone/offline. Built gaps say so and point to their
section; the others have a proposed shape.

### Free MVP

Built on branch `app-gaps` (each gap's final shape is in the section named):
G1, G2, G3, G4, G6, G7, G10, G13, G14; G5 on branch `personal-channel`.
Not built: G8, G9, G11, G12.

**G1. A phone sees, renames and unlinks itself. Built** (4.7).

```
GET    /api/devices/self   perm: device → {"id", "name", "user_id", "platform", "model", "created", "last_seen"}
PUT    /api/devices/self   perm: device   {"device_name"} → the record
DELETE /api/devices/self   perm: device → {"removed": true}   (the token stops working at once)
DELETE /api/devices/{id}   now also perm device, for the phone's own id only
```

**G2. Report casting: "Cast device X plays channel N". Built** (9.4, 9.5).

```
POST   /api/sessions/{id}/outputs               perm: ch.play  {"kind", "name", "address", "receiver_id"}
       → 201 {"output_id": "o_c4ad496bead1", ..., "channel_id": 1, "stream_path": "/stream/channel1",
              "stream_url": "http://192.0.2.25:8008/stream/channel1", "heartbeat_seconds": 60, "expire_seconds": 180}
PUT    /api/sessions/{id}/outputs/{output_id}   perm: ch.play  → heartbeat (every 60 s; dropped after 180 s)
DELETE /api/sessions/{id}/outputs/{output_id}   perm: ch.play  → {"removed": true}
GET    /api/sessions/{id}/outputs               perm: ch.view  → {"outputs": [...]}
GET    /api/sessions/{id}  → "outputs": [{"output_id", "kind", "name", "address", "receiver_id", "since", "by_device_id", "expires_in"}]
Events: channel.output_added / channel.output_removed {channel_id, name, output_id, kind, output_name, address[, reason]}
```

Differences from the proposal: `stream_url` is the plain stream (no
`?out=`): it is the speakers' stream, so speakers-muted applies without any
stream change, and the phone's own `?local=1` stays the only listener that
ignores it. The output keeps the mix running (no 15 s cut-off); stop, pause,
delete and idle drop the outputs and announce it. `is_playing` is still the
speakers' only; the web UI's channel card doesn't show outputs yet (a design
board item), HA and MQTT don't either.

**G3. A listener that survives Wi-Fi drops. Built** (9.3).
`POST /api/sessions/{id}/listen` takes `{"hold_seconds": 0-300}` (or
`?hold_seconds=`; default 15, as before) and answers it back. The longest
hold asked for counts. No `listen_id`: `DELETE /listen` is unchanged. Event
`channel.listening {channel_id, name, listening}`.

**G4. Event ids restart with the server. Built** (3, 8.1, 8.3).

```
GET /api/info → "boot_id": "<32 hex, new at every start>"
SSE frames:   id: <boot_id>:<n>   (data JSON: "id": n, "boot_id")
Last-Event-ID <boot_id>:<n>: replay after n; another run's id: replay all of this run's held events; bare n: as before
```

**G5. Missing events. Built** (8.2): `playlist.created|changed|deleted`,
`playlist.started|stopped`, `group.changed`, `spaces.changed`,
`settings.changed`, and `channel.listening` gained `devices` and
`playing`. They carry ids and names; the app refetches.

**G6. Live per-speaker volume and mute. Built** (7.4).

```
PUT /api/speakers/{id}/volume   perm: sp.mute   {"volume": 0-100} → {"speaker_id", "volume"}
PUT /api/speakers/{id}/muted    perm: sp.mute   {"muted": true}   → {"speaker_id", "muted"}
GET /api/speakers → adds "volume" and "muted" once set this way
```

404 unknown speaker, 422 bad body, 502 when the speaker doesn't take it.
Network speakers mute by volume 0. Values set elsewhere aren't followed.

**G7. mDNS TXT `pair`. Built** (1.2): it follows Require login (checked
every 10 s, re-announced when it changes).

**G8. Server name.** (Low) `n`, `name` and TXT `name` are always
`"Sonorium"`, so two servers look alike. Proposed: a **Server name** setting
(`PUT /api/settings {"server_name"}`, Admin), used in `/api/info.name`, the
QR `n` and TXT `name`. Not built.

**G9. Capabilities list.** (Low) `/api/info` → `"capabilities": ["events",
"listen", "pair_login", "recovery_code", "outputs", ...]`, so the app can
light features without probing routes. Not built: until then, `boot_id` in
`/api/info` marks a server with G1-G4, G6, G7, G10, G13 and G14.

**G10. Legacy routes answering 200 `{"error"}`. Built** (11.1): those
routes of `/api/themes`, `/api/categories` and `/api/channels` answer
404/409/503/500/400 with `{"detail", "error"}`.

**G11. Typed pairing code without a camera.** (Low, planned in AUTH.md)
HMAC exchange over the fingerprint:
`POST /api/pair/challenge {"client_nonce"} → {"server_nonce", "server_proof"}`,
`POST /api/pair {"code_proof", "client_nonce", ...}`, where proofs are
`HMAC-SHA256(code, fp | nonces | role)`. Not built.

**G12. Windows/Linux installs: theme IDs and paths.** (Low for the app, real
for testers on Windows) On Windows, themes whose `metadata.json` has non-ASCII
text (an emoji icon) are registered under their folder name instead of their
UUID (THEME_FORMAT.md Q11), so `POST /sessions/{id}/listen` and `play` answer
400 `"Theme not found"` for them while `GET /themes` lists the UUID. Seen on
the test server with Tavern and A Rainy Day; Sleigh Ride (no icon) worked.
Docker and the add-on aren't affected. Not built (theme loading is being
rewritten on another branch).

**G13. The web UI's dead calls. Built**: removed (no visible change); the
features were gone (updates come from HA, Docker or the Windows launcher;
speakers are switched on in Settings › Speakers). The app's proxy and the
offline engine needn't answer them.

**G14. UI version and host hooks. Built** (12.2.2): the `SONORIUM_HOST`
hooks in the web UI, and `/api/info` → `"ui": {"version": "1.6.2-dev.24",
"host_api": 1}`.

### Offline playback

Built on branch `theme-packages`: P1-P6 (section 10).

**P1. Theme package listing. Built** (10.3).

```
GET /api/themes/packages   perm: th.view
→ {"packages": [{"theme_id", "name", "spec_version", "content_hash": "sha256:…", "size_bytes", "file_count",
                 "track_count", "updated_at", "license": {…} | null, "source": {…} | null,
                 "redistributable": true | false | null}]}
```

**P2. Package with checksums. Built** (10.1-10.3).

```
GET /api/themes/{id}/package   perm: th.view
→ {"theme_id", "name", "spec_version", "content_hash", "size_bytes", "files": [{"path", "size", "sha256"}],
   "metadata": {…as exported…}, "presets": {…}, "playback_values": {…}}
```

Linked files appear at their exported path, as in the zip. The hash is
defined in 10.2.

**P3. Resumable per-file download. Built** (10.3).

```
GET /api/themes/{id}/files/{path}   perm: th.view
    Range, If-Range, If-None-Match; ETag = "<sha256>"; Content-Length; 404 for paths not in the package
```

**P4. Export lookup and streaming. Built** (10.3). The theme is found in the
configured audio folder; the zip is written to a temporary file and sent
with `Content-Length`.

**P5. Reference render. Built** (10.3).

```
POST /api/themes/{id}/render   admin only   {"preset_id", "seconds": 1..120, "seed", "master_gain"?}
→ audio/wav, mono 44.1 kHz 16-bit; rate-limited (11.2)
```

**P6. Advanced values per server. Built** (10.3): `playback_values` in P2.

---

## 14. What was checked, and how

A temporary standalone server (1.6.2-dev.23, `SONORIUM_STANDALONE=1`,
config and media in a temp folder, the three bundled themes, no Home
Assistant, no MQTT) on Windows, driven with `httpx`:

- `/api/info` over HTTP and HTTPS; the certificate's SHA-256 matched
  `fingerprint`; the four words matched the word list.
- mDNS browse of `_sonorium._tcp` (both the test server and the NAS server).
- First-run admin setup, `POST /api/link` (new `https://sonorium.app/link#`
  form with `hosts`), `POST /api/pair` over HTTP (403) and HTTPS (token),
  code reuse (401), `POST /api/pair/login` (right and wrong password, HTTP
  403), the pair rate limit (429 with `Retry-After`).
- Phone tokens of an Admin and a User: `/api/auth/me` perms, every read route
  in section 7, Admin-only refusals, recovery codes (phone 200, browser 403),
  device list and removal (phone 403, admin browser 200), revoked token (401
  `pair`), a phone linked with Users off then refused (401 `pair`) when Users
  came back with Require login on, an unknown token (401 `pair`), `?token=`.
- A channel: create, preset, volume, speakers-muted, play with no speakers
  (400), listen, stream (MP3, 44.1 kHz frame header `FFFB90`, 125-126 kbps,
  3 concurrent listeners), stream over 8808, unlisten, stop, delete;
  `/api/stream/url` before and during listening; an idle slot (200, 0
  bytes); slot 99 (404).
- The event stream over HTTPS with a phone token: live `channel.changed` /
  `channel.stopped`, replay with `Last-Event-ID`, anonymous 401.
- Switching the Users plugin off and on from an Admin's phone, and what
  phones and anonymous callers get meanwhile.

The gaps built on `app-gaps` (1.6.2-dev.24 plus that branch, same kind of
temp server, `httpx` over HTTPS with a phone token, the SSE stream read
alongside):

- `/api/info` `boot_id` and `ui` (HTTP and HTTPS); `/api/devices/self` GET,
  PUT, DELETE (then 401 `pair`), a browser's 403.
- `listen` with `hold_seconds` 400 (400) and 40: the phone's `?local=1`
  stream decoded (RMS about 5400), closed; 25 s later the channel still had
  its slot and `listening: true`; `DELETE /listen` released it.
- Outputs: the phone listened, reported a pretend Cast output (a documentation
  address, nothing contacted), stopped listening; the script pulled the plain
  `stream_url` for 25 s and the mix kept running (RMS about 7300). With
  speakers muted, the plain stream decoded to silence (RMS 0) while
  `?local=1` didn't (RMS about 7100). Heartbeat, DELETE (mix released),
  stop (outputs dropped).
- Events: `channel.listening` on/off, `channel.output_added`,
  `channel.output_removed` (`removed`, `stopped`); ids `<boot_id>:<n>`;
  replay after `<boot_id>:<n>`; an unknown boot id replayed from event 1.
- G10: `GET /api/themes/not-a-theme` and `/api/channels/99` → 404 with
  `detail`.
- G7: browsing `_sonorium._tcp` showed TXT `pair` 1, 0 after switching
  Require login off, 1 again after switching it on (each within 12 s).
- G13/G14: the web UI in a browser: no `/api/heartbeat` or `/update/check`
  calls, Listen here still plays `/stream/channel1?local=1` through
  `<audio>`, no `SONORIUM_HOST`. The hooks with a fake host: node test.
- G6 only for an unknown speaker (404) and a bad body (422): no real
  speaker's volume was changed. The rest is from unit tests with fake
  Home Assistant and network controllers.

Not run:

- **Playing to real speakers or Cast**: the test channel had no speakers, so
  `play`, speaker events and a real Cast handoff (9.4) are from the code
  only; outputs were checked with a pretend device. Output expiry (180 s)
  and the 90 s idle drop are from unit tests.
- **G6 on real speakers** (HA `volume_set`/`volume_mute`, network volume).
- **The add-on** (ingress port, Supervisor addresses, port mappings) and
  **Docker**: from code. The live NAS server's `/api/info` and mDNS
  announcement were read.
- **Theme packages** (10): a bundled theme (Tavern) downloaded file by file
  through the package routes from a local server, every SHA-256 and the
  content hash checked, one file resumed with `Range`/`If-Range`, the folder
  rebuilt and rendered with `sonorium.engine`: the same WAV bytes as the
  server's `POST /render` with the same seed. The zip export answered with
  `Content-Length`.
- **Sequences, playlists, `sequence.step` / `playlist.entry` events**: the
  bundled themes have no sequences; shapes from code and PLAYBACK.md.
- Theme, preset and track editing routes (Admin, web UI territory): from
  code.
