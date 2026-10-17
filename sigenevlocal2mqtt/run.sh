#!/usr/bin/env bash
set -euo pipefail

# Home Assistant base image ships bashio; source it when present (local runs may skip).
if [ -f /usr/lib/bashio/bashio.sh ]; then
    # shellcheck disable=SC1091
    source /usr/lib/bashio/bashio.sh
fi

if command -v bashio >/dev/null 2>&1 || declare -F bashio::config >/dev/null 2>&1; then
    export CHARGER_HOST="$(bashio::config 'charger_host')"
    export CHARGER_PORT="$(bashio::config 'charger_port')"
    export POLL_INTERVAL_SECONDS="$(bashio::config 'poll_interval_seconds')"
    export SLOW_POLL_MULTIPLIER="$(bashio::config 'slow_poll_multiplier')"
    export MIN_CURRENT="$(bashio::config 'min_current')"
    export MAX_CURRENT="$(bashio::config 'max_current')"
    export ENABLE_DANGEROUS_COMMANDS="$(bashio::config 'enable_dangerous_commands')"
    export MQTT_CLIENT_ID="$(bashio::config 'mqtt_client_id')"
    export MQTT_TOPIC_PREFIX="$(bashio::config 'topic_prefix')"
    export DEVICE_NAME="$(bashio::config 'device_name')"
    export LOG_LEVEL="$(bashio::config 'log_level')"

    if bashio::config.has_value 'mqtt_host'; then
        export MQTT_HOST="$(bashio::config 'mqtt_host')"
    elif bashio::services.available "mqtt" 2>/dev/null; then
        export MQTT_HOST="$(bashio::services mqtt 'host')"
    fi

    if bashio::config.has_value 'mqtt_port'; then
        export MQTT_PORT="$(bashio::config 'mqtt_port')"
    elif bashio::services.available "mqtt" 2>/dev/null; then
        export MQTT_PORT="$(bashio::services mqtt 'port')"
    fi

    if bashio::config.has_value 'mqtt_username'; then
        export MQTT_USER="$(bashio::config 'mqtt_username')"
    elif bashio::services.available "mqtt" 2>/dev/null; then
        export MQTT_USER="$(bashio::services mqtt 'username')"
    fi

    if bashio::config.has_value 'mqtt_password'; then
        export MQTT_PASS="$(bashio::config 'mqtt_password')"
    elif bashio::services.available "mqtt" 2>/dev/null; then
        export MQTT_PASS="$(bashio::services mqtt 'password')"
    fi
fi

cd /app
exec python3 -m src.app
