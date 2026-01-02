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
    bashio::log.warning "MQTT service not available from Supervisor"
    bashio::log.warning "Set manual MQTT config or install Mosquitto broker addon"
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
bashio::log.info "Testing Python imports..."
if ! python3 -c "import numpy" 2>&1; then
    bashio::log.error "FAILED: numpy import"
fi
if ! python3 -c "import av" 2>&1; then
    bashio::log.error "FAILED: av (PyAV) import"
fi
if ! python3 -c "import pydantic" 2>&1; then
    bashio::log.error "FAILED: pydantic import"
fi
if ! python3 -c "import fastapi" 2>&1; then
    bashio::log.error "FAILED: fastapi import"
fi
bashio::log.info "Python imports OK"

# Check if sonorium command exists
if ! command -v sonorium &> /dev/null; then
    bashio::log.info "Running via Python module..."
    exec python3 -m sonorium.entrypoint
fi

bashio::log.info "Launching Sonorium..."

# Run sonorium
exec sonorium
