#!/usr/bin/with-contenv bash
# shellcheck shell=bash
# ==============================================================================
# Sonorium Addon Startup Script
# ==============================================================================

# Source bashio library
source /usr/lib/bashio/bashio.sh

bashio::log.info "Starting Sonorium addon..."

# Log environment for debugging
bashio::log.debug "Environment variables:"
bashio::log.debug "  SUPERVISOR_TOKEN present: $([ -n "${SUPERVISOR_TOKEN:-}" ] && echo 'yes' || echo 'no')"

# Export addon configuration as environment variables
export SONORIUM__STREAM_URL="$(bashio::config 'sonorium__stream_url')"
export SONORIUM__PATH_AUDIO="$(bashio::config 'sonorium__path_audio')"
export SONORIUM__MAX_CHANNELS="$(bashio::config 'sonorium__max_channels')"

# MQTT Configuration - Priority: Manual config > bashio::services > Python fallback
MQTT_HOST_CONFIG="$(bashio::config 'sonorium__mqtt_host')"
MQTT_PORT_CONFIG="$(bashio::config 'sonorium__mqtt_port')"
MQTT_USER_CONFIG="$(bashio::config 'sonorium__mqtt_username')"
MQTT_PASS_CONFIG="$(bashio::config 'sonorium__mqtt_password')"

# The Mosquitto add-on registers the MQTT service each time it starts. At boot
# Sonorium can start first, so wait for it instead of failing (issue #42).
if [[ -z "${MQTT_HOST_CONFIG}" || "${MQTT_HOST_CONFIG}" == "auto" ]] && ! bashio::services.available "mqtt"; then
    bashio::log.warning "No MQTT broker is registered with the Supervisor yet. Waiting up to 2 minutes for the Mosquitto broker add-on to start..."
    for _ in $(seq 1 24); do
        sleep 5
        if bashio::services.available "mqtt"; then
            bashio::log.info "MQTT broker is now available"
            break
        fi
    done
fi

# Check if user provided manual MQTT config (not "auto" or empty)
if [[ -n "${MQTT_HOST_CONFIG}" && "${MQTT_HOST_CONFIG}" != "auto" ]]; then
    bashio::log.info "Using manual MQTT configuration"
    export SONORIUM__MQTT_HOST="${MQTT_HOST_CONFIG}"
    export SONORIUM__MQTT_PORT="${MQTT_PORT_CONFIG:-1883}"
    export SONORIUM__MQTT_USERNAME="${MQTT_USER_CONFIG}"
    export SONORIUM__MQTT_PASSWORD="${MQTT_PASS_CONFIG}"
elif bashio::services.available "mqtt"; then
    # Auto-detect from Supervisor services (recommended HA method)
    bashio::log.info "Auto-detecting MQTT from Supervisor services..."
    export SONORIUM__MQTT_HOST="$(bashio::services mqtt "host")"
    export SONORIUM__MQTT_PORT="$(bashio::services mqtt "port")"
    export SONORIUM__MQTT_USERNAME="$(bashio::services mqtt "username")"
    export SONORIUM__MQTT_PASSWORD="$(bashio::services mqtt "password")"
    bashio::log.info "MQTT auto-detected: ${SONORIUM__MQTT_HOST}:${SONORIUM__MQTT_PORT}"
else
    bashio::log.warning "No MQTT broker is registered with the Supervisor."
    bashio::log.warning "If the Mosquitto broker add-on is installed, make sure it is started and 'Start on boot' is on, then restart Sonorium."
    bashio::log.warning "Using a different MQTT broker? Set sonorium__mqtt_host (and port/username/password) in Sonorium's configuration."
    # Export config values anyway - Python will handle the error
    export SONORIUM__MQTT_HOST="${MQTT_HOST_CONFIG}"
    export SONORIUM__MQTT_PORT="${MQTT_PORT_CONFIG}"
    export SONORIUM__MQTT_USERNAME="${MQTT_USER_CONFIG}"
    export SONORIUM__MQTT_PASSWORD="${MQTT_PASS_CONFIG}"
fi

bashio::log.info "Configuration:"
bashio::log.info "  Stream URL: ${SONORIUM__STREAM_URL}"
bashio::log.info "  Audio Path: ${SONORIUM__PATH_AUDIO}"
bashio::log.info "  Max Channels: ${SONORIUM__MAX_CHANNELS}"
bashio::log.info "  MQTT Host: ${SONORIUM__MQTT_HOST:-not set}"
bashio::log.info "  MQTT Port: ${SONORIUM__MQTT_PORT:-not set}"

# Create audio directory if it doesn't exist
if [ ! -d "${SONORIUM__PATH_AUDIO}" ]; then
    bashio::log.warning "Audio path does not exist, creating: ${SONORIUM__PATH_AUDIO}"
    mkdir -p "${SONORIUM__PATH_AUDIO}"
fi

# Test critical Python imports (helps diagnose segfaults)
# These tests run in the same order as sonorium imports them
bashio::log.info "Testing Python imports..."
IMPORTS_OK=true

# Test individual imports first
if ! python3 -c "import numpy; print(f'numpy {numpy.__version__}')" 2>&1; then
    bashio::log.error "FAILED: numpy import"
    IMPORTS_OK=false
    # A VM with a basic virtual CPU (Proxmox's default "kvm64") hides CPU
    # features that numpy builds can require (issues #18, #39).
    if [[ "$(uname -m)" == "x86_64" ]] && ! grep -qw sse4_2 /proc/cpuinfo; then
        bashio::log.error "This CPU doesn't report SSE4.2. If Home Assistant runs in a virtual machine (Proxmox, etc.),"
        bashio::log.error "set the VM's CPU type to 'host', then fully shut down and start the VM."
        bashio::log.error "In Proxmox: VM -> Hardware -> Processors -> Type: host."
    fi
fi
if ! python3 -c "import av; print(f'av {av.__version__}')" 2>&1; then
    bashio::log.error "FAILED: av (PyAV) import"
    IMPORTS_OK=false
fi
if ! python3 -c "import pydantic" 2>&1; then
    bashio::log.error "FAILED: pydantic import"
    IMPORTS_OK=false
fi
if ! python3 -c "import fastapi" 2>&1; then
    bashio::log.error "FAILED: fastapi import"
    IMPORTS_OK=false
fi

# Test combined imports (order matters - this is how recording.py imports them)
# This catches issues where individual imports work but combination causes segfault
bashio::log.info "Testing combined imports (numpy + av)..."
if ! python3 -c "import numpy; import av; print('Combined import OK')" 2>&1; then
    bashio::log.error "FAILED: Combined numpy+av import"
    bashio::log.error "This may indicate a compatibility issue with virtualized environments"
    bashio::log.error "Please report this issue with your HA OS version and architecture"
    IMPORTS_OK=false
fi

# Test the actual recording module import
bashio::log.info "Testing sonorium.recording import..."
if ! python3 -c "from sonorium.recording import RecordingMetadata; print('Recording module OK')" 2>&1; then
    bashio::log.error "FAILED: sonorium.recording import"
    IMPORTS_OK=false
fi

if [[ "${IMPORTS_OK}" == "true" ]]; then
    bashio::log.info "Python imports OK"
else
    bashio::log.error "One or more Python imports failed (see above). Sonorium will probably not start."
fi

# Check if sonorium command exists
if ! command -v sonorium &> /dev/null; then
    bashio::log.info "Running via Python module..."
    exec python3 -m sonorium.entrypoint
fi

bashio::log.info "Launching Sonorium..."

# Run sonorium
exec sonorium
