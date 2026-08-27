# Plan: `sigenevcloud2mqtt` Home Assistant Add-on

## Why
- Standalone Sigen EV AC: Modbus not enabled (support refused); ESP RS485 TX works, no RX.
- OCPP Smart Charging still “in development” per Sigenergy — cannot set current reliably.
- Current **can** be set via https://app-eu.sigencloud.com/ (same APIs as mySigen).
- PyPI package `sigen` already wraps EVAC current get/set — **no HA integration exposes this today**.
- This repo already ships MQTT bridges as add-ons (`ocppsigen2mqtt`, `modqtt`). **Add-on + MQTT is the right path** (not a full HACS integration for v1).

## Goal (revised)
Add-on **`sigenevcloud2mqtt`** is the **full EVAC control surface via Sigenergy Cloud**, intended to **replace `ocppsigen2mqtt`**.

It must cover everything useful that OCPP gives today, **plus** what OCPP cannot do (set current):

| Capability | OCPP add-on today | Cloud add-on target |
|------------|-------------------|---------------------|
| Start / stop charging | RemoteStart / RemoteStop | Cloud START/STOP (same as mySigen app) |
| Status | StatusNotification | Cloud charger / session status |
| Live power / current / energy | MeterValues | Cloud poll (energy flow / device card) |
| Set charging current (A) | ❌ broken (SetChargingProfile) | ✅ cloud PUT current |
| Optional: watts → amps helper | planned / non-functional on OCPP | compute locally, then cloud set current |
| Reset / get_config / set_config | OCPP only | out of scope unless cloud endpoints found |
| App-access authorize gate | OCPP Authorize Blocked | N/A on cloud path (charger stays on Sigen Cloud) |

Keep `ocppsigen2mqtt` running only until cloud start/stop + telemetry are proven. Then drop it and point the charger back to **default Sigen Cloud** (not a local OCPP URL).

### Critical prerequisite to drop OCPP
Sigenergy docs: when the charger is connected to an **external OCPP server**, start/stop identity moves to that OCPP server and away from Sigen Cloud. Cloud start/stop (and likely app START/STOP) only work when the charger uses Sigenergy’s proprietary cloud link again.

Migration path:
1. Build + validate cloud current + telemetry while OCPP still owns start/stop if needed.
2. Discover / confirm cloud start/stop.
3. Switch charger OCPP URL back to Sigen Cloud default.
4. Retire `ocppsigen2mqtt`.

## New folder layout

```
sigenevcloud2mqtt/
  config.yaml
  Dockerfile
  run.sh
  DOCS.md
  CHANGELOG.md
  src/
    app.py           # main loop: poll + command handling
    cloud.py         # thin Sigenergy cloud client (prefer over full `sigen` package)
    mqtt_bridge.py   # connect, discovery, pub/sub
```

Also update root `README.md` to list the add-on. Leave `repository.yaml` unchanged.
Add CI workflow mirroring the other add-ons (`addon-sigenevcloud2mqtt` image).

## Patterns to copy
- **Python + venv Dockerfile** → follow `modqtt/Dockerfile` (not Bun; need AES + aiohttp).
- **Options → env** → follow `ocppsigen2mqtt/run.sh` (`/data/options.json`).
- **MQTT discovery** → follow `ocppsigen2mqtt/src/index.ts` style, but richer entity set.
- Image name: `ghcr.io/ypsilontm/hassaddons/addon-sigenevcloud2mqtt`.

## Cloud API

Base (region-dependent): `https://api-{eu|apac|us|cn}.sigencloud.com/`

### Confirmed (from `sigen` 3.0.3)

| Action | Call |
|--------|------|
| Login | `POST auth/oauth/token` — Basic auth `sigen:sigen`; password AES-CBC encrypted with key/iv `sigensigensigenp` |
| Station | `GET device/owner/station/home` → `stationId`, `acSnList`, `hasAcCharger` |
| Read current | `GET device/acevse/charge/read/current?stationId=` → `lastSetCurrent`, `maxCurrent`, `dlmStatus` |
| Set current | `PUT device/acevse/charge/current?stationId=&current=` |
| Charge mode | `GET device/acevse/charge/mode?stationId=&snCode=` |
| DLM toggle | `POST device/acevse/more-setting` with `dlmStatus` |
| Energy flow | `GET device/sigen/station/energyflow?id=` (likely EV power for sensors) |

**Prefer a thin client** in `cloud.py` (auth + station + EVAC + energyflow only). Full `sigen.async_initialize()` also pulls smart-loads / dynamic modes and can break on EVAC-only stations.

### To discover (required for OCPP replacement)

App can START/STOP an AC charger; those HTTP calls are **not** wrapped in `sigen` yet. Capture from mySigen / web app (browser network tab or MITM) and document here before coding start/stop:

| Action | Expected (TBD) | Notes |
|--------|----------------|-------|
| Start charge | e.g. `…/device/acevse/…` | Confirm path + body (`stationId`, `snCode`) |
| Stop charge | matching stop | |
| Session / status | device card or energyflow fields | Map to HA status string |
| Live power / energy | energyflow or AC device endpoints | Prefer real cloud values over inventing phase V/A |

Until start/stop endpoints are confirmed, ship current + read-only telemetry first; do not retire OCPP.

## HA entities (MQTT discovery) — parity + more

One device: `device_name` (default `Sigen EV AC`), identifier from `ac_sn` / station.

**Must have (replace OCPP):**
- `button` Start Charging
- `button` Stop Charging
- `sensor` Status
- `sensor` Power (W)
- `sensor` Current (A) — measured if available, else derived from power
- `sensor` Energy (Wh / kWh as available)
- `binary_sensor` Bridge availability

**Cloud-only upgrade:**
- `number` Charging current setpoint (6–16 A for 11 kW) — **the reason this add-on exists**
- Optional `number` / command for target watts → convert with `usable_phases` + 230 V → clamp → set current

**Nice later:**
- `sensor` Max current (diagnostic)
- `sensor` / `switch` DLM status
- Charge mode select if useful

## `config.yaml` options (suggested)

```yaml
username: ""
password: ""
region: eu                    # eu | apac | us | cn
poll_interval_seconds: 15     # tighter than “current-only” plan; OCPP was event-driven
min_current: 6
max_current: 16
usable_phases: 2              # for watts→amps helper (1|2|3)
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: ""
mqtt_password: ""
mqtt_client_id: sigenevcloud2mqtt-addon
topic_prefix: sigen_evac
device_name: Sigen EV AC
log_level: INFO
```

Schema: password as `password`, region/usable_phases/log_level as lists, ints with ranges.

## MQTT topics

Prefix default: `sigen_evac`

| Topic | Direction | Payload |
|-------|-----------|---------|
| `{prefix}/availability` | out | `online` / `offline` |
| `{prefix}/state` | out | JSON: `status`, `power_w`, `current_a`, `energy_wh`, `setpoint_a`, `max_current` |
| `{prefix}/current/state` | out | setpoint amps (for number entity) |
| `{prefix}/current/set` | in | setpoint amps from HA |
| `{prefix}/command` | in | `START` / `STOP` (match OCPP add-on simplicity) |
| `{prefix}/command/set_power_watts` | in | `{"watts": 3000}` → amps → cloud set current |
| `homeassistant/.../config` | out | discovery for all entities above |

Number entity: name `Sigen EV Laadstroom`, unit `A`, icon `mdi:current-ac`, step 1, min/max from options (also clamp to cloud `maxCurrent`).

## Runtime loop

1. MQTT connect → LWT/availability + discovery
2. Cloud login → resolve `stationId` / `ac_sn`
3. Every poll: refresh token if needed → GET current + status/telemetry → publish state if changed
4. On `{prefix}/current/set`: clamp → PUT current → GET → publish
5. On `{prefix}/command` START/STOP: cloud start/stop → poll → publish
6. On `set_power_watts`: `amps = floor(watts / (usable_phases * 230))`, clamp `[min,max]` ∩ cloud max → set current
7. Refresh OAuth token before expiry

## Dependencies (Dockerfile)
`aiohttp`, `paho-mqtt`, `pycryptodome`

## Docs (`DOCS.md`)
- Cloud dependency (internet required)
- Credentials in add-on options
- **How to leave OCPP** (restore Sigen Cloud on charger) once this add-on is trusted
- MQTT topic table + discovery notes
- Watts→amps formula and phase assumption
- Not a replacement for local Modbus (ESP project stays parked until Modbus is enabled)

## Out of scope v1
- Plant / battery cloud entities (PV, SOC, EMS modes)
- Full HACS integration
- ESP / Modbus changes
- OCPP server inside this add-on (cloud replaces OCPP; do not dual-stack)
- Reset / ChangeConfiguration unless cloud equivalents appear

## Phased delivery

1. **Skeleton** — add-on packaging, login, station resolve, MQTT discovery stubs  
2. **Current control** — get/set current number entity (proven APIs)  
3. **Telemetry** — status / power / energy from energyflow or device endpoints  
4. **Start/stop** — after capturing app APIs; buttons + command topic  
5. **Watts helper** — optional command / number  
6. **Cutover** — document OCPP retirement; remove or archive `ocppsigen2mqtt` when ready  

## Install checklist
1. Open this repo: `/home/ypsilon/development/homeassistant/homeassistant-addons`
2. Implement `sigenevcloud2mqtt/` as above
3. HA → Add-on store → refresh YpsilonTM repo → install
4. Enter mySigen user/pass + MQTT
5. Verify setpoint number updates the web app
6. Capture + implement start/stop; verify against app
7. Point charger off local OCPP → Sigen Cloud; stop `ocppsigen2mqtt`

## Related parked work
ESPHome bridge: `/home/ypsilon/development/hardware/esphome/sigenbus` — useful only if Modbus is ever enabled.
