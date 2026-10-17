# OCPP SIGEN to MQTT – Add-on Documentation

> **Deprecated.** This add-on is no longer maintained. Use
> [Sigen EV Local to MQTT](../sigenevlocal2mqtt/DOCS.md) instead — local
> NearTerminal control is the recommended way to steer the charger.

## What it does

This add-on starts an OCPP 1.6 central system (WebSocket server).  Your EV
charger connects to it, and the add-on bridges charger telemetry to MQTT topics
readable by Home Assistant.  Start/stop charging and other commands are sent
via MQTT.

**Home Assistant Integration:** The bridge automatically publishes Home Assistant
MQTT discovery payloads, so sensors appear in Home Assistant without manual setup.

## Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `ocpp_host` | `0.0.0.0` | Interface to bind the OCPP WebSocket server on. |
| `ocpp_port` | `9200` | TCP port the charger connects to. |
| `mqtt_host` | `core-mosquitto` | MQTT broker hostname. |
| `mqtt_port` | `1883` | MQTT broker port. |
| `mqtt_username` | _(empty)_ | MQTT username. |
| `mqtt_password` | _(empty)_ | MQTT password. |
| `mqtt_client_id` | `ocppsigen2mqtt-addon` | MQTT client ID. |
| `topic_prefix` | `ocpp` | Root topic prefix for all published topics. |
| `log_level` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `usable_phases` | `2` | Number of usable phases for watts-to-current conversion. |

### Add-on / local runtime inputs

Home Assistant add-on options are read from `/data/options.json` and exported to
environment variables by `run.sh`. For local development, use a `.env` file in
the project root with the same variable names.

## Home Assistant Integration

The bridge automatically publishes MQTT discovery payloads when it connects to the broker.
Home Assistant will auto-discover these entities:

- **Sensors:**
  - Power (W)
  - Current (A)
  - Voltage (V)
  - Total Energy (Wh)
  - Lifetime Energy (kWh)

- **Binary Sensors:**
  - Charger Availability (online/offline)
  - Bridge Availability (online/offline)

- **Buttons:**
  - Start Charging
  - Stop Charging
  - Reset Charger

- **Switches:**
  - App Access (enable/disable charger app charging access)

All sensors are grouped under a device named `EV Charger {charger_id}` and appear in
the **Integrations > MQTT > Discovered** list. They'll be created automatically without
additional configuration.

If you don't see them in Home Assistant, ensure:
1. MQTT integration is configured in Home Assistant
2. Discovery is enabled (Settings > Devices & Services > MQTT > Configure > Enable discovery)
3. The charger is connected and `availability` topic shows `online`

When the charger firmware reports `0` for aggregate current, power, or energy,
the bridge derives usable values from the phase current and phase voltage
samples so the sensors stay meaningful in Home Assistant.

## MQTT Topics

All topics are prefixed with the configured `topic_prefix` (default `ocpp`).

### Published by bridge

| Topic | Payload | Description |
|-------|---------|-------------|
| `{prefix}/bridge/availability` | `online`/`offline` | Bridge status |
| `{prefix}/power_w` | float | Active power in Watts |
| `{prefix}/current_a` | float | Import current in Amps |
| `{prefix}/total_energy_wh` | float | Total energy in Wh |
| `{prefix}/lifetime_energy_kwh` | float | Lifetime/cumulative charged energy in kWh |
| `{prefix}/voltage_v` | float | Voltage in Volts |
| `{prefix}/metrics` | JSON | All metrics combined |
| `{prefix}/status` | JSON | Latest `StatusNotification` payload |
| `{prefix}/connector/{n}/status` | string | Connector status string |
| `{prefix}/boot` | JSON | `BootNotification` payload |
| `{prefix}/transaction/active` | JSON | Active transaction info |
| `{prefix}/transaction/last` | JSON | Last completed transaction |
| `{prefix}/command_result/start` | JSON | Result of start command |
| `{prefix}/command_result/stop` | JSON | Result of stop command |
| `{prefix}/command_result/reset` | JSON | Result of reset command |
| `{prefix}/command_result/get_config` | JSON | Result of GetConfiguration |
| `{prefix}/command_result/set_config` | JSON | Result of ChangeConfiguration |
| `{prefix}/command_result/set_power_watts` | JSON | Result of set_power_watts command (⚠ see note below) |
| `{prefix}/authorize/state` | JSON | Runtime authorize-gate state |

### Subscribed (commands)

| Topic | Payload (JSON) | Description |
|-------|----------------|-------------|
| `{prefix}/command/start` | `{"connector_id": 1, "id_tag": "REMOTE"}` | Start charging |
| `{prefix}/command/stop` | `{"transaction_id": 123}` _(optional if active)_ | Stop charging |
| `{prefix}/command/reset` | `{"type": "Soft"}` | Reset charger |
| `{prefix}/command/get_config` | `{"keys": ["HeartbeatInterval"]}` | Read config keys |
| `{prefix}/command/set_config` | `{"key": "HeartbeatInterval", "value": "60"}` | Write config key |
| `{prefix}/command/set_power_watts` | `{"watts": 3000}` | ⚠ **Not supported** – see smart charging note below |
| `{prefix}/command/toggle_authorize` | `{"enabled": true}` | Enable/disable app charging access |

The `authorize/state` topic is retained so the App Access switch is available
immediately after Home Assistant restarts.

## OCPP connection setup

The SIGEN note suggests the charger can connect over either WebSocket or secure
WebSocket, depending on firmware and deployment:

- `ws://<HA-host>:9200/<charge_point_id>`
- `wss://<HA-host>:9200/<charge_point_id>`

Use the charge point serial number as the CPID when possible, because the
bridge and SIGEN onboarding flow both expect that identifier to stay stable.
Set the subprotocol to `ocpp1.6`.

If the app or charger complains during save, try the exact root/CPID variants
above first, then confirm the charger is not trying to force a different cloud
mode or path.

## Charger setup

Point your charger's OCPP server URL to:
```
ws://<HA-host>:9200/<charge_point_id>
```
Set the subprotocol to `ocpp1.6`.

For chargers that prefer TLS, use `wss://` with a valid certificate setup.

Ensure port `9200` is exposed in the add-on Network settings.

## Working commands (examples)

With `topic_prefix: ocpp`, use these MQTT commands:

```bash
# Start charging on connector 1
mosquitto_pub -h localhost -t ocpp/command/start \
  -m '{"connector_id": 1, "id_tag": "REMOTE"}'

# Stop charging (transaction ID is optional if bridge knows the active transaction)
mosquitto_pub -h localhost -t ocpp/command/stop \
  -m '{"transaction_id": 123}'

# Toggle app access (enable or disable charging via the charger app)
mosquitto_pub -h localhost -t ocpp/command/toggle_authorize \
  -m '{"enabled": true}'

# Reset charger
mosquitto_pub -h localhost -t ocpp/command/reset \
  -m '{"type": "Soft"}'

# Read configuration
mosquitto_pub -h localhost -t ocpp/command/get_config \
  -m '{"keys": ["HeartbeatInterval"]}'

# Write configuration
mosquitto_pub -h localhost -t ocpp/command/set_config \
  -m '{"key": "HeartbeatInterval", "value": "60"}'
```

Results are published on the matching `command_result/{action}` topic.

## Smart charging and power control

⚠ **IMPORTANT: Dynamic power control via OCPP SetChargingProfile is not supported by the SIGEN charger firmware.**

The SIGEN EVAC 11 firmware advertises `Smart Charging` feature support but does not implement
the `SetChargingProfile` CALL message. The bridge can send the command, but the charger will
not respond, causing a timeout.

The `set_power_watts` command is provided for compatibility but will timeout when used.
**You can only control charging via `start`, `stop`, and `toggle_authorize` commands.**

If your charger is a different model or firmware version, please test and report whether
SetChargingProfile works for your device.

### Charging control that works

- `command/start` – Start charging  
- `command/stop` – Stop charging  
- `command/toggle_authorize` – Enable/disable app charging  

### Configuration reference (non-functional)

The bridge includes logic to convert watts to per-phase current using the formula below.
This is preserved for reference and future compatibility, but will not work with most
SIGEN chargers:

```
current_per_phase = floor(watts / (230 * phases))
```

Clamped to 6–16 A per OCPP specification.

Example (if it were supported):
```json
{"watts": 3000}
```

Optional overrides (non-functional):
```json
{"watts": 3000, "phases": 3, "purpose": "TxProfile"}
```

## Local development

```bash
MQTT_HOST=192.168.50.11 MQTT_USER=homeassistant MQTT_PASS=mqtt \
  OCPP_PORT=9200 MQTT_TOPIC_PREFIX=dev/ocpp \
  python3 src/server.py
```
