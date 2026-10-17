# Sigen EV Local to MQTT

Unofficial and **not affiliated with Sigenergy**. This add-on is a community Home Assistant bridge. Using it may affect hardware warranty or mySigen Terms of Service. Use at your own risk.

Bridge a standalone Sigen EV AC charger over the LAN NearTerminal path (TCP 21319) to MQTT, with Home Assistant discovery for the full firmware signal catalog.

This is the recommended Sigen EV bridge. The older **Sigen EV Cloud to MQTT** and **OCPP SIGEN to MQTT** add-ons are deprecated; migrate here when ready. Topic prefixes and device ids differ, so you can run this alongside a deprecated add-on during cutover.

## Features

- Local mTLS session to the charger (no Sigenergy cloud)
- **Reads** every EVAC RO/RW tag from the firmware catalog, plus meter power/energy when a DH meter is on the pole
- **Writes** every RW tag (current, modes, DLM, output mode, …)
- Fast poll for live telemetry, slower poll for the rest of the catalog
- Home Assistant MQTT **device discovery** (one retained config per charger); existing entity IDs and history are kept across the 0.2.0 migration
- Factory reset / data wipe / breaker-current writes stay gated behind `enable_dangerous_commands`
- Self-watchdog exits if the poll loop stalls or MQTT stays down for 5+ minutes — enable the Supervisor **Watchdog** toggle so the add-on restarts automatically

Local **start / stop** uses firmware tag 12000 (`0` start, `1` stop) — the same values as the mySigen app and the cloud add-on.

## Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `charger_host` | _(empty)_ | LAN IP of the EVAC pole (example `192.0.2.10`) |
| `charger_port` | `21319` | NearTerminal TLS port |
| `poll_interval_seconds` | `5` | Fast-tag poll interval |
| `slow_poll_multiplier` | `4` | Read the full catalog every Nth cycle |
| `min_current` / `max_current` | `6` / `16` | Clamp range for charge current **writes** (tag 12194); the published setpoint is the raw device value |
| `enable_dangerous_commands` | `false` | Expose factory reset (12002), data wipe (12003), and house/pole breaker current writes (12116 / 12118) |
| `mqtt_host` / `mqtt_port` / `mqtt_username` / `mqtt_password` | _(empty)_ | MQTT broker; when empty, filled from the Supervisor MQTT service if available |
| `topic_prefix` | `sigen_evac_local` | Root topic prefix — keep different from the cloud add-on (`sigen_evac`) |
| `device_name` | `Sigen EV AC Local` | HA device name |
| `log_level` | `INFO` | Log verbosity |

Leave the MQTT options blank on a supervised install with the Mosquitto add-on. Explicit values always win over auto-discovery.

## MQTT topics

Prefix default: `sigen_evac_local`

| Topic | Direction | Payload |
|-------|-----------|---------|
| `{prefix}/availability` | out | `online` / `offline` |
| `{prefix}/state` | out | JSON snapshot of featured tags |
| `{prefix}/status` | out | named run-state (tag 11265), e.g. `unplugged` / `charging` |
| `{prefix}/power_w` | out | watts (from tag 11220 kW) |
| `{prefix}/current_a` | out | phase-A amps |
| `{prefix}/voltage_v` | out | phase-A volts |
| `{prefix}/active_phases` | out | count of phases with \|I\| ≥ 0.3 A |
| `{prefix}/lifetime_energy_kwh` | out | kWh (tag 11350) |
| `{prefix}/current/state` | out | charge-current setpoint (raw device value) |
| `{prefix}/current/set` | in | charge-current setpoint (amps; clamped on write) |
| `{prefix}/command` | in | `START` / `STOP` |
| `{prefix}/tag/{id}/state` | out | decoded value for catalog tag `id` (named for known enums; secrets are never published) |
| `{prefix}/tag/{id}/set` | in | write that tag (RW/CMD only; enum selects accept the option name or the raw int) |

Discovery uses a single retained topic: `homeassistant/device/<device_id>/config`.

### Named mode values

| Entity | Options |
|--------|---------|
| Auth mode | `auto_charge`, `authentication` |
| Work mode | `fast_charging`, `green_charging`, `eco_charging` |
| Control mode | `local`, `ocpp` |
| Output mode | `1_phase`, `3_phase`, `3_phase_4wire`, `biphase` |
| DLM enable | `off`, `on` |
| DLM mode | hidden by default (raw enum; names TBD) |
| Single-phase sequence | `L1`, `L2`, `L3`, `system_automatic` |
| Run state | `unplugged`, `plugged`, `preparing`, `charging`, `fault`, `reserving`, `occupied_a`/`_b`/`_c`, `unavailable`, `offline` |
| Plug status | `unplugged`, `plugged` |

## Safety

- File/upgrade/activation commands (35–39) are **not** implemented.
- Factory reset, data wipe, and house/pole breaker current are hidden unless `enable_dangerous_commands` is enabled.
- Wi-Fi password and APN tags are write-only text entities (`mode: password`); values are never polled or published.
- Reboot (12001) is a disabled-by-default button.

## Reliability

- Enable the add-on **Watchdog** in the Supervisor UI. If the poll loop stalls (`10 × poll_interval`, minimum 300 s) or MQTT stays disconnected for more than 5 minutes, the process exits so Supervisor can restart it. A charger that is simply unreachable stays `offline` via availability and does **not** force an exit.

## Local development

```bash
cd sigenevlocal2mqtt
python3 -m venv .venv
source .venv/bin/activate.fish   # fish shell
pip install -r requirements-dev.txt
cp .env.example .env
```

Charger smoke test:

```bash
.venv/bin/python -m src.app --local-check
.venv/bin/python -m src.app --local-check --set-current 8
```

Full bridge:

```bash
.venv/bin/python -m src.app
```
