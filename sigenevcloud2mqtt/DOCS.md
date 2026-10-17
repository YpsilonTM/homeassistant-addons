# Sigen EV Cloud to MQTT

> **Deprecated.** This add-on is no longer maintained. Use
> [Sigen EV Local to MQTT](../sigenevlocal2mqtt/DOCS.md) instead — local
> NearTerminal control is the recommended way to steer the charger.

Bridge Sigenergy cloud APIs for a standalone Sigen EV AC charger to MQTT, with Home Assistant discovery.

## Features

- Login to Sigenergy Cloud (mySigen credentials)
- **Current setpoint** get/set (MQTT number)
- **Start / stop** charging (MQTT buttons + `{prefix}/command`)
- **Status**, live **power/current/voltage**, **lifetime energy**
- **Active phases** inferred from realtime telemetry while charging
- **Phase mode** read/write via MQTT select (`1_phase` / `3_phase` / `auto`), synced with mySigen Advanced Settings

Phase mode is controlled only through the select entity (and `{prefix}/phase_mode/set`). The older duplicate Set 1-Phase / 3-Phase / Auto buttons were removed in 0.3.0.

## Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `username` | _(empty)_ | mySigen / cloud account email |
| `password` | _(empty)_ | Account password |
| `region` | `eu` | `eu`, `apac`, `us`, or `cn` |
| `poll_interval_seconds` | `15` | Cloud poll interval |
| `slow_poll_multiplier` | `4` | Fetch energy totals and advanced settings every Nth poll cycle (1–60) |
| `min_current` / `max_current` | `6` / `16` | Clamp range for the HA number |
| `mqtt_host` / `mqtt_port` / `mqtt_username` / `mqtt_password` | _(empty)_ | MQTT broker; when empty, filled from the Supervisor MQTT service if available |
| `topic_prefix` | `sigen_evac` | Root topic prefix |
| `device_name` | `Sigen EV AC` | HA device name |
| `log_level` | `INFO` | Log verbosity |

Leave the MQTT options blank on a supervised install with the Mosquitto add-on (or another broker exposed via Supervisor). Explicit values always win over auto-discovery.

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

(same screen as `#/charger/.../charger-advanced-setting`). Energy totals and Advanced Settings are refreshed every `slow_poll_multiplier` poll cycles (default every 4th cycle). Phase mode state is published on `{prefix}/phase_mode/state`.

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
| `{prefix}/phase_mode/state` | out | `1_phase` / `3_phase` / `auto` |
| `{prefix}/phase_mode/set` | in | same values |
| `{prefix}/phase_mode/availability` | out | `online` / `offline` — select is unavailable while mode is unknown |
| `{prefix}/command` | in | `START` / `STOP` |

The phase mode select options are exactly `1_phase`, `3_phase`, and `auto` (`unknown` is not an option). When the cloud mode cannot be decoded, the bridge publishes `offline` on `{prefix}/phase_mode/availability` (with `availability_mode: all` on the discovery config) instead of exposing `unknown` as a selectable state.

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

## Migration

Both this add-on and `ocppsigen2mqtt` are deprecated in favor of
[Sigen EV Local to MQTT](../sigenevlocal2mqtt/DOCS.md). Install that add-on,
point it at the charger’s LAN IP, and stop this one when ready.
