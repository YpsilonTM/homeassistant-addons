# Changelog

## 0.2.1

- Fix boot crash: load options from `/data/options.json` again. `bashio::config` was forbidden on Supervisor API and left `CHARGER_HOST` empty.

## 0.2.0

- Switch to MQTT **device discovery** (`homeassistant/device/<id>/config`) with a one-time migration that keeps existing entity IDs and history
- Use `default_entity_id` instead of deprecated `object_id`
- Stop polling/publishing Wi‑Fi password / APN secrets; clear any retained secret state topics; secrets become write-only
- Gate house/pole breaker current (12116 / 12118) behind `enable_dangerous_commands`
- Publish the real charge-current setpoint on `current/state` (clamp on write only)
- Mark `session_energy` as `state_class: total_increasing`
- NearTerminal: frame buffer + sequence matching, silent single retry on no-reply, quieter poll errors
- Per-tag write error handling, RW write dedup, publish-on-change retained MQTT cache
- Self-watchdog exits on stalled loop or long MQTT outage — enable Supervisor Watchdog
- Add `translations/en.yaml`, `icon.png`, `logo.png`; scrub example IPs/serials; drop `uv.lock`

## 0.1.4

- Hide raw `DLM mode` by default until value names are known
- Drop duplicate synthetic `Charging Power` (W); keep catalog `Active charge power` (kW)
- Clarify charge-power names (`Active charge power` vs `Charge power limit (%)` / fixed)
- Put charge-current slider under Controls; move power limits / breakers / DLM to Config
- Publish session/lifetime duration with `device_class: duration`
- Normalize temperature unit `℃` → `°C` so cable/internal temps discover in HA

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
