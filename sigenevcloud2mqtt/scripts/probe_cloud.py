#!/usr/bin/env python3
"""Probe Sigenergy cloud EVAC endpoints (local dev)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.cloud import SigenCloudClient  # noqa: E402


def pp(label: str, payload) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2, default=str)[:8000])


async def request(client, method: str, path: str, *, params=None, json_body=None):
    url = f"{client.base_url}{path}"
    session = client._session_or_raise()
    kwargs = {"headers": client._auth_headers()}
    if params:
        kwargs["params"] = params
    if json_body is not None:
        kwargs["json"] = json_body
    async with session.request(method, url, **kwargs) as resp:
        text = await resp.text()
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = {"text": text[:500]}
        return resp.status, body


async def main() -> None:
    import os

    user = os.environ["SIGEN_USERNAME"]
    pw = os.environ["SIGEN_PASSWORD"]
    region = os.environ.get("SIGEN_REGION", "eu")

    async with SigenCloudClient(user, pw, region) as client:
        await client.login()
        await client.fetch_station_info()
        sid = client.station_id
        sn = client.ac_sn

        probes = [
            ("GET charge/status", "GET", "device/acevse/charge/status", {"stationId": sid}),
            ("GET charge/status+sn", "GET", "device/acevse/charge/status", {"stationId": sid, "snCode": sn}),
            ("GET charge/read/info", "GET", "device/acevse/charge/read/info", {"stationId": sid}),
            ("GET charge/read/data", "GET", "device/acevse/charge/read/data", {"stationId": sid}),
            ("GET charge/read/detail", "GET", "device/acevse/charge/read/detail", {"stationId": sid}),
            ("GET charge/read/more-setting", "GET", "device/acevse/charge/read/more-setting", {"stationId": sid}),
            ("GET read/more-setting", "GET", "device/acevse/read/more-setting", {"stationId": sid, "snCode": sn}),
            ("GET more-setting/read", "GET", "device/acevse/more-setting/read", {"stationId": sid}),
            ("GET device/card", "GET", "device/acevse/device/card", {"stationId": sid, "snCode": sn}),
            ("GET tp-device card", "GET", "device/tp-device/acevse/card", {"stationId": sid, "snCode": sn}),
            ("GET sigen device card", "GET", "device/sigen/device/card", {"stationId": sid}),
            ("GET station device list", "GET", "device/sigen/station/device/list", {"stationId": sid}),
            ("GET acevse list", "GET", "device/acevse/list", {"stationId": sid}),
            ("GET energyflow id", "GET", "device/sigen/station/energyflow", {"id": sid}),
            ("GET energyflow stationId", "GET", "device/sigen/station/energyflow", {"stationId": sid}),
            ("GET realtime", "GET", "data-process/sigen/station/statistics/real-time", {"stationId": sid}),
            ("GET acevse realtime", "GET", "data-process/sigen/acevse/statistics/real-time", {"stationId": sid, "snCode": sn}),
            ("GET acevse charge realtime", "GET", "device/acevse/charge/realtime", {"stationId": sid, "snCode": sn}),
            ("GET acevse charge read/realtime", "GET", "device/acevse/charge/read/realtime", {"stationId": sid}),
            ("GET acevse charge read/running", "GET", "device/acevse/charge/read/running", {"stationId": sid}),
            ("GET acevse charge read/power", "GET", "device/acevse/charge/read/power", {"stationId": sid, "snCode": sn}),
        ]
        for label, method, path, params in probes:
            status, body = await request(client, method, path, params=params)
            pp(label, {"http": status, "body": body})

        # POST more-setting read attempts (null fields like sigen lib)
        post_bodies = [
            ("POST more-setting nulls", {"stationId": sid, "dlmStatus": None, "outputMode": None, "meterPhase": None, "phaseAutoSwitch": None, "offGridCharge": None, "chargingOutputCurrent": None, "homeCircuitBreaker": None}),
            ("POST more-setting GET-like", {"stationId": sid}),
        ]
        for label, body in post_bodies:
            status, resp = await request(client, "POST", "device/acevse/more-setting", json_body=body)
            pp(label, {"http": status, "body": resp})

        # Start/stop probe (dry - only OPTIONS-like safe GET first)
        command_paths = [
            ("GET charge/start", "device/acevse/charge/start"),
            ("GET charge/stop", "device/acevse/charge/stop"),
            ("GET charge/control", "device/acevse/charge/control"),
            ("GET charge/switch", "device/acevse/charge/switch"),
            ("GET charge/operate", "device/acevse/charge/operate"),
            ("GET charge/manual", "device/acevse/charge/manual"),
        ]
        for label, path in command_paths:
            status, body = await request(client, "GET", path, params={"stationId": sid, "snCode": sn})
            pp(label, {"http": status, "body": body})


if __name__ == "__main__":
    asyncio.run(main())
