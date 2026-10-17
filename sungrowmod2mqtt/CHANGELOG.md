# Changelog

## 0.1.1

- Default `device_name` is English (`Sungrow Inverter`) for public installs; override locally if desired.

## 0.1.0

- Initial release: Sungrow residential hybrid inverter Modbus TCP → MQTT bridge.
- Full V1.1.11 register catalog (scalar RO + RW; historical 6100–6826 blocks omitted).
- Home Assistant MQTT discovery with curated `enabled_by_default` core sensors.
- Gated writes via `allow_writes` / `enable_ems_writes`.
- Optional `legacy_modqtt_compat` preserves existing `sensor.sungrow_inverter_*` entity IDs.
