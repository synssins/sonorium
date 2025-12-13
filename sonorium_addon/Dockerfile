# Use HA addon base image with bashio
ARG BUILD_FROM=ghcr.io/hassio-addons/base:16.3.2
FROM ${BUILD_FROM}

# Install Python and build dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    ffmpeg

# Create working directory
WORKDIR /app

# Install Python dependencies
RUN pip3 install --no-cache-dir --break-system-packages \
    pyyaml \
    appdirs \
    cachetools \
    fastapi \
    av \
    uvicorn \
    numpy \
    pydantic \
    httpx \
    homeassistant_api \
    websockets \
    python-multipart

# Install fmtr.tools and haco from PyPI
RUN pip3 install --no-cache-dir --break-system-packages \
    'fmtr.tools[version.dev,logging,sets,yaml,debug,caching,api,path.app,tabular,av,ha.api,http]' \
    haco

# Copy local sonorium source code
COPY sonorium/ /app/sonorium/
COPY setup.py pyproject.toml README.md /app/

# Copy logo and icon
COPY logo.png icon.png /app/

# Install sonorium from local source
RUN pip3 install --no-cache-dir --break-system-packages -e /app

# Verify installation
RUN python3 -c "from sonorium.version import __version__; print(f'Sonorium {__version__} imported successfully')"

# Copy the run script
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL io.hass.version="1.1.16-dev"

CMD [ "/run.sh" ]
