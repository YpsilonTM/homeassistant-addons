# Sigen EV Local to MQTT

Unofficial and **not affiliated with Sigenergy**. This add-on is a community Home Assistant bridge. Using it may affect hardware warranty or mySigen Terms of Service. Use at your own risk.

Bridge a standalone Sigen EV AC charger over the LAN NearTerminal path (TCP 21319) to MQTT, with Home Assistant discovery for the full firmware signal catalog.

This add-on is independent of **Sigen EV Cloud to MQTT**. Both can run at the same time because they use different topic prefixes and device ids.

## Features

- Local mTLS session to the charger (no Sigenergy cloud)
- **Reads** every EVAC RO/RW tag from the firmware catalog, plus meter power/energy when a DH meter is on the pole
- **Writes** every RW tag (current, modes, DLM, output mode, …)
- Fast poll for live telemetry, slower poll for the rest of the catalog
- Home Assistant MQTT discovery: featured entities on by default, the rest available but disabled
- Factory reset / data wipe stay gated behind `enable_dangerous_commands`

Local **start / stop** uses firmware tag 12000 (`0` start, `1` stop) — the same values as the mySigen app and the cloud add-on.

## Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `charger_host` | _(empty)_ | LAN IP of the EVAC pole (example `192.168.50.36`) |
| `charger_port` | `21319` | NearTerminal TLS port |
| `poll_interval_seconds` | `5` | Fast-tag poll interval |
| `slow_poll_multiplier` | `4` | Read the full catalog every Nth cycle |
| `min_current` / `max_current` | `6` / `16` | Clamp range for charge current (tag 12194) |
| `enable_dangerous_commands` | `false` | Expose factory reset (12002) and data wipe (12003) |
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
| `{prefix}/current/state` | out | charge-current setpoint |
| `{prefix}/current/set` | in | charge-current setpoint (amps) |
| `{prefix}/command` | in | `START` / `STOP` |
| `{prefix}/tag/{id}/state` | out | decoded value for catalog tag `id` (named for known enums) |
| `{prefix}/tag/{id}/set` | in | write that tag (RW/CMD only; enum selects accept the option name or the raw int) |

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
- Factory reset and data wipe are hidden unless `enable_dangerous_commands` is enabled.
- Wi-Fi password and APN tags exist as disabled text entities (`mode: password`).
- Reboot (12001) is a disabled-by-default button.

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
