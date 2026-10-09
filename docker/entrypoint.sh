#!/bin/sh
# Sonorium standalone entrypoint.
# PUID/PGID (optional): run as that user, the usual convention for
# self-hosted images, so files in the mounted volumes get the right owner.
set -e

mkdir -p /config/sonorium /media/sonorium

if [ -n "${PUID}" ] && [ -n "${PGID}" ] && [ "$(id -u)" = "0" ]; then
    # /config/sonorium is Sonorium's own folder: take ownership of all of it.
    # /media/sonorium may hold your existing audio: only its top folder.
    chown -R "${PUID}:${PGID}" /config/sonorium
    chown "${PUID}:${PGID}" /media/sonorium
    exec setpriv --reuid="${PUID}" --regid="${PGID}" --clear-groups \
        python -m sonorium.entrypoint
fi

exec python -m sonorium.entrypoint
