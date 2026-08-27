# Plan: `sigenevcloud2mqtt` Home Assistant Add-on

## Why
- Standalone Sigen EV AC: Modbus not enabled (support refused); ESP RS485 TX works, no RX.
- OCPP Smart Charging still “in development” per Sigenergy — cannot set current reliably.
- Current **can** be set via https://app-eu.sigencloud.com/ (same APIs as mySigen).
- PyPI package `sigen` already wraps EVAC current get/set — **no HA integration exposes this today**.
- This repo already ships MQTT bridges as add-ons (`ocppsigen2mqtt`, `modqtt`). **Add-on + MQTT is the right path** (not a full HACS integration for v1).

## Goal
Add-on **`sigenevcloud2mqtt`**:
1. Login to Sigenergy Cloud (EU) with mySigen credentials
2. Poll EVAC setpoint / max current
3. Expose HA **`number`** (6–16 A for 11 kW) via MQTT discovery
4. On HA change → cloud PUT to set current

Keep existing `ocppsigen2mqtt` for start/stop/status if useful; this add-on owns **current**.

## New folder layout

```
sigenevcloud2mqtt/
  config.yaml
  Dockerfile
  run.sh
  DOCS.md
  CHANGELOG.md
  src/
    app.py           # main loop
    cloud.py         # thin Sigenergy cloud client (prefer over full `sigen` package)
    mqtt_bridge.py   # connect, discovery, pub/sub
```

Also update root `README.md` to list the add-on. Leave `repository.yaml` unchanged.

## Patterns to copy
- **Python + venv Dockerfile** → follow `modqtt/Dockerfile` (not Bun; need AES + aiohttp).
- **Options → env** → follow `ocppsigen2mqtt/run.sh` (`/data/options.json`).
- **MQTT discovery** → follow `ocppsigen2mqtt/src/index.ts` style (`homeassistant/number/.../config`).
- Image name: `ghcr.io/ypsilontm/hassaddons/addon-sigenevcloud2mqtt`.

## Cloud API (from `sigen` 3.0.3)

Base: `https://api-eu.sigencloud.com/`

| Action | Call |
|--------|------|
| Login | `POST auth/oauth/token` — Basic auth `sigen:sigen`; password AES-CBC encrypted with key/iv `sigensigensigenp` |
| Station | `GET device/owner/station/home` → `stationId`, `acSnList` |
| Read current | `GET device/acevse/charge/read/current?stationId=` → `lastSetCurrent`, `maxCurrent`, `dlmStatus` |
| Set current | `PUT device/acevse/charge/current?stationId=&current=` |

**Prefer a thin client** in `cloud.py` (auth + station + get/set only). Full `sigen.async_initialize()` also pulls smart-loads / dynamic modes and can break on EVAC-only stations.

## `config.yaml` options (suggested)

```yaml
username: ""
password: ""
region: eu                    # eu | apac | us | cn
poll_interval_seconds: 30
min_current: 6
max_current: 16
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: ""
mqtt_password: ""
mqtt_client_id: sigenevcloud2mqtt-addon
topic_prefix: sigen_evac
device_name: Sigen EV AC
log_level: INFO
```

Schema: password as `password`, region as list, ints with ranges, log_level list.

## MQTT topics

Prefix default: `sigen_evac`

| Topic | Direction | Payload |
|-------|-----------|---------|
| `{prefix}/availability` | out | `online` / `offline` |
| `{prefix}/current/state` | out | amps (int/float string) |
| `{prefix}/current/set` | in | amps from HA |
| `{prefix}/max_current/state` | out | diagnostic |
| `homeassistant/number/sigen_evac_laadstroom/config` | out | discovery JSON |

Number entity: name `Sigen EV Laadstroom`, unit `A`, icon `mdi:current-ac`, step 1, min/max from options (also clamp to cloud `maxCurrent`).

## Runtime loop

1. MQTT connect → LWT/availability + discovery
2. Cloud login → resolve `stationId`
3. Every poll: GET current → publish state if changed
4. On `{prefix}/current/set`: clamp → PUT → GET → publish
5. Refresh OAuth token before expiry

## Dependencies (Dockerfile)
`aiohttp`, `paho-mqtt`, `pycryptodome`

## Docs (`DOCS.md`)
- Cloud dependency (internet required)
- Credentials in add-on options
- Works alongside `ocppsigen2mqtt`
- MQTT topic table + discovery notes
- Not a replacement for local Modbus (ESP project stays parked)

## Out of scope v1
- Plant/battery cloud entities
- Full HACS integration
- ESP / Modbus changes
- Start/stop via cloud (unless endpoints confirmed later)

## Install checklist
1. Open this repo in Cursor: `/home/ypsilon/development/homeassistant/homeassistant-addons`
2. Implement `sigenevcloud2mqtt/` as above
3. HA → Add-on store → refresh YpsilonTM repo → install
4. Enter mySigen user/pass + MQTT
5. Verify `number.sigen_evac_laadstroom` and that web app current updates

## Related parked work
ESPHome bridge: `/home/ypsilon/development/hardware/esphome/sigenbus` — useful only if Modbus is ever enabled.
