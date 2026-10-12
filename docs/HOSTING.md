# Running Sonorium inside another program

Sonorium doesn't check what it runs on. The program that starts it (the
*host*: the Docker image, the Home Assistant app, a Windows service, a script)
switches on what it can do for Sonorium with environment variables. A host
that sets none of them gets Sonorium as it always was.

Think of it as a power strip: Sonorium has the same plug everywhere, and each
host decides which sockets are live.

| Variable | What it does | Default |
|---|---|---|
| `SONORIUM_SUPERVISED=1` | A restart ends Sonorium with exit code 75; the host starts it again | Sonorium restarts itself in place |
| `SONORIUM_SECRET_HELPER=<program>` | The Home Assistant token and the MQTT password are kept by that program, not in `connection.json` | Kept in `connection.json` (owner-only file) |
| `SONORIUM_CONSOLE_TOKEN_FILE=<path>` | Sonorium writes an admin credential for the host's console to that file | No console credential |
| `SONORIUM_HTTP_PORT=<port>` | The HTTP port (web UI, API, streams) | 8008 (Home Assistant app: 8080 inside, mapped to 8008) |
| `SONORIUM_HTTPS_PORT=<port>` | The HTTPS port | 8808 |
| `SONORIUM_BUNDLED_THEMES=<folder>` | Where the themes copied into an empty audio folder on first start are | `/app/themes` |
| `SONORIUM_CONFIG_DIR`, `SONORIUM_CONNECTION_FILE`, `SONORIUM__PATH_AUDIO` | Where settings, the connection settings and the audio are | `/config/sonorium`, `/config/sonorium/connection.json`, `/media/sonorium` |

## Restart: `SONORIUM_SUPERVISED`

Restart in Settings > Advanced, and every setting that needs one (a change
under Settings > Connection, the stream URL), stops Sonorium cleanly first:
channels stop, plugins stop, open streams are closed after 5 seconds, and
the MQTT broker is told Sonorium is leaving. Then:

- **With `SONORIUM_SUPERVISED=1`:** Sonorium exits with code **75**
  (`EX_TEMPFAIL`). The host starts it again at once. Any other exit code
  while the host isn't stopping it is a crash: restart it with a back-off.
- **Without it:** Sonorium starts itself again in the same process
  (`python -m sonorium.entrypoint`).

If the clean stop takes longer than 20 seconds, Sonorium goes ahead anyway.
The log shows `Restarting Sonorium (<why>): shutting down, then ...` when it
begins.

Both images run Sonorium under `supervise.sh`
(`/usr/local/bin/sonorium-supervise`), which sets `SONORIUM_SUPERVISED=1` and
starts Sonorium again when it exits with 75, inside the same container. So a
restart works without a `restart:` policy or Home Assistant's watchdog. A stop
(`docker stop`, stopping the app in Home Assistant) is passed on to Sonorium,
which shuts down and isn't started again. Any other exit ends the container
with Sonorium's exit code, for Docker's restart policy.

The web UI waits for the restart by watching `boot_id` in `GET /api/info`
(new at every start), then reloads the page once Sonorium has started.

## Secrets: `SONORIUM_SECRET_HELPER`

The value is **one program path, no arguments** (a path with spaces needs no
quotes). Sonorium runs it without a shell, the way git runs a credential
helper:

| Call | Input | Answer |
|---|---|---|
| `<program> store <name>` | The value on stdin: UTF-8, no newline after it | Exit 0 when stored |
| `<program> get <name>` | Nothing | The value on stdout, exit 0. Exit 1: it has none |
| `<program> erase <name>` | Nothing | Exit 0 (also when it had none) |

Names: `ha_token`, `mqtt_password`. One newline after the value on `get` is
ignored. Each call may take 10 seconds.

- **Saving** (Settings > Connection): the new token or password goes to the
  helper, never into `connection.json`. If the helper fails, nothing is saved
  and the save answers 500 with `"Not saved: The ha_token couldn't be saved:
  the secret helper failed (...)"`; Sonorium doesn't restart.
- **Removing** Home Assistant or MQTT: the helper's copy is erased first. If
  that fails, nothing is removed (500, `"Not removed: ..."`).
- **Starting:** Sonorium asks the helper once per secret. Exit 1, a failure or
  a missing program is logged and the secret counts as not set: Sonorium
  still starts, without that connection.
- **Switching it on later:** a token or password still in `connection.json`
  moves into the helper at the next start and leaves the file. One the helper
  won't take stays in the file, is used, and is tried again next start.
- **Switching it off later:** Sonorium no longer asks the helper, so the
  token and password have to be entered again.

## Console credential: `SONORIUM_CONSOLE_TOKEN_FILE`

A key for the host's own console (a tray app, a settings window), so it can
use the API as an admin without anyone signing in.

- At start, if the file is missing or its token isn't the one Sonorium has
  stored, Sonorium makes a new random token, keeps only its SHA-256 hash (in
  `security.json`, like every other token) and writes the token to the file:
  one line, UTF-8, no BOM, `sc_...` and nothing else. It is written to a temp
  file in the same folder and then swapped in, so a reader never sees half a
  file. Folders are created; the file is owner-only on Linux. The host
  protects the folder (on Windows, its permissions).
- **A new credential:** delete the file and restart. The old one stops
  working.
- **Use:** `Authorization: Bearer sc_...`, accepted **only from this
  computer**: the connection must come from `127.0.0.1`, `::1` or
  `::ffff:127.0.0.1`. Headers such as `X-Forwarded-For` don't count, and the
  Home Assistant sidebar's port never accepts it. From anywhere else, or
  without the variable, it is refused with 401.
- **Rights:** an admin's (every route). `GET /api/auth/me` answers
  `"kind": "console"`.
- It isn't a key: Settings > Keys and Users & devices don't list it, no route
  revokes it, and switching keys off doesn't affect it.

## Switching speaker discovery off

Speaker discovery (`network_speakers`: speakers found on the network or added
by address) is on in Docker and other standalone installs, and off in the Home
Assistant app until switched on in Settings > Advanced. On every install it
can be switched through the API:

```
PUT /api/install/features
{"network_speakers": false}                    takes effect at the next restart
{"network_speakers": false, "restart": true}   ... and restarts now
{"network_speakers": true}                     back on (or back to the default)
```

It needs the right to change settings (`st.change`; the console has it). The answer is `GET /api/install`'s, plus `"restarting"`; each
feature there has `"switchable": true` when this route may change it, and
`"enabled"` says whether it will run after the restart. Features with
`"live": true` (`keys`, `announcements`) change at once, without a restart.

## Stopping without a clean shutdown

Settings are written when they change, each to a temp file that then replaces
the old one (`state.json`, `security.json`, `connection.json`, the plugins'
and users' files, themes' `metadata.json` and `presets.json`). Ending
Sonorium without a clean shutdown (a hard kill, `docker stop` timing out)
loses nothing saved. What it skips: telling the MQTT broker and speakers that
Sonorium is going (they notice on their own), and "last used" times of keys
and linked phones from the last minute.
