# ModQTT Development Notes

## Real Hardware Testing Results (2026-05-26)

### Successful Charge Cycle Captured
Successfully connected SIGEN EVAC 11 4G T2 WH (Serial: 120A64150210) and performed a full charge cycle with bridge commands.

**Timeline:**
- **09:54:10** — Charger connected via WebSocket `/120A64150210`
- **09:55:00** — Bridge sent `RemoteStartTransaction` → Accepted
- **09:55:09** — Charger status changed to "Charging"
- **09:55:10 - 09:56:10** — Multiple `MeterValues` messages during charge
- **09:56:19** — Bridge sent `RemoteStopTransaction` → Accepted
- **09:56:29** — Charger sent final StopTransaction with energy delivered: **60Wh**

### Charger Metadata
- **Vendor:** SIGEN
- **Model:** EVAC 11 4G T2 WH
- **Firmware:** V100R001C10SPC113B055C1
- **Serial Number:** 120A64150210

### MeterValues Data Structure (Key Observations)
```json
{
  "connectorId": 1,
  "transactionId": 1779782100,
  "meterValue": [{
    "timestamp": "2026-05-26T07:55:42Z",
    "sampledValue": [
      {"value": "6.18", "measurand": "Current.Import", "unit": "A", "phase": "L1"},
      {"value": "6.16", "measurand": "Current.Import", "unit": "A", "phase": "L2"},
      {"value": "0.00", "measurand": "Current.Import", "unit": "A", "phase": "L3"},
      {"value": "6.18", "measurand": "Current.Offered", "unit": "A"},
      {"value": "0", "measurand": "Energy.Active.Import.Register", "unit": "Wh"},
      {"value": "0", "measurand": "Power.Active.Import", "unit": "W"},
      {"value": "237.18", "measurand": "Voltage", "unit": "V", "phase": "L1"},
      {"value": "233.69", "measurand": "Voltage", "unit": "V", "phase": "L2"},
      {"value": "232.49", "measurand": "Voltage", "unit": "V", "phase": "L3"}
    ]
  }]
}
```

### Voltage Data
✅ **Available in every MeterValues message:**
- L1: ~237V (range 236.98-237.61V observed)
- L2: ~233V (range 233.43-234.50V observed)
- L3: ~232V (range 232.06-232.49V observed)

**Average:** ~234V

### Current Observations
✅ **Current.Import** (actual consumption per phase):
- Started at 0.65A on L1/L2
- Ramped to ~6.2A on L1, ~6.2A on L2 during charge
- L3 remained at 0.00A (car can only charge on 2 phases)
- **CRITICAL:** Current is **per-phase**, not total
  - 6.2A per phase × 2 active phases × 235V = **~2,900W total**

✅ **Current.Offered** (max available to charger):
- Consistently reported at current draw level (6.18A observed)
- This appears to be the *configured* charging current per phase

⚠️ **Power.Active.Import:** Shows as "0" in all messages
- Possible charger firmware limitation or only reported at different interval
- Can calculate from: `Current_per_phase × Active_phases × Average_Voltage`
  - Example: 6.18A × 2 phases × 235V ≈ 2,900W

### Energy Delivered
- **meterStart:** 56580 Wh (before charge)
- **meterStop:** 56640 Wh (after charge)
- **Delta:** 60 Wh (small test charge)

---

## Power-to-Current Feature (Implemented)

### User Requirement
> "I want to be able to give an input where you can fill out how much watt the charger may load with but it works based on current set. So we do that calculation ourselves. I = P/U and then floor the I, also the range of the I to set is 6A-16A"

### Implementation Plan

**MQTT Command Topic:** `dev/ocpp/command/set_power_watts`
**Payload Format:** `{"watts": <total_wattage>}`

**Processing Logic:**
1. Get last MeterValues from charger
2. **Detect active phases:** Count non-zero Current.Import values (L1, L2, L3)
3. Use fixed nominal voltage: **230V**
4. Calculate: `current_per_phase = floor(watts / (active_phases × 230))`
5. Clamp to [6, 16]A per phase
6. Send OCPP `ChangeConfiguration` with calculated current per phase

**Example Calculations (2 active phases, fixed 230V):**
- 1500W input → floor(1500 / (2 × 230)) = floor(3.26) = 3A per phase → clamp to 6A
- 2000W input → floor(2000 / (2 × 230)) = floor(4.34) = 4A per phase → clamp to 6A
- 3000W input → floor(3000 / (2 × 230)) = floor(6.52) = 6A per phase
- 5000W input → floor(5000 / (2 × 230)) = floor(10.86) = 10A per phase
- 10000W input → floor(10000 / (2 × 230)) = floor(21.73) = 21A per phase → clamp to 16A

**If all 3 phases active (different car):**
- 3000W input → floor(3000 / (3 × 230)) = floor(4.34) = 4A per phase → clamp to 6A

### Configuration Key for SIGEN Charger
Default key used by bridge: `MaxCurrentOnVehicleConnector`.

Use OCPP `GetConfiguration` to discover/confirm the correct key for your charger:
- MQTT command topic: `dev/ocpp/command/get_config`
- Payload examples:
  - `{}` (ask for all keys)
  - `{"keys": ["MaxCurrentOnVehicleConnector", "ChargingScheduleAllowedChargingRateUnit"]}`
- Result topic: `dev/ocpp/command_result/get_config`

If SIGEN requires a different key, override on set-power command:
- `dev/ocpp/command/set_power_watts` with `{"watts": 3000, "key": "<correct_key>"}`

### Test Strategy
1. Capture MeterValues and detect active phases (count non-zero currents)
2. Use nominal voltage 230V
3. Publish command: `{"watts": 2000}` → floor(2000 / (2 × 230)) = 4A → clamp to 6A
4. Verify charger current adjustment via next MeterValues
5. Test phase-awareness: Works correctly for both 1-phase, 2-phase, 3-phase cars
6. Test clamping at boundaries:
  - 600W (→1A per phase) → clamp to 6A
  - 10000W (→21A per phase on 2 phases) → clamp to 16A

---

## Bridge Status & Recent Work

### ✅ Completed
- Full OCPP 1.6 server with WebSocket support (root URL + path variants)
- All core OCPP handlers working (BootNotification, Heartbeat, Authorize, StatusNotification, StartTransaction, StopTransaction, MeterValues, DataTransfer)
- MQTT pub/sub with flat topic structure + charger_id in JSON payload
- Configuration system (ENV + YAML + HA options.json)
- paho-mqtt v2 API with v1 fallback compatibility
- 7/7 unit tests passing
- Docker image building cleanly
- Root WebSocket URL support (no path required)
- Backward compatibility for legacy path-based command topics

### ✅ Completed
- **Power-to-current feature** implemented with fixed 230V nominal calculation
- MQTT command `dev/ocpp/command/set_power_watts` implemented
- Unit tests for calculation + clamping implemented
- **New config:** `usable_phases` (1|2|3), default `2`
  - Used as default phase count for watts-to-current conversion
  - Can be overridden per command payload with `{"phases": 1|2|3}`

### ✅ Completed Features

**Authorize Gate (App/Bridge Toggle Mode)**
- OCPP connection stays live and listening (bridge doesn't disconnect)
- Charger keeps sending MeterValues and Heartbeat even when unauthorized
- Charging gated at **Authorize** step:
  - When `authorize_enabled=true` (default): Respond with `{"status": "Accepted"}` (normal)
  - When `authorize_enabled=false`: Respond with `{"status": "Blocked"}` (app can't charge)
- **Config Option:** `OCPP_AUTHORIZE_ENABLED` (env var, default `true`)
- **MQTT Command:** `dev/ocpp/command/toggle_authorize`
  - No payload: Toggles current state
  - Payload `{"enabled": true}`: Set enabled
  - Payload `{"enabled": false}`: Set disabled
- **Response Topic:** `dev/ocpp/authorize/state` with payload `{"charger_id": "...", "enabled": true/false}`
- **Behavior:**
  - Bridge ON, authorize OFF → App sees "Not Authorized", but bridge stays connected, can send RemoteStart
  - Bridge ON, authorize ON → App can charge and use app normally
  - Toggle at runtime via MQTT without restarting
- **Tests:** 4 new unit tests covering all scenarios (all passing)

### 📝 To-Do
- DOCS.md update (currently shows old nested topic structure)
- Determine correct OCPP config key for SIGEN charger current setting
- HA discovery payloads (future nice-to-have)
- Write safety guardrails for future write operations

---

## Terminal Session
**Active Bridge:** Terminal ID `6b4d0619-b4cc-49fe-820c-46f3561477a2`
- Running locally at 0.0.0.0:9200
- Connected to MQTT at 192.168.50.11:1883
- DEBUG logging enabled
- Charger can reconnect anytime
