# Use HA addon base image with bashio
ARG BUILD_FROM=ghcr.io/hassio-addons/base:16.3.2

# Build stage: Sonorium's own FFmpeg (audio only, LGPL only) and PyAV built
# against it, in place of PyAV's wheel with its GPL video encoders. See
# ffmpeg/build.sh. Only the libraries and the wheel go into the image below.
FROM ${BUILD_FROM} AS ffmpeg
# musl: the base image pins its exact release, which musl-dev (build-base) can't match
RUN apk add --no-cache \
    musl build-base nasm pkgconf zlib-dev curl \
    python3 python3-dev py3-pip
COPY ffmpeg/build.sh /build-ffmpeg.sh
RUN sh /build-ffmpeg.sh

FROM ${BUILD_FROM}

# Install Python and build dependencies
# Note: py3-numpy from Alpine avoids segfault on some virtualized environments (qemux86-64)
# No ffmpeg package: audio is decoded and encoded by PyAV with the FFmpeg
# libraries built above, and nothing runs the ffmpeg program
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-numpy

# FFmpeg's libraries and the PyAV wheel from the build stage
COPY --from=ffmpeg /opt/ffmpeg/lib/ /opt/ffmpeg/lib/
COPY --from=ffmpeg /wheels/ /tmp/wheels/

# Create working directory
WORKDIR /app

# Install Python dependencies
# Note: numpy installed via apk above, av installed below
# uvicorn[standard]: the web server with uvloop and httptools.
# httpx-retries: retries for Home Assistant registry requests.
RUN pip3 install --no-cache-dir --break-system-packages \
    pyyaml \
    appdirs \
    cachetools \
    fastapi \
    'uvicorn[standard]' \
    pydantic \
    httpx \
    httpx-retries \
    websockets \
    python-multipart \
    soco

# numpy, av (audio decoding), paho-mqtt (for HA entities), pydantic-settings, pychromecast
# numpy<2.4: pip replaces Alpine's numpy here, and numpy 2.4+ wheels require
# x86-64-v2 CPUs, which crashes on VMs with a basic virtual CPU (issues #18, #39).
# av: PyAV 19.0.1 built in the stage above (the version playback is tested with)
RUN pip3 install --no-cache-dir --break-system-packages \
    'numpy<2.4' \
    /tmp/wheels/av-*.whl \
    paho-mqtt \
    pydantic-settings \
    pychromecast \
    && rm -rf /tmp/wheels

# Network speakers (sonorium/network): on in Docker, off by default here and
# switched on in Settings > Advanced. Same packages as the Docker image.
RUN pip3 install --no-cache-dir --break-system-packages \
    aiohttp zeroconf async-upnp-client pyatv

# Sign-in: the HTTPS certificate (cryptography), the Link App QR code (segno),
# and password hashes for the built-in Users plugin (argon2-cffi, Argon2id)
RUN pip3 install --no-cache-dir --break-system-packages \
    cryptography segno argon2-cffi

# Copy local sonorium source code
COPY sonorium/ /app/sonorium/
COPY README.md /app/

# Copy logo and icon
COPY logo.png icon.png favicon.png display.png /app/

# Copy bundled themes (installed on first run if /media/sonorium is empty)
COPY themes/ /app/themes/

# Set PYTHONPATH instead of pip install -e (avoids caching issues)
# PYTHONUNBUFFERED ensures logs appear immediately in Docker
ENV PYTHONPATH="/app:${PYTHONPATH}" \
    PYTHONUNBUFFERED=1

# Verify installation and get version
RUN python3 -c "from sonorium.version import __version__; print(f'Sonorium {__version__} imported successfully')"

# Debug: List files in web directory
RUN ls -la /app/sonorium/web/ && ls -la /app/sonorium/web/static/ 2>/dev/null || echo "No static dir"

# Copy the run script
COPY run.sh /run.sh
# Starts Sonorium again when it exits to restart (code 75); same script as the Docker image
COPY supervise.sh /usr/local/bin/sonorium-supervise
RUN chmod a+x /run.sh /usr/local/bin/sonorium-supervise

# Read version from the single source of truth (sonorium/version file)
# Note: LABEL is set at build time, reading from the version file
ARG ADDON_VERSION
LABEL io.hass.version="${ADDON_VERSION:-1.8.1-dev.6}"

CMD [ "/run.sh" ]
