#!/usr/bin/env bash
set -euo pipefail

if [ -f /data/options.json ]; then
    eval "$({
        python3 - <<'PY'
import json
import shlex
from pathlib import Path

options_path = Path('/data/options.json')
with options_path.open('r', encoding='utf-8') as fh:
    options = json.load(fh)

env_map = {
    'SIGEN_USERNAME': options.get('username', ''),
    'SIGEN_PASSWORD': options.get('password', ''),
    'SIGEN_REGION': options.get('region', 'eu'),
    'POLL_INTERVAL_SECONDS': options.get('poll_interval_seconds', 15),
    'MIN_CURRENT': options.get('min_current', 6),
    'MAX_CURRENT': options.get('max_current', 16),
    'MQTT_HOST': options.get('mqtt_host', 'core-mosquitto'),
    'MQTT_PORT': options.get('mqtt_port', 1883),
    'MQTT_USER': options.get('mqtt_username', ''),
    'MQTT_PASS': options.get('mqtt_password', ''),
    'MQTT_CLIENT_ID': options.get('mqtt_client_id', 'sigenevcloud2mqtt-addon'),
    'MQTT_TOPIC_PREFIX': options.get('topic_prefix', 'sigen_evac'),
    'DEVICE_NAME': options.get('device_name', 'Sigen EV AC'),
    'LOG_LEVEL': options.get('log_level', 'INFO'),
}

for key, value in env_map.items():
    print(f"export {key}={shlex.quote(str(value))}")
PY
    })"
fi

cd /app
exec python3 -m src.app
