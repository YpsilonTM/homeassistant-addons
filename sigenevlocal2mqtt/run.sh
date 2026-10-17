#!/usr/bin/env bash
set -euo pipefail

# Home Assistant base image ships bashio; source it when present (local runs may skip).
# Options are read from /data/options.json (always mounted). bashio::config hits the
# Supervisor API and can fail with "forbidden" on some installs — do not use it here.
if [ -f /usr/lib/bashio/bashio.sh ]; then
    # shellcheck disable=SC1091
    source /usr/lib/bashio/bashio.sh
fi

if [ -f /data/options.json ]; then
    eval "$({
        python3 - <<'PY'
import json
import shlex
from pathlib import Path

options_path = Path('/data/options.json')
with options_path.open('r', encoding='utf-8') as fh:
    options = json.load(fh)

def opt(key, default=""):
    value = options.get(key, default)
    return "" if value is None else value

env_map = {
    'CHARGER_HOST': opt('charger_host'),
    'CHARGER_PORT': opt('charger_port', 21319),
    'POLL_INTERVAL_SECONDS': opt('poll_interval_seconds', 5),
    'SLOW_POLL_MULTIPLIER': opt('slow_poll_multiplier', 4),
    'MIN_CURRENT': opt('min_current', 6),
    'MAX_CURRENT': opt('max_current', 16),
    'ENABLE_DANGEROUS_COMMANDS': opt('enable_dangerous_commands', False),
    'MQTT_HOST': opt('mqtt_host'),
    'MQTT_PORT': opt('mqtt_port'),
    'MQTT_USER': opt('mqtt_username'),
    'MQTT_PASS': opt('mqtt_password'),
    'MQTT_CLIENT_ID': opt('mqtt_client_id', 'sigenevlocal2mqtt-addon'),
    'MQTT_TOPIC_PREFIX': opt('topic_prefix', 'sigen_evac_local'),
    'DEVICE_NAME': opt('device_name', 'Sigen EV AC Local'),
    'LOG_LEVEL': opt('log_level', 'INFO'),
}

for key, value in env_map.items():
    print(f"export {key}={shlex.quote(str(value))}")
PY
    })"
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
