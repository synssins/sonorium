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

bashio::log.info "Configuration:"
bashio::log.info "  Stream URL: ${SONORIUM__STREAM_URL}"
bashio::log.info "  Audio Path: ${SONORIUM__PATH_AUDIO}"
bashio::log.info "  Max Channels: ${SONORIUM__MAX_CHANNELS}"

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
