#!/bin/sh
# Sonorium's own FFmpeg for Windows: audio only, LGPL only. The Windows twin of
# sonorium_addon/ffmpeg/build.sh, with the same versions and the same configure
# flags, so the add-on, the Docker image and the desktop app play and stream alike.
#
# Runs in MSYS2's UCRT64 shell (CI: msys2/setup-msys2). Builds LAMER as a static
# library inside libavcodec and FFmpeg as DLLs, then leaves in $PREFIX:
#   bin/*.dll           the FFmpeg DLLs (and the MinGW runtime they need, if any)
#   lib/*.lib           MSVC import libraries (made because lib.exe is on PATH),
#                       which build-pyav.ps1 links PyAV against
#   include/            the headers
set -eu

FFMPEG_VERSION=9.0.2
FFMPEG_SHA256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e
LAMER_VERSION=3.101.0
LAMER_SHA256=81839b16fdc401e20b73389d05f32a7656008f43bcb960feff290d6f08fc109e

PREFIX=${PREFIX:-/c/sonorium-ffmpeg}
SRC=${SRC:-/tmp/ffmpeg-src}
JOBS=${JOBS:-$(nproc)}

mkdir -p "$SRC"
cd "$SRC"

fetch() {  # url file sha256
    curl -fsSL --retry 3 -o "$2" "$1"
    echo "$3  $2" | sha256sum -c -
}

fetch "https://github.com/basswood-io/lamer/archive/refs/tags/v${LAMER_VERSION}.tar.gz" \
    lamer.tar.gz "$LAMER_SHA256"
tar xzf lamer.tar.gz
make -C "lamer-${LAMER_VERSION}" -j "$JOBS" lib PIC=1
make -C "lamer-${LAMER_VERSION}" install PREFIX="$SRC/lame"

fetch "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" ffmpeg.tar.xz "$FFMPEG_SHA256"
tar xJf ffmpeg.tar.xz
cd "ffmpeg-${FFMPEG_VERSION}"

# zlib (permissive) goes inside the DLLs: a folder holding only MSYS2's static
# libz.a comes first in the search path, so -lz can't pick libz.dll.a.
mkdir -p "$SRC/zlib-static"
cp "${MINGW_PREFIX:-/ucrt64}/lib/libz.a" "$SRC/zlib-static/"

# Same flags as build.sh, except: no rpath (Windows finds DLLs beside the
# module), zlib static (above), Windows' own threads instead of MinGW's
# pthreads, and static libgcc, so the DLLs need no MinGW runtime DLLs.
./configure \
    --prefix="$PREFIX" \
    --target-os=mingw32 --arch=x86_64 \
    --disable-pthreads --enable-w32threads \
    --enable-shared --disable-static \
    --disable-gpl --disable-nonfree \
    --disable-autodetect --enable-zlib \
    --disable-programs --disable-doc --disable-network \
    --disable-everything \
    --enable-libmp3lame \
    --extra-cflags="-I$SRC/lame/include" \
    --extra-ldflags="-L$SRC/zlib-static -L$SRC/lame/lib -static-libgcc" \
    --enable-protocol=file \
    --enable-demuxer=mp3,wav,flac,ogg,mov,aac \
    --enable-parser=mpegaudio,flac,vorbis,opus,aac \
    --enable-decoder=mp3,mp3float,mp2,mp2float,flac,vorbis,opus,aac,alac \
    --enable-decoder=pcm_u8,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s32le,pcm_f32le,pcm_f64le \
    --enable-decoder=pcm_alaw,pcm_mulaw,adpcm_ms,adpcm_ima_wav \
    --enable-encoder=libmp3lame,pcm_s16le \
    --enable-muxer=mp3,wav \
    --enable-filter=abuffer,abuffersink,aformat,aresample \
    --enable-swresample
make -j "$JOBS"
make install
grep -q 'define CONFIG_GPL 0' config.h        # refuse a GPL build
grep -q 'define CONFIG_NONFREE 0' config.h
rm -rf "$PREFIX/share"

# The DLLs must not depend on any MinGW runtime DLL that isn't shipped
for dll in "$PREFIX"/bin/*.dll; do
    deps=$(objdump -p "$dll" | sed -n 's/^\s*DLL Name: //p')
    echo "$(basename "$dll"): $deps" | tr '\n' ' '; echo
    if echo "$deps" | grep -qiE 'libgcc|libwinpthread|libstdc|zlib1'; then
        echo "error: $dll needs a MinGW runtime DLL" >&2; exit 1
    fi
done
