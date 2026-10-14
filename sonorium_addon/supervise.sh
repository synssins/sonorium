#!/bin/sh
# Runs Sonorium and starts it again when it asks to be restarted.
#
#   supervise.sh <command that starts Sonorium...>
#
# Used by both images (the Home Assistant app's run.sh and the Docker image's
# entrypoint.sh). It sets SONORIUM_SUPERVISED=1, so Restart in the web UI (and
# every setting that needs a restart) shuts Sonorium down cleanly and exits
# with code 75; this script then starts it again in the same container. Any
# other exit ends the script with Sonorium's exit code, for Docker's restart
# policy or Home Assistant's watchdog. A stop signal (docker stop) is passed
# on to Sonorium, which shuts down and is not started again.

export SONORIUM_SUPERVISED=1
RESTART_EXIT_CODE=75

child=""
stopping=""

stop() {
    stopping=1
    if [ -n "${child}" ]; then
        kill -TERM "${child}" 2>/dev/null || true
    fi
}
trap stop TERM INT

if [ "$#" -eq 0 ]; then
    echo "supervise.sh: no command given" >&2
    exit 64
fi

while :; do
    "$@" &
    child=$!
    status=0
    wait "${child}" || status=$?
    # A stop signal interrupts wait: wait again until Sonorium has shut down
    while kill -0 "${child}" 2>/dev/null; do
        status=0
        wait "${child}" || status=$?
    done
    child=""
    if [ -z "${stopping}" ] && [ "${status}" -eq "${RESTART_EXIT_CODE}" ]; then
        echo "Sonorium asked to be restarted: starting it again"
        continue
    fi
    exit "${status}"
done
