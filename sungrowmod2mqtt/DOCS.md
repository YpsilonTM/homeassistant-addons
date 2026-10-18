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
| `modbus_host` | _(required)_ | Inverter / WiNet Modbus TCP host |
| `modbus_port` | `502` | |
| `modbus_unit_id` | `1` | |
| `poll_interval_seconds` | `10` | Fast-path poll interval |
| `slow_poll_multiplier` | `6` | Slow RO registers every N cycles |
| `allow_writes` | `false` | Master write gate |
| `enable_ems_writes` | `false` | EMS / export / charge-discharge (requires `allow_writes`) |
| `legacy_modqtt_compat` | `false` | If true, reuse old ModQTT `unique_id`s for selected sensors |
| `registers` | core set on | Per-register toggles grouped by category (see below) |
| `topic_prefix` | `sungrowmod2mqtt` | State topics under this prefix |
| MQTT host/user/pass | empty | Falls back to Supervisor MQTT service |

Enable the add-on **Watchdog** toggle in the Supervisor so a stuck poll loop or long MQTT outage triggers an automatic restart.

## Choosing registers

The **Registers** section lists every catalog register as an on/off toggle, grouped by category (`power`, `energy`, `mppt`, `battery`, `ems`, …).

- Defaults match the previous core set (`enabled_by_default` in the catalog): about 16 sensors on, the rest off.
- A register that is **off** is not read over Modbus and is not published as a Home Assistant entity (discovery uses a stub so HA removes it).
- Turning a register **on** starts polling it and creates the entity.
- Holding / EMS controls still require `allow_writes` / `enable_ems_writes` in addition to their toggle.
- Identity registers (serial, device type, …) are always read at startup so the bridge can start; their toggles only control whether they appear as entities.

Prefer a small enabled set. Sungrow WiNet is sensitive to Modbus load; polling the full catalog is harder on the session than ModQTT’s thinner reading list was.

After changing register toggles, restart the add-on so discovery and polling pick up the new selection.

## Topics

- `{prefix}/availability` — `online` / `offline` (LWT)
- `{prefix}/state` — JSON snapshot of currently enabled register values
- `{prefix}/{slug}/state` — per-register state
- `{prefix}/{slug}/set` — write requests (when writes enabled)

Discovery uses a single retained device topic: `homeassistant/device/<device_id>/config`.

## Writes

Keep `allow_writes` **false** unless you intentionally control the inverter. EMS-related registers (EMS mode, forced charge/discharge, export limits, SoC limits, off-grid, …) additionally require `enable_ems_writes: true`.

Via WiNet, holding registers must not be polled densely; this add-on refreshes them at startup, after each write, and every 5 minutes.

## Migrating from ModQTT

1. Install this add-on (`legacy_modqtt_compat` defaults to `false` for a clean entity set).
2. **Stop ModQTT** so two Modbus clients do not compete.
3. Start **Sungrow Modbus to MQTT**.
4. Repoint Energy Dashboard / automations to the new entities (names derive from `device_name`, e.g. `sensor.sungrow_inverter_*`).
5. Clear any leftover retained ModQTT discovery topics if old `sensor.sungrow_inverter_*` entities linger.

Set `legacy_modqtt_compat: true` only if you need to keep the old `modqtt-addon_*` unique IDs and Energy Dashboard history without repointing.

### Discovery migration (0.2.0+)

On first start after upgrading to device-based discovery, the add-on migrates existing per-entity discovery topics once (marker under `/data`). Entity `unique_id`s are unchanged, so history is kept. Toggle `allow_writes` / `enable_ems_writes` / per-register options to add or remove entities; hidden components are cleaned up automatically.

## Local development

```bash
cd sungrowmod2mqtt
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # edit MODBUS_HOST / MQTT_*
python -m src.app --local-check
pytest
```

When you add or rename registers in `src/catalog.json`, regenerate add-on options:

```bash
python3 scripts/gen_options.py
```
