# Sigen EV Cloud to MQTT

Bridge Sigenergy cloud APIs for a standalone Sigen EV AC charger to MQTT, with Home Assistant discovery.

## Features (v0.2.4)

- Login to Sigenergy Cloud (mySigen credentials)
- **Current setpoint** get/set (MQTT number)
- **Start / stop** charging (MQTT buttons + `{prefix}/command`)
- **Status**, live **power/current/voltage**, **lifetime energy**
- **Active phases** inferred from realtime telemetry while charging
- **Phase mode** read/write (1-phase / 3-phase / auto), synced with mySigen Advanced Settings

## Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `username` | _(empty)_ | mySigen / cloud account email |
| `password` | _(empty)_ | Account password |
| `region` | `eu` | `eu`, `apac`, `us`, or `cn` |
| `poll_interval_seconds` | `15` | Cloud poll interval |
| `min_current` / `max_current` | `6` / `16` | Clamp range for the HA number |
| `mqtt_host` | `core-mosquitto` | MQTT broker |
| `topic_prefix` | `sigen_evac` | Root topic prefix |
| `device_name` | `Sigen EV AC` | HA device name |
| `log_level` | `INFO` | Log verbosity |

## Cloud API notes

| Action | Endpoint |
|--------|----------|
| Start charge | `PUT device/acevse/charge/start?enable=0&stationId=&snCode=` |
| Stop charge | `PUT device/acevse/charge/start?enable=1&stationId=&snCode=` |
| Status | `GET device/acevse/charge/status?stationId=` → `1 idle`, `2 preparing`, `3 charging` |
| Realtime | `GET device/acevse/charge/realtime?stationId=&snCode=` |
| Energy totals | `GET data-process/acevse/energy?stationId=&snCode=` |
| Phase mode read | `GET device/acevse/more-setting/{stationId}?snCode=` → `phaseType`, `phaseAutoSwitch` |
| Phase mode write | `POST device/acevse/more-setting` with `outputMode` + `phaseAutoSwitch` |

Phase mode mapping:

| HA select | Cloud calls |
|-----------|-------------|
| `1_phase` | `phaseAutoSwitch=0`, then `outputMode=0` (separate POSTs) |
| `3_phase` | `phaseAutoSwitch=0`, then `outputMode=1` |
| `auto` | `phaseAutoSwitch=1` only |

Read mapping (`phaseType` + `phaseAutoSwitch` from Advanced Settings):

| Cloud read | HA state |
|------------|----------|
| `phaseAutoSwitch=1` | `auto` |
| `phaseType=1` | `3_phase` |
| `phaseType=0` | `1_phase` |

The mySigen web app loads this from:

`GET device/acevse/more-setting/{stationId}?snCode=`

(same screen as `#/charger/.../charger-advanced-setting`). The bridge polls this
every cycle and publishes `{prefix}/phase_mode/state`.

Writes return `data: true` when accepted. Combined payloads with null fields often
return `data: false` — the bridge uses minimal partial POSTs and retries after stop
if needed.

## MQTT topics

Prefix default: `sigen_evac`

| Topic | Direction | Payload |
|-------|-----------|---------|
| `{prefix}/availability` | out | `online` / `offline` |
| `{prefix}/state` | out | JSON snapshot |
| `{prefix}/status` | out | `idle` / `preparing` / `charging` |
| `{prefix}/power_w` | out | watts |
| `{prefix}/current_a` | out | measured amps |
| `{prefix}/voltage_v` | out | volts |
| `{prefix}/active_phases` | out | `0–3` inferred |
| `{prefix}/lifetime_energy_kwh` | out | kWh (only published on valid cloud reads) |
| `{prefix}/lifetime_energy/availability` | out | `online` / `offline` — HA marks lifetime sensor unavailable when `offline` |
| `{prefix}/current/state` | out | setpoint amps |
| `{prefix}/current/set` | in | setpoint amps |
| `{prefix}/phase_mode/state` | out | `1_phase` / `3_phase` / `auto` / `unknown` |
| `{prefix}/phase_mode/set` | in | same values |
| `{prefix}/command` | in | `START` / `STOP` |

## Local development

```bash
cd sigenevcloud2mqtt
python3 -m venv .venv
source .venv/bin/activate.fish   # fish shell
pip install -r requirements.txt
cp .env.example .env
```

Cloud smoke test:

```bash
.venv/bin/python -m src.app --cloud-check
.venv/bin/python -m src.app --cloud-check --set-current 8
.venv/bin/python -m src.app --cloud-check --set-phase-mode 3_phase
```

Full bridge:

```bash
.venv/bin/python -m src.app
```

Probe helpers (dev only):

```bash
.venv/bin/python scripts/probe_cloud.py
.venv/bin/python scripts/probe_advanced_setting.py
```

## Replacing `ocppsigen2mqtt`

This add-on now covers start/stop, status, and live telemetry via cloud. To fully drop OCPP, point the charger back to default Sigenergy Cloud (not a local OCPP URL) so cloud start/stop works.
