# Sungrow Modbus to MQTT

Unofficial local Modbus TCP bridge for **Sungrow residential hybrid inverters** (SH / MG series). Publishes decoded registers to MQTT with Home Assistant discovery. Not affiliated with Sungrow.

## Requirements

- Sungrow hybrid inverter reachable over Modbus TCP (WiNet-S / WiNet-S2 Ethernet, port **502**)
- MQTT broker (Mosquitto add-on recommended)

## Protocol

Register map derived from Sungrow **Communication Protocol of Residential Hybrid Inverter V1.1.11** (2025-11-17). The PDF is not redistributed here.

Important protocol details:

- Documented register address *N* is accessed at Modbus address *N − 1*
- Read-only values use function code 0x04 (input registers)
- Settings use 0x03 / 0x06 / 0x10 (holding registers)
- 32-bit values are word-swapped (low word first)
- Unavailable values return type-specific sentinels and are not published

Historical time-series blocks (addresses 6100–6826) are omitted from the catalog — they are large and often unsupported via WiNet forwarding.

## Configuration

| Option | Default | Notes |
|--------|---------|-------|
| `modbus_host` | `192.168.50.35` | Inverter / WiNet Modbus TCP host |
| `modbus_port` | `502` | |
| `modbus_unit_id` | `1` | |
| `poll_interval_seconds` | `10` | Fast-path poll interval |
| `slow_poll_multiplier` | `6` | Slow RO registers every N cycles |
| `allow_writes` | `false` | Master write gate |
| `enable_ems_writes` | `false` | EMS / export / charge-discharge (requires `allow_writes`) |
| `legacy_modqtt_compat` | `true` | Keep existing `sensor.sungrow_inverter_*` entity IDs |
| `topic_prefix` | `sungrowmod2mqtt` | State topics under this prefix |
| MQTT host/user/pass | empty | Falls back to Supervisor MQTT service |

## Topics

- `{prefix}/availability` — `online` / `offline` (LWT)
- `{prefix}/state` — JSON snapshot of core (enabled-by-default) values
- `{prefix}/{slug}/state` — per-register state
- `{prefix}/{slug}/set` — write requests (when writes enabled)

## Writes

Keep `allow_writes` **false** unless you intentionally control the inverter. EMS-related registers (EMS mode, forced charge/discharge, export limits, SoC limits, off-grid, …) additionally require `enable_ems_writes: true`.

Via WiNet, holding registers must not be polled densely; this add-on refreshes them at startup, after each write, and every 5 minutes.

## Migrating from ModQTT

If you currently use ModQTT with Sungrow discovery:

1. Install this add-on; leave `legacy_modqtt_compat: true`.
2. Prefer a read-only check first (local `--local-check` or start with writes disabled).
3. **Stop ModQTT** (do not uninstall yet) so two Modbus clients do not compete.
4. Start **Sungrow Modbus to MQTT**.
5. Confirm Energy Dashboard solar sensors (`sensor.sungrow_inverter_total_pv_generation`, `sensor.sungrow_inverter_total_dc_power`) keep updating with history intact.

With compatibility enabled, discovery for the 22 previous ModQTT sensors reuses `unique_id` values `modqtt-addon_{suffix}` so Home Assistant updates those entities in place. New registers use `sungrow_{serial}_{slug}` unique IDs.

## Local development

```bash
cd sungrowmod2mqtt
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # edit MODBUS_HOST / MQTT_*
python -m src.app --local-check
pytest
```
