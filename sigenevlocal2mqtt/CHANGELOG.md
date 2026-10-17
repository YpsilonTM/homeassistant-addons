# Changelog

## 0.1.3

- Mode / status enums publish names instead of raw flags (`auth_mode`, `work_mode`, `control_mode`, `output_mode`, `dlm_enable`, `run_state`, `plug_status`, …)
- RW enums become MQTT `select` entities; RO enums use `device_class: enum`
- After updating, remove leftover `number.*` mode entities from the HA entity registry if they remain beside the new selects

## 0.1.2

- Translate remaining Chinese entity names to English (grid frequency, Wi‑Fi/cloud status, relays, schedules, …)

## 0.1.1

- Fix GHCR image path after the repo rename (`hassaddons` → `homeassistant-addons`) so Supervisor can pull the public package

## 0.1.0

- Initial local NearTerminal bridge for standalone Sigen EV AC
- MQTT discovery for the full EVAC catalog (RO/RW) plus meter energy/power tags
- Charge-current write (tag 12194) with min/max clamp
- Convenience topics mirroring the cloud add-on (`current/set`, `power_w`, lifetime)
- Factory reset / data wipe gated behind `enable_dangerous_commands`
- Local start/stop via firmware tag 12000 (`START`/`STOP` buttons and `{prefix}/command`)
- Unofficial / not-affiliated / warranty-ToS notice in the store description, docs, and startup log
