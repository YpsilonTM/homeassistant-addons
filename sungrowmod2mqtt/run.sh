#!/usr/bin/env bash
set -euo pipefail

# Home Assistant base image ships bashio; source it when present (local runs may skip).
if [ -f /usr/lib/bashio/bashio.sh ]; then
    # shellcheck disable=SC1091
    source /usr/lib/bashio/bashio.sh
fi

if command -v bashio >/dev/null 2>&1 || declare -F bashio::config >/dev/null 2>&1; then
    export MODBUS_HOST="$(bashio::config 'modbus_host')"
    export MODBUS_PORT="$(bashio::config 'modbus_port')"
    export MODBUS_UNIT_ID="$(bashio::config 'modbus_unit_id')"
    export MODBUS_TIMEOUT_SECONDS="$(bashio::config 'modbus_timeout_seconds')"
    export POLL_INTERVAL_SECONDS="$(bashio::config 'poll_interval_seconds')"
    export SLOW_POLL_MULTIPLIER="$(bashio::config 'slow_poll_multiplier')"
    export ALLOW_WRITES="$(bashio::config 'allow_writes')"
    export ENABLE_EMS_WRITES="$(bashio::config 'enable_ems_writes')"
    export LEGACY_MODQTT_COMPAT="$(bashio::config 'legacy_modqtt_compat')"
    export MQTT_CLIENT_ID="$(bashio::config 'mqtt_client_id')"
    export MQTT_TOPIC_PREFIX="$(bashio::config 'topic_prefix')"
    export DEVICE_NAME="$(bashio::config 'device_name')"
    export LOG_LEVEL="$(bashio::config 'log_level')"

    if bashio::config.has_value 'mqtt_host'; then
        export MQTT_HOST="$(bashio::config 'mqtt_host')"
    fi
    if bashio::config.has_value 'mqtt_port'; then
        export MQTT_PORT="$(bashio::config 'mqtt_port')"
    fi
    if bashio::config.has_value 'mqtt_username'; then
        export MQTT_USER="$(bashio::config 'mqtt_username')"
    fi
    if bashio::config.has_value 'mqtt_password'; then
        export MQTT_PASS="$(bashio::config 'mqtt_password')"
    fi
fi

# When MQTT options are empty/null, fall back to Supervisor MQTT service discovery.
if command -v bashio >/dev/null 2>&1 || declare -F bashio::services.available >/dev/null 2>&1; then
    if [ -z "${MQTT_HOST:-}" ] && bashio::services.available "mqtt" 2>/dev/null; then
        export MQTT_HOST="$(bashio::services mqtt "host")"
    fi
    if [ -z "${MQTT_PORT:-}" ] && bashio::services.available "mqtt" 2>/dev/null; then
        export MQTT_PORT="$(bashio::services mqtt "port")"
    fi
    if [ -z "${MQTT_USER:-}" ] && bashio::services.available "mqtt" 2>/dev/null; then
        export MQTT_USER="$(bashio::services mqtt "username")"
    fi
    if [ -z "${MQTT_PASS:-}" ] && bashio::services.available "mqtt" 2>/dev/null; then
        export MQTT_PASS="$(bashio::services mqtt "password")"
    fi
fi

cd /app
exec python3 -m src.app
