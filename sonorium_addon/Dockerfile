# Use HA addon base image with bashio
ARG BUILD_FROM=ghcr.io/hassio-addons/base:16.3.2
FROM ${BUILD_FROM}

# Install Python and build dependencies
# Note: py3-numpy from Alpine avoids segfault on some virtualized environments (qemux86-64)
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-numpy \
    ffmpeg

# Create working directory
WORKDIR /app

# Install Python dependencies
# Note: numpy installed via apk above, av installed via fmtr.tools[av] below
RUN pip3 install --no-cache-dir --break-system-packages \
    pyyaml \
    appdirs \
    cachetools \
    fastapi \
    uvicorn \
    pydantic \
    httpx \
    homeassistant_api \
    websockets \
    python-multipart \
    soco

# Install fmtr.tools (for API layer), paho-mqtt (for HA entities), pydantic-settings, pychromecast
RUN pip3 install --no-cache-dir --break-system-packages \
    'fmtr.tools[version.dev,logging,sets,yaml,debug,caching,api,path.app,tabular,av,ha.api,http]' \
    paho-mqtt \
    pydantic-settings \
    pychromecast

# Copy local sonorium source code
COPY sonorium/ /app/sonorium/
COPY setup.py pyproject.toml README.md /app/

# Copy logo and icon
COPY logo.png icon.png /app/

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
RUN chmod a+x /run.sh

# Read version from the single source of truth (sonorium/version file)
# Note: LABEL is set at build time, reading from the version file
ARG ADDON_VERSION
LABEL io.hass.version="${ADDON_VERSION:-1.2.80-dev}"

CMD [ "/run.sh" ]
