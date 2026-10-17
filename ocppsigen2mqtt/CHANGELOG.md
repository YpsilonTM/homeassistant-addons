# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- Added `state_class: measurement` to power and current sensors for improved Home Assistant Energy Dashboard compatibility.

## [1.0.3] - 2026-10-17

### Fixed
- Point GHCR image at `homeassistant-addons` after the repo rename (`hassaddons` → `homeassistant-addons`)

## [1.0.2] - 2026-10-17

### Deprecated
- Add-on marked deprecated (`stage: deprecated`). Prefer [Sigen EV Local to MQTT](../sigenevlocal2mqtt/DOCS.md) for charger control.

## [1.0.1] - 2026-06-11

## [1.0.0] - 2026-06-10
### Added
- Complete rewrite to Node.js.

## [0.5.0] - 2026-05-28

### Added
- Added a Home Assistant-discovered `charger_status` sensor published from
  OCPP `StatusNotification`, so charger state is visible as a regular sensor.

### Fixed
- Prevented stale current display after charging finishes by clamping
  published `current_a` to `0.0` when charger status is non-charging and
  imported power is effectively zero.
- Tuned charging-state detection for SIGEN behavior: only `Charging` is
  treated as active for current display, so `SuspendedEV` + zero power now
  publishes `0.0 A`.
- Added voltage plausibility filtering (120-300V) for phase-based derivation,
  so invalid low-voltage samples do not create misleading derived power and
  stale current readings.

### Added
- Added a retained Home Assistant switch for app charging access so the
  authorize gate is visible and controllable in the mobile app.

## [0.3.1] - 2026-05-27

### Fixed
- When the charger reports zero aggregate current, power, or energy, the
  bridge now derives usable values from phase current and phase voltage
  samples instead of publishing repeated zeros.

## [0.3.0] - 2026-05-27

### Fixed
- Added Home Assistant MQTT discovery for command buttons (`start`, `stop`,
  `reset`) so they appear directly on the charger device page.
- Replaced the voltage sensor icon with `mdi:sine-wave` for consistent icon
  rendering in Home Assistant.

### Added
- Added `lifetime_energy_kwh` sensor publication and Home Assistant discovery,
  sourced from `Energy.Active.Import.Register` (with `meterStop` fallback on
  `StopTransaction`).

## [0.2.2] - 2026-05-27

### Fixed
- Simplified MQTT discovery payload for numeric sensors to match ModQTT
  style: publish `availability_topic` only and omit optional availability
  fields. This avoids schema edge cases and allows
  power/current/voltage/energy sensors to be discovered reliably.

## [0.2.1] - 2026-05-27

### Fixed
- Declared add-on port mapping for OCPP listener in `config.yaml` with
  `ports`/`ports_description` (`9200/tcp`). This allows Home Assistant OS
  to expose the bridge listener to LAN clients and chargers.

## [0.2.0] - 2026-05-27

### Added
- **Home Assistant MQTT Discovery:** Bridge automatically publishes discovery payloads
  on startup, making sensors appear in Home Assistant without manual setup. Exposed
  entities: Power (W), Current (A), Voltage (V), Total Energy (Wh), and Availability
  (binary sensor). All sensors grouped under a device with proper device classes and
  units.
- Runtime authorization gate for `Authorize` handling with MQTT toggle command
  `command/toggle_authorize` and state publish on `authorize/state`.
- `usable_phases` configuration option (1|2|3, default 2) for future car upgrades.

### Changed
- `Authorize` response is now configurable at runtime (`Accepted`/`Blocked`) while
  keeping OCPP connection online.
- Documentation clarified that OCPP `SetChargingProfile` is not supported on SIGEN
  EVAC 11 firmware (advertised but not implemented). The `set_power_watts` command
  is provided for code compatibility and future hardware support but will timeout
  on current firmware versions.
- MQTT topic structure simplified in documentation (removed incorrect `{cp_id}`
  patterns).

### Tests
- Added coverage for authorization gate enabled/disabled behavior and runtime
  toggling.

## [0.1.0] - 2025-07-15

### Added
- OCPP 1.6 JSON/WebSocket central system server.
- Charge point connection tracking and per-CP state (active transaction ID).
- Inbound OCPP handlers: `BootNotification`, `Heartbeat`, `Authorize`,
  `StatusNotification`, `StartTransaction`, `StopTransaction`, `MeterValues`,
  `DataTransfer`.
- Meter value publishing for `Power.Active.Import` (→ W), `Current.Import`,
  `Energy.Active.Import.Register` (→ Wh), `Voltage` — unit-aware scaling.
- MQTT `command/start` and `command/stop` topics with CALL/CALL_RESULT
  correlation and result publish-back.
- MQTT `command/reset`, `command/get_config`, `command/set_config` topics.
- Auto-resolve stop `transactionId` from tracked active transaction when not
  provided in the command payload.
- Bridge and per-CP availability topics (`online`/`offline`).
- YAML config loaded via `--config` (HA add-on) or environment variables (dev).
- `run.sh` converts `/data/options.json` to runtime YAML for Home Assistant.
- Docker build using `ghcr.io/home-assistant/base:3.23` (Alpine-based).
