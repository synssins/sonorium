# Third-party notices

Sonorium is built on software and content made by other people. This file lists what is
shipped with Sonorium (the Docker image, the Home Assistant add-on and the Windows app)
and the licence each part is under.

The versions below are the ones installed by the Dockerfiles
([`docker/Dockerfile`](docker/Dockerfile), [`sonorium_addon/Dockerfile`](sonorium_addon/Dockerfile))
at the time of writing; pip resolves the newest versions allowed when an image is built.
Each package's own licence file is installed with it, in its `*.dist-info` folder inside the
image.

---

## Source code for LGPL and GPL components

Some components below are under the GNU Lesser General Public License (LGPL) or the GNU
General Public License (GPL). Sonorium uses them unmodified, as published by their projects,
and as separate shared libraries or programs: they can be replaced with other builds of the
same libraries inside the image.

The complete corresponding source code of each of these components is available from the
project links below. In addition, for at least three years after the last release that
included a given component, the Sonorium maintainers will provide a copy of that
component's corresponding source code, for no more than the cost of providing it, to anyone
who asks. Ask by opening an issue at <https://github.com/synssins/sonorium/issues>.

---

## Python packages

Installed with pip in both images (the packages named in the Dockerfiles, and what they need).

| Package | Licence | Project |
|---|---|---|
| aiohappyeyeballs | PSF-2.0 | https://github.com/aio-libs/aiohappyeyeballs |
| aiohttp | Apache-2.0 AND MIT | https://github.com/aio-libs/aiohttp |
| aiosignal | Apache-2.0 | https://github.com/aio-libs/aiosignal |
| annotated-doc | MIT | https://github.com/fastapi/annotated-doc |
| annotated-types | MIT | https://github.com/annotated-types/annotated-types |
| anyio | MIT | https://github.com/agronholm/anyio |
| appdirs | MIT | https://github.com/ActiveState/appdirs |
| argon2-cffi | MIT | https://github.com/hynek/argon2-cffi |
| argon2-cffi-bindings | MIT (bundles the Argon2 reference library: CC0-1.0 or Apache-2.0) | https://github.com/hynek/argon2-cffi-bindings |
| async-upnp-client | Apache-2.0 | https://github.com/StevenLooman/async_upnp_client |
| attrs | MIT | https://github.com/python-attrs/attrs |
| av (PyAV) | BSD-3-Clause (built from source against Sonorium's own FFmpeg build, see below) | https://github.com/PyAV-Org/PyAV |
| cachetools | MIT | https://github.com/tkem/cachetools |
| casttube | MIT | https://github.com/ur1katz/casttube |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |
| cffi | MIT-0 | https://github.com/python-cffi/cffi |
| chacha20poly1305-reuseable | Apache-2.0 OR BSD-3-Clause | https://github.com/bdraco/chacha20poly1305-reuseable |
| charset-normalizer | MIT | https://github.com/jawah/charset_normalizer |
| click | BSD-3-Clause | https://github.com/pallets/click |
| cryptography | Apache-2.0 OR BSD-3-Clause (wheels include OpenSSL, Apache-2.0) | https://github.com/pyca/cryptography |
| defusedxml | PSF-2.0 | https://github.com/tiran/defusedxml |
| fastapi | MIT | https://github.com/fastapi/fastapi |
| frozenlist | Apache-2.0 | https://github.com/aio-libs/frozenlist |
| h11 | MIT | https://github.com/python-hyper/h11 |
| httpcore | BSD-3-Clause | https://github.com/encode/httpcore |
| httptools | MIT | https://github.com/MagicStack/httptools |
| httpx | BSD-3-Clause | https://github.com/encode/httpx |
| httpx-retries | MIT | https://github.com/will-ockmore/httpx-retries |
| idna | BSD-3-Clause | https://github.com/kjd/idna |
| ifaddr | MIT | https://github.com/pydron/ifaddr |
| lxml | BSD-3-Clause (bundles libxml2 and libxslt, MIT) | https://lxml.de/ |
| miniaudio | MIT | https://github.com/irmen/pyminiaudio |
| multidict | Apache-2.0 | https://github.com/aio-libs/multidict |
| numpy | BSD-3-Clause (bundles OpenBLAS and other libraries, see below) | https://numpy.org |
| opentelemetry-api | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python |
| paho-mqtt | EPL-2.0 OR BSD-3-Clause (EDL-1.0) | https://github.com/eclipse-paho/paho.mqtt.python |
| propcache | Apache-2.0 | https://github.com/aio-libs/propcache |
| protobuf | BSD-3-Clause | https://github.com/protocolbuffers/protobuf |
| pyatv | MIT | https://github.com/postlund/pyatv |
| PyChromecast | MIT | https://github.com/home-assistant-libs/pychromecast |
| pycparser | BSD-3-Clause | https://github.com/eliben/pycparser |
| pydantic | MIT | https://github.com/pydantic/pydantic |
| pydantic-core | MIT | https://github.com/pydantic/pydantic |
| pydantic-settings | MIT | https://github.com/pydantic/pydantic-settings |
| python-didl-lite | Apache-2.0 | https://github.com/StevenLooman/python-didl-lite |
| python-dotenv | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| python-multipart | Apache-2.0 | https://github.com/Kludex/python-multipart |
| PyYAML | MIT | https://pyyaml.org/ |
| requests | Apache-2.0 | https://github.com/psf/requests |
| segno | BSD-3-Clause | https://github.com/heuer/segno |
| six | MIT | https://github.com/benjaminp/six |
| soco | MIT | https://github.com/SoCo/SoCo |
| srptools | BSD-3-Clause | https://github.com/idlesign/srptools |
| starlette | BSD-3-Clause | https://github.com/Kludex/starlette |
| tabulate | MIT | https://github.com/astanin/python-tabulate |
| tinytag | MIT | https://github.com/tinytag/tinytag |
| typing-extensions | PSF-2.0 | https://github.com/python/typing_extensions |
| typing-inspection | MIT | https://github.com/pydantic/typing-inspection |
| urllib3 | MIT | https://github.com/urllib3/urllib3 |
| uvicorn | BSD-3-Clause | https://github.com/Kludex/uvicorn |
| uvloop | MIT OR Apache-2.0 | https://github.com/MagicStack/uvloop |
| voluptuous | BSD-3-Clause | https://github.com/alecthomas/voluptuous |
| watchfiles | MIT | https://github.com/samuelcolvin/watchfiles |
| websockets | BSD-3-Clause | https://github.com/python-websockets/websockets |
| xmltodict | MIT | https://github.com/martinblech/xmltodict |
| yarl | Apache-2.0 | https://github.com/aio-libs/yarl |
| zeroconf | **LGPL-2.1-or-later** | https://github.com/python-zeroconf/python-zeroconf |

**zeroconf** (mDNS: finding speakers, and announcing `sonorium.local`) is under the LGPL.
Sonorium uses it unmodified, as an installed Python package that can be replaced with any
other version of zeroconf. Source: <https://github.com/python-zeroconf/python-zeroconf>.

---

## Libraries bundled inside Python packages

### PyAV (`av`): FFmpeg

PyAV's ready-made wheels are not used: they carry a full FFmpeg built with GPL video
encoders (x264, x265) and TLS libraries. Both images build an audio-only FFmpeg from
source instead, with nothing under the GPL, and build PyAV 19.0.1 from source against it
([`sonorium_addon/ffmpeg/build.sh`](sonorium_addon/ffmpeg/build.sh), which holds the
exact versions, checksums and `configure` options). FFmpeg reports this build's licence
as *LGPL version 2.1 or later*.

| Library | Licence | Source |
|---|---|---|
| FFmpeg 9.0.2 (libavcodec, libavformat, libavutil, libavfilter, libavdevice, libswresample, libswscale) | **LGPL-2.1-or-later** | https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz |
| LAME (LAMER 3.101.0, the LAME tree PyAV builds its wheels with), linked into libavcodec | **LGPL-2.1-or-later** | https://github.com/basswood-io/lamer |
| zlib (from Debian / Alpine, used by FFmpeg) | Zlib | https://zlib.net/ |

What the build includes: reading MP3, WAV, FLAC, Ogg (Vorbis, Opus) and M4A (AAC, ALAC)
with FFmpeg's own decoders; writing MP3 (LAME) and 16-bit WAV; and converting sample rate,
format and channels (libswresample). Everything else is left out (`--disable-everything`,
`--disable-autodetect`, `--disable-gpl`, `--disable-nonfree`).

The libraries are separate shared objects in `/opt/ffmpeg/lib` inside the image, and PyAV
loads them from there: they can be replaced with another build of the same FFmpeg version.
PyAV's source is at <https://github.com/PyAV-Org/PyAV>.

### NumPy

NumPy's binary wheels contain OpenBLAS (BSD-3-Clause), LAPACK (BSD-3-Clause-Open-MPI),
the GCC Fortran runtime (libgfortran: GPL-3.0-or-later WITH GCC-exception-3.1) and
libquadmath (**LGPL-2.1-or-later**). The full list is in NumPy's installed `LICENSE.txt`.
Sources: <https://github.com/numpy/numpy>, <https://github.com/OpenMathLib/OpenBLAS>,
<https://gcc.gnu.org/>.

---

## Operating system packages

| Image | Base | Packages Sonorium adds |
|---|---|---|
| Docker (`docker/Dockerfile`) | `python:3.12-slim` (Debian) | `util-linux` (from Debian) |
| Home Assistant add-on (`sonorium_addon/Dockerfile`) | `ghcr.io/hassio-addons/base` (Alpine Linux) | `python3`, `py3-pip`, `py3-numpy` (from Alpine) |

These packages are installed unmodified from Debian and Alpine Linux. Neither image installs
the distributions' FFmpeg packages (those are built with GPL options). util-linux (the
`setpriv` program, run by the Docker image's start script) is under GPL-2.0-or-later and
other licences listed in its source. Their source
code is published by each distribution: <https://sources.debian.org/> and
<https://gitlab.alpinelinux.org/alpine/aports>. Python itself is under the
PSF-2.0 licence (<https://www.python.org/>).

---

## Web interface

Many of the icons in the web interface and the Home Assistant dashboard cards are drawn
from **Feather** (<https://feathericons.com>, <https://github.com/feathericons/feather>),
included inline as SVG; the rest are drawn for Sonorium in the same style. Feather is under
the MIT licence:

```
The MIT License (MIT)

Copyright (c) 2013-2017 Cole Bemis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The web interface loads nothing from other websites: no fonts, scripts or stylesheets.

---

## Bundled themes

The themes in [`sonorium_addon/themes`](sonorium_addon/themes) ("A Rainy Day... Or is it?",
Sleigh Ride, Tavern) contain recordings from Ambient-Mixer.com under the Creative Commons
Sampling Plus 1.0 licence (<https://creativecommons.org/licenses/sampling+/1.0/>). Each
theme folder has an `ATTRIBUTION.md` listing its source and files.

---

## Windows app

The Windows app (`app/windows/Sonorium.exe`) is built with PyInstaller and ships an
embedded Python with the packages in [`app/core/requirements.txt`](app/core/requirements.txt).
In addition to the packages above, it contains:

| Component | Licence | Source |
|---|---|---|
| Python (embedded) | PSF-2.0 | https://www.python.org/ |
| PyInstaller bootloader | GPL-2.0-or-later WITH Bootloader exception | https://github.com/pyinstaller/pyinstaller |
| PyQt6 (the launcher window) | **GPL-3.0-only** (or a commercial licence from Riverbank Computing) | https://www.riverbankcomputing.com/software/pyqt/ |
| Qt 6 (used by PyQt6) | **LGPL-3.0-only** | https://www.qt.io/download-open-source |
| sounddevice | MIT (bundles PortAudio, MIT) | https://github.com/spatialaudio/python-sounddevice |
| aiofiles | Apache-2.0 | https://github.com/Tinche/aiofiles |

---

## Upstream project

Sonorium was originally based on [Amniotic](https://github.com/fmtr/amniotic) by fmtr. It has since been fully rewritten, and none of Amniotic's code remains.
