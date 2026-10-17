# Changelog

All notable changes to this Home Assistant add-on will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.3] - 2026-10-17
### Fixed
- Point GHCR image at `homeassistant-addons` after the repo rename (`hassaddons` → `homeassistant-addons`)

## [0.2.2] - 2026-09-10
### Fixed
- ModQTT now survives a Mosquitto restart without a manual add-on restart. Previously a broker bounce left the client publishing `rc=4` (`MQTT_ERR_NO_CONN`) forever while Modbus polling kept running, so Home Assistant showed stale retained values indefinitely.
- Enabled paho reconnect backoff (`reconnect_delay_set(1, 30)`) and start the network loop exactly once, so paho's loop thread owns reconnection instead of `loop_start()` being called on every retry.
- A failed publish now clears the connected flag, waits a bounded time for the loop thread to reconnect, and retries once; it no longer blocks the poll cycle while the broker is down, and it recovers on a later cycle instead of spamming forever.
- Added a fallback manual `reconnect()` after 60 s without a connection, for the rare case where paho's loop thread is no longer retrying.
- Availability now uses an MQTT last-will (`offline`, retained) and republishes `online` on every (re)connect, so Home Assistant marks Sungrow entities unavailable instead of presenting stale retained values as current.

### Added
- Added root-level repository `README.md` to support multi-add-on presentation.
- Unit tests for the reconnect paths: disconnect then publish, `rc=4` clearing connected state, single `loop_start`, bounded publish while the broker stays down, and paho v1/v2 disconnect callback signatures.

### Changed
- Removed `modqtt/README.md` and consolidated add-on documentation reference to `modqtt/DOCS.md`.
- Updated repository URLs and Home Assistant repository badge links to `YpsilonTM/hassaddons` after repo rename.
- Updated add-on image path to `ghcr.io/ypsilontm/hassaddons/addon-modqtt` to match the renamed repository workflow output.
- Updated repository display name in `repository.yaml` to a generic multi-add-on title.

## [0.2.1] - 2026-05-20
### Fixed
- Removed reference to deleted `profile` field in app.py startup log (fixes startup crash after profile removal).

## [0.2.0] - 2026-05-20
### Added
- Initial CHANGELOG.md for the add-on.

### Changed
- Removed all profile (dev/prod) logic and configuration. Topic selection is now explicit only.
- Generalized all config, code, and docs for device-neutral use.
- Bumped add-on and Python package version to 0.2.0.

## [0.1.0] - 2026-05-19
### Added
- Initial public release: Modbus TCP to MQTT bridge for Home Assistant.
- Supports device-neutral configuration, Modbus polling, MQTT publishing, and Home Assistant discovery (optional).
