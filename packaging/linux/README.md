# Sonorium for Linux: the .deb package

One package that has everything Sonorium needs, so nothing else is installed
from the internet: a private Python, Sonorium's audio-only FFmpeg and
Sonorium itself. It runs as a service that starts with the computer.

| Machine | Package |
|---|---|
| Raspberry Pi 3, 4, 5 (64-bit Raspberry Pi OS), other 64-bit ARM boards | `sonorium_<version>_arm64.deb` |
| PCs and servers (Intel, AMD) | `sonorium_<version>_amd64.deb` |

Needs Debian 12 or later, Raspberry Pi OS (bookworm or later), Ubuntu 22.10 or later,
or another distribution built on them. 32-bit systems aren't supported.

## Install

Download the package for the machine from the
[releases](https://github.com/synssins/sonorium/releases), then:

```
sudo apt install ./sonorium_<version>_arm64.deb
```

Sonorium starts straight away. Open `http://<machine's address>:8008` in a browser.
Installing a newer package the same way updates it; settings and themes are kept.

| What | Where |
|---|---|
| Settings, plugins, logs | `/var/lib/sonorium/data` |
| Themes | `/var/lib/sonorium/themes` |
| Ports, theme folder | `/etc/default/sonorium` |
| Program | `/opt/sonorium` |

```
sudo systemctl status sonorium      # running?
sudo journalctl -u sonorium -f      # the log
sudo systemctl restart sonorium     # after changing /etc/default/sonorium
sudo apt remove sonorium            # uninstall (keeps /var/lib/sonorium)
```

## How it's built

[`build-deb.sh`](build-deb.sh) runs in a Debian 12 container on a machine of
the same architecture; CI ([`linux-deb.yml`](../../.github/workflows/linux-deb.yml))
does this on native amd64 and arm64 runners, installs each package with
systemd and checks the service answers. Every release (`addon-version.yml`)
builds them from the release tag and attaches them to the GitHub release.

- `/opt/sonorium/python`: [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
  3.12 with the Docker image's packages
- `/opt/sonorium/ffmpeg`: the LGPL audio-only FFmpeg from
  [`sonorium_addon/ffmpeg/build.sh`](../../sonorium_addon/ffmpeg/build.sh), with PyAV built against it
- [`sonorium.service`](sonorium.service): runs as the `sonorium` user with the
  host switches from [docs/HOSTING.md](../../docs/HOSTING.md)
- [`debian/`](debian): makes the user and folders, starts and stops the service
