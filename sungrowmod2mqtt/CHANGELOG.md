# Changelog

## 0.2.0

- Recover MQTT availability after poll failures (publish `online` again once polling succeeds).
- Device-based Home Assistant MQTT discovery with one-time migration from per-entity topics (`unique_id` preserved).
- Hidden gated write entities are removed via stub components when writes/EMS options are off.
- Publish-on-change for retained MQTT payloads; skip redundant holding-register writes when the value is unchanged.
- Stricter unsupported-register handling (illegal address or 3 consecutive single-register failures).
- Retry identity read until a non-empty serial is available; `modbus_host` default is empty (required).
- Self-watchdog exits non-zero on a stuck poll loop or long MQTT outage (enable the Supervisor Watchdog toggle).
- `daily_*` energy sensors use `state_class: total_increasing`; number entity `step` uses register scale.
- Bounds on wide charge/discharge power and charge-cutoff voltage registers; reject negative `u16`/`u32` encodes.
- `legacy_modqtt_compat` defaults to `false`; bashio-based `run.sh`; English option translations; icon/logo.

## 0.1.1

- Default `device_name` is English (`Sungrow Inverter`) for public installs; override locally if desired.

## 0.1.0

- Initial release: Sungrow residential hybrid inverter Modbus TCP → MQTT bridge.
- Full V1.1.11 register catalog (scalar RO + RW; historical 6100–6826 blocks omitted).
- Home Assistant MQTT discovery with curated `enabled_by_default` core sensors.
- Gated writes via `allow_writes` / `enable_ems_writes`.
- Optional `legacy_modqtt_compat` preserves existing `sensor.sungrow_inverter_*` entity IDs.
