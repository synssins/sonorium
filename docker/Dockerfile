# Sonorium standalone image (Docker, no Home Assistant Supervisor).
# Built from the same code as the Home Assistant add-on. Build context: repo root.
#
#   docker build -f docker/Dockerfile -t sonorium .
#
# Connection settings (Home Assistant URL + token, MQTT, stream URL) are set
# in the web UI after the first start: Settings -> Connection.

FROM python:3.12-slim

# ffmpeg: audio decoding/encoding; util-linux: setpriv (drop to PUID/PGID)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg util-linux \
    && rm -rf /var/lib/apt/lists/*

# Same Python dependencies and pins as the add-on (sonorium_addon/Dockerfile).
# numpy<2.4: 2.4+ wheels need x86-64-v2 CPUs (issues #18, #39).
# av 19.x: the audio decoder version playback is tested with.
RUN pip install --no-cache-dir \
    pyyaml appdirs cachetools fastapi uvicorn pydantic httpx homeassistant_api \
    websockets python-multipart soco \
    'fmtr.tools[version.dev,logging,sets,yaml,debug,caching,api,path.app,tabular,av,ha.api,http]' \
    'numpy<2.4' \
    'av>=19,<20' \
    paho-mqtt pydantic-settings pychromecast

# Network speaker discovery and streaming (sonorium/network); also in the
# add-on, where it's off unless switched on. mDNS/SSDP discovery needs host networking.
RUN pip install --no-cache-dir \
    aiohttp zeroconf async-upnp-client pyatv

WORKDIR /app
COPY sonorium_addon/sonorium/ /app/sonorium/
COPY sonorium_addon/logo.png sonorium_addon/icon.png sonorium_addon/favicon.png sonorium_addon/display.png /app/
# Bundled themes, copied into /media/sonorium on first start if it's empty
COPY sonorium_addon/themes/ /app/themes/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && python -c "from sonorium.version import __version__; print(f'Sonorium {__version__}')"

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    SONORIUM_STANDALONE=1 \
    SONORIUM__PATH_AUDIO=/media/sonorium

# /config: settings, channels, plugins. /media/sonorium: themes and audio.
VOLUME ["/config", "/media/sonorium"]
EXPOSE 8008

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8008/', timeout=4)"

ENTRYPOINT ["/entrypoint.sh"]
