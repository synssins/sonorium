#!/bin/sh
# Sonorium's own FFmpeg: audio only, LGPL only, and PyAV built against it.
#
# PyAV's ready-made wheels carry a full FFmpeg with video encoders under the
# GPL (x264, x265) and TLS libraries Sonorium never uses. This builds just the
# parts Sonorium plays and streams with, and nothing under the GPL:
#
#   reads    MP3, WAV, FLAC, Ogg (Vorbis, Opus), M4A/AAC/ALAC
#   writes   MP3 (LAME, LGPL-2.1), for the speaker streams and the test chime,
#            and 16-bit WAV (the tests make their sample files with it)
#   converts sample rate, format and channels (libswresample, aresample)
#
# Used by both images (sonorium_addon/Dockerfile and docker/Dockerfile) in a
# build stage. Installs FFmpeg's shared libraries in $PREFIX/lib and leaves
# PyAV's wheel in $WHEELS; the image itself only copies those two.
#
# The versions are the ones PyAV 19.0.1's wheels were built from (FFmpeg 9.0.2,
# LAMER 3.101.0, PyAV's maintained LAME tree), so the MP3 stream comes out the
# same, byte for byte.
set -eu

FFMPEG_VERSION=9.0.2
FFMPEG_SHA256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e
LAMER_VERSION=3.101.0
LAMER_SHA256=81839b16fdc401e20b73389d05f32a7656008f43bcb960feff290d6f08fc109e
AV_VERSION=19.0.1

PREFIX=${PREFIX:-/opt/ffmpeg}
WHEELS=${WHEELS:-/wheels}
SRC=${SRC:-/tmp/ffmpeg-src}
JOBS=${JOBS:-$(nproc)}

mkdir -p "$SRC" "$WHEELS"
cd "$SRC"

fetch() {  # url file sha256
    curl -fsSL --retry 3 -o "$2" "$1"
    echo "$3  $2" | sha256sum -c -
}

# LAME (LAMER: PyAV's cleaned-up LAME 3.101 tree), LGPL-2.1-or-later.
# A static library inside libavcodec, as in PyAV's wheels: no separate file.
fetch "https://github.com/basswood-io/lamer/archive/refs/tags/v${LAMER_VERSION}.tar.gz" \
    lamer.tar.gz "$LAMER_SHA256"
tar xzf lamer.tar.gz
make -C "lamer-${LAMER_VERSION}" -j "$JOBS" lib PIC=1
make -C "lamer-${LAMER_VERSION}" install PREFIX="$SRC/lame"

fetch "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" ffmpeg.tar.xz "$FFMPEG_SHA256"
tar xJf ffmpeg.tar.xz
cd "ffmpeg-${FFMPEG_VERSION}"

# --disable-gpl/--disable-nonfree are FFmpeg's defaults, spelled out: the
# build stays LGPL-2.1-or-later. --disable-autodetect: nothing is picked up
# from whatever else is installed (ALSA, X11, TLS...); zlib (permissive) is
# the one library asked for, for compressed headers in M4A files.
./configure \
    --prefix="$PREFIX" \
    --enable-shared --disable-static --enable-rpath \
    --disable-gpl --disable-nonfree \
    --disable-autodetect --enable-zlib \
    --disable-programs --disable-doc --disable-network \
    --disable-everything \
    --enable-libmp3lame \
    --extra-cflags="-I$SRC/lame/include" \
    --extra-ldflags="-L$SRC/lame/lib" \
    --enable-protocol=file \
    --enable-demuxer=mp3,wav,flac,ogg,mov,aac \
    --enable-parser=mpegaudio,flac,vorbis,opus,aac \
    --enable-decoder=mp3,mp3float,mp2,mp2float,flac,vorbis,opus,aac,alac \
    --enable-decoder=pcm_u8,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s32le,pcm_f32le,pcm_f64le \
    --enable-decoder=pcm_alaw,pcm_mulaw,adpcm_ms,adpcm_ima_wav \
    --enable-encoder=libmp3lame,pcm_s16le,flac \
    --enable-muxer=mp3,wav,flac \
    --enable-filter=abuffer,abuffersink,aformat,aresample \
    --enable-swresample
make -j "$JOBS"
make install
grep -q 'define CONFIG_GPL 0' config.h        # refuse a GPL build
grep -q 'define CONFIG_NONFREE 0' config.h
rm -rf "$PREFIX/share"

# PyAV from source, linked to the libraries above (it finds them by pkg-config).
# The libraries find each other through their rpath (--enable-rpath), and PyAV
# gets its own through LDFLAGS: its setup.py refuses linker flags in pkg-config.
sed -i 's/ *-Wl,-rpath,[^ ]*//g' "$PREFIX"/lib/pkgconfig/*.pc
cd "$SRC"
PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig" LDFLAGS="-Wl,-rpath,$PREFIX/lib" \
    pip wheel --no-cache-dir --no-deps --no-binary av -w "$WHEELS" "av==${AV_VERSION}"

rm -rf "$SRC"
