#!/bin/sh
# Builds Sonorium's Debian package (.deb) for the machine it runs on: amd64 or
# arm64 (Raspberry Pi 3/4/5 with a 64-bit OS, other ARM boards).
#
#   docker run --rm -v "$PWD:/src" -w /src debian:bookworm packaging/linux/build-deb.sh dist
#
# Runs as root in a clean Debian 12 (bookworm) container: the package then
# works on Debian 12 and later, Raspberry Pi OS (bookworm and later) and
# Ubuntu 22.10 and later (glibc 2.36 or newer). Builds in /opt/sonorium itself,
# the path it is installed to, because FFmpeg's libraries and PyAV find each
# other there (rpath).
#
# What goes in, laid out like the Windows app (packaging/windows):
#
#   /opt/sonorium/python    a private Python (python-build-standalone) with
#                           Sonorium's packages: the Docker image's list
#   /opt/sonorium/ffmpeg    Sonorium's LGPL audio-only FFmpeg (sonorium_addon/ffmpeg/build.sh)
#   /opt/sonorium/app       the sonorium package, bundled themes, logo and icons
#   /lib/systemd/system/sonorium.service, /etc/default/sonorium
#
# Data lives in /var/lib/sonorium (made by postinst, kept on removal).
set -eu

OUT=${1:-dist}
SRC=$(cd "$(dirname "$0")/../.." && pwd)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)

# Python 3.12, as in the Docker image (python:3.12-slim)
PBS_RELEASE=20261003
PY_VERSION=3.12.15
PY_SHA256_amd64=f937814031eab4698ca6d07ec606ede1825768f3f3e99af76d9db3900bee03c5
PY_SHA256_arm64=95c01982c9fcb9d95b0acfdb5eb8a6e0099dd11edf062314a474228d93f2b765

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    build-essential nasm pkg-config zlib1g-dev curl ca-certificates xz-utils dpkg-dev

ARCH=$(dpkg --print-architecture)
case "$ARCH" in
    amd64) PY_TRIPLE=x86_64-unknown-linux-gnu; PY_SHA256=$PY_SHA256_amd64 ;;
    arm64) PY_TRIPLE=aarch64-unknown-linux-gnu; PY_SHA256=$PY_SHA256_arm64 ;;
    *) echo "No Sonorium package for $ARCH (amd64 and arm64 only)" >&2; exit 1 ;;
esac

VERSION=$(cat "$SRC/sonorium_addon/sonorium/version")
# 1.8.1-dev.5 -> 1.8.1~dev.5: a pre-release sorts before the release in dpkg
DEB_VERSION=$(echo "$VERSION" | sed 's/-/~/')

OPT=/opt/sonorium
rm -rf "$OPT"
mkdir -p "$OPT"

# 1. The private Python: relocatable, needs only glibc, carries its own OpenSSL
py_tar=/tmp/python.tar.gz
curl -fsSL --retry 3 -o "$py_tar" \
    "https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_RELEASE/cpython-$PY_VERSION+$PBS_RELEASE-$PY_TRIPLE-install_only.tar.gz"
echo "$PY_SHA256  $py_tar" | sha256sum -c -
tar xzf "$py_tar" -C "$OPT"   # -> /opt/sonorium/python
rm "$py_tar"
PY=$OPT/python/bin/python3
"$PY" -m pip install --no-cache-dir --disable-pip-version-check --upgrade pip

# 2. FFmpeg (audio only, LGPL) and PyAV against it, with the private Python's pip
PATH="$OPT/python/bin:$PATH" PREFIX="$OPT/ffmpeg" WHEELS=/tmp/wheels \
    sh "$SRC/sonorium_addon/ffmpeg/build.sh"
rm -rf "$OPT/ffmpeg/include" "$OPT/ffmpeg/lib/pkgconfig"

# 3. Packages: the Docker image's list and pins (docker/Dockerfile).
# numpy<2.4: 2.4+ wheels need x86-64-v2 CPUs (issues #18, #39).
# pip stays: plugins may install their own packages (into the data folder).
"$PY" -m pip install --no-cache-dir --disable-pip-version-check \
    /tmp/wheels/av-*.whl \
    pyyaml appdirs cachetools fastapi 'uvicorn[standard]' pydantic httpx httpx-retries \
    websockets python-multipart soco \
    'numpy<2.4' \
    paho-mqtt pydantic-settings pychromecast \
    aiohttp zeroconf async-upnp-client pyatv \
    cryptography segno argon2-cffi
rm -rf /tmp/wheels

# 4. Sonorium, laid out as in the image's /app
mkdir -p "$OPT/app"
cp -r "$SRC/sonorium_addon/sonorium" "$OPT/app/sonorium"
cp -r "$SRC/sonorium_addon/themes" "$OPT/app/themes"
for f in logo.png icon.png favicon.png display.png; do
    cp "$SRC/sonorium_addon/$f" "$OPT/app/"
done
rm -rf "$OPT/app/sonorium/tests"
find "$OPT" -name __pycache__ -type d -prune -exec rm -rf {} +
# Compiled now: the service user can't write to /opt
"$PY" -m compileall -q -j 0 "$OPT/app/sonorium" "$OPT/python/lib" >/dev/null || true

# 5. Check it runs on its own: the private Python imports Sonorium and the LGPL PyAV
env -i PYTHONPATH="$OPT/app" "$PY" -c '
import sys, av, av._core as c
lic = c.library_meta["libavcodec"]["license"]
print("Python", sys.version.split()[0], "PyAV", av.__version__, "FFmpeg", av.ffmpeg_version_info, lic)
assert lic.startswith("LGPL"), lic
conf = c.library_meta["libavcodec"]["configuration"]
assert "--disable-gpl" in conf and "--disable-everything" in conf, conf
assert "x264" not in conf and "x265" not in conf, conf
assert av.codec.Codec("mp3", "w").name == "libmp3lame"
import sonorium.mp3_out
from sonorium.version import __version__
print("Sonorium", __version__)
'

# 6. The package
PKG=/tmp/sonorium-deb
rm -rf "$PKG"
mkdir -p "$PKG/opt" "$PKG/lib/systemd/system" "$PKG/etc/default" "$PKG/DEBIAN"
cp -a "$OPT" "$PKG/opt/"
cp "$SRC/packaging/linux/sonorium.service" "$PKG/lib/systemd/system/"
cp "$SRC/packaging/linux/sonorium.default" "$PKG/etc/default/sonorium"
for s in postinst prerm postrm; do
    install -m 0755 "$SRC/packaging/linux/debian/$s" "$PKG/DEBIAN/$s"
done
echo /etc/default/sonorium > "$PKG/DEBIAN/conffiles"

size=$(du -sk "$PKG" | cut -f1)
cat > "$PKG/DEBIAN/control" <<EOF
Package: sonorium
Version: $DEB_VERSION
Architecture: $ARCH
Maintainer: Sonorium <15873745+synssins@users.noreply.github.com>
Installed-Size: $size
Depends: libc6 (>= 2.36), zlib1g, adduser
Section: sound
Priority: optional
Homepage: https://github.com/synssins/sonorium
Description: Multi-zone ambient soundscape mixer
 Sonorium mixes ambient themes and streams them to speakers on the
 network or through Home Assistant.
 Runs as a service; the web UI is on port 8008 (HTTPS 8808).
EOF

deb="$OUT/sonorium_${DEB_VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$deb"
rm -rf "$PKG"
dpkg-deb --info "$deb"
echo "Built $deb"
