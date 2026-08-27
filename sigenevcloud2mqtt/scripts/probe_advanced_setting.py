#!/usr/bin/env python3
"""Probe read API for mySigen Advanced Settings (charger-advanced-setting page).

App route (EU):
  https://app-eu.sigencloud.com/#/charger/charger-setting/user-charger-setting/charger-advanced-setting

Read endpoint (confirmed):
  GET device/acevse/more-setting/{stationId}?snCode={snCode}

Writes still use:
  POST device/acevse/more-setting  (outputMode, phaseAutoSwitch, dlmStatus, ...)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.cloud import MoreSettingInfo, SigenCloudClient  # noqa: E402


def pp(label: str, payload: object) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2, default=str)[:6000])


async def main() -> None:
    import os

    user = os.environ["SIGEN_USERNAME"]
    pw = os.environ["SIGEN_PASSWORD"]
    region = os.environ.get("SIGEN_REGION", "eu")

    async with SigenCloudClient(user, pw, region) as client:
        await client.login()
        await client.fetch_station_info()
        more = await client.get_more_setting()
        pp("more-setting read", more)
        if more:
            pp(
                "decoded phase_mode",
                MoreSettingInfo.decode_phase_mode(more.phase_type, more.phase_auto_switch),
            )


if __name__ == "__main__":
    asyncio.run(main())
