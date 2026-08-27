# Changelog

## 0.2.5

- Fix add-on crash: `run.sh` had Windows CRLF line endings (`bash\r` in container)

## 0.2.4

- Fix phase mode writes: cloud `outputMode` is 0=1-phase, 1=3-phase (not 1/2)
- Remove unused `usable_phases` add-on option (was never wired up)

## 0.2.3

- Read phase mode from cloud: `GET device/acevse/more-setting/{stationId}?snCode=`
- Poll and publish real `phase_mode/state` (syncs with mySigen Advanced Settings)
- Map read fields `phaseType` / `phaseAutoSwitch` to HA `1_phase` / `3_phase` / `auto`

## 0.2.2

- Phase mode select shows `unknown` on startup (clears stale MQTT retain)
- Add Set 1-Phase / 3-Phase / Auto buttons that always send the cloud command
- Rename select to "last HA set" — cannot sync from mySigen app (no cloud read API)

## 0.2.1

- Improve phase mode reliability: split into minimal cloud POSTs, check `data: true`, retry after stop when charging

## 0.2.0

- Add cloud start/stop (`enable=0/1` on `device/acevse/charge/start`)
- Add status, realtime power/current/voltage, lifetime energy sensors
- Add start/stop MQTT buttons and `{prefix}/command` topic
- Add active phases sensor (inferred from realtime while charging)
- Add phase mode select (`1_phase`, `3_phase`, `auto`) via `more-setting`
- Expand `--cloud-check` with snapshot logging and `--set-phase-mode`

## 0.1.0

- Initial MVP: Sigenergy cloud login, station resolve, EVAC current get/set
- MQTT discovery for charging current number + max current diagnostic sensor
- Local run via `.env` and `--cloud-check` smoke test
