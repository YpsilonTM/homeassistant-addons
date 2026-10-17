from __future__ import annotations

from typing import Any

# Appendix 2 — exact register values (some states have two encodings).
RUNNING_STATE: dict[int, str] = {
    0x0000: "running",
    0x0040: "running",
    0x0014: "microgrid",
    0x0400: "maintain_mode",
    0x0800: "compulsory_mode",
    0x1000: "running_off_grid",
    0x2000: "open_loop",
    0x4000: "external_ems",
    0x4001: "emergency_charging",
    0x1111: "uninitialized",
    0x8000: "stop",
    0x0001: "stop",
    0x1300: "key_stop",
    0x0002: "key_stop",
    0x1500: "emergency_stop",
    0x0004: "emergency_stop",
    0x1400: "standby",
    0x0008: "standby",
    0x1200: "initial_standby",
    0x0010: "initial_standby",
    0x1600: "starting",
    0x0020: "starting",
    0x1800: "intelligent_station_building",
    0x9100: "warn_running",
    0x8100: "derating",
    0x0080: "derating",
    0x8200: "dispatch_running",
    0x5500: "fault",
    0x0100: "fault",
    0x1700: "afci_self_test_shutdown",
    0x1900: "safe_mode",
    0x0041: "off_grid_charge",
    0x0200: "update_failed",
    0x2500: "communicate_fault",
    0x2501: "restarting",
}

OUTPUT_TYPE: dict[int, str] = {
    0: "single_phase",
    1: "three_phase_4l",
    2: "three_phase_3l",
}

EMS_MODE: dict[int, str] = {
    0: "self_consumption",
    2: "compulsory",
    3: "external_ems",
    4: "vpp",
}

CHARGE_DISCHARGE: dict[int, str] = {
    0xAA: "charge",
    0xBB: "discharge",
    0xCC: "stop",
}

ENABLE_DISABLE: dict[int, str] = {
    0xAA: "enable",
    0x55: "disable",
}

ON_OFF: dict[int, str] = {
    0xAA: "on",
    0x55: "off",
}

START_STOP: dict[int, str] = {
    0xCF: "boot",
    0xCE: "shutdown",
}

DO_CONFIG: dict[int, str] = {
    0: "off",
    1: "load_control",
    2: "grounding_fault",
    3: "micro_grid",
}

LOAD_CONTROL_MODE: dict[int, str] = {
    0: "timing",
    1: "on_off",
    2: "power_optimized",
    3: "disable",
}

FORCED_STARTUP: dict[int, str] = {
    0xAA: "forced_startup",
}

PV_LIMIT: dict[int, str] = {
    0xAA: "limit_pv",
    0x55: "allow_pv",
}

FORCED_CHARGING_TIME: dict[int, str] = {
    0: "weekday",
    1: "every_day",
}

POWER_FLOW_BITS: dict[int, str] = {
    0: "pv",
    1: "battery_charging",
    2: "battery_discharging",
    3: "load",
    4: "feed_in",
    5: "import",
    7: "negative_load",
}

ENUMS: dict[str, dict[int, str]] = {
    "running_state": RUNNING_STATE,
    "output_type": OUTPUT_TYPE,
    "ems_mode": EMS_MODE,
    "charge_discharge": CHARGE_DISCHARGE,
    "enable_disable": ENABLE_DISABLE,
    "on_off": ON_OFF,
    "start_stop": START_STOP,
    "do_config": DO_CONFIG,
    "load_control_mode": LOAD_CONTROL_MODE,
    "forced_startup": FORCED_STARTUP,
    "pv_limit": PV_LIMIT,
    "forced_charging_time": FORCED_CHARGING_TIME,
}


def enum_options(enum_key: str | None) -> list[str] | None:
    if not enum_key:
        return None
    if enum_key == "power_flow":
        return list(POWER_FLOW_BITS.values())
    mapping = ENUMS.get(enum_key)
    if not mapping:
        return None
    # Deduplicate labels that share multiple encodings
    seen: list[str] = []
    for name in mapping.values():
        if name not in seen:
            seen.append(name)
    return seen


def encode_enum(enum_key: str | None, value: Any) -> str | None:
    if enum_key is None or value is None:
        return None
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return None
    if enum_key == "power_flow":
        names = [name for bit, name in POWER_FLOW_BITS.items() if ivalue & (1 << bit)]
        return ",".join(names) if names else "none"
    mapping = ENUMS.get(enum_key)
    if not mapping:
        return None
    return mapping.get(ivalue) or f"0x{ivalue:04X}"


def decode_enum(enum_key: str | None, payload: str) -> int | None:
    if not enum_key:
        return None
    mapping = ENUMS.get(enum_key)
    if not mapping:
        return None
    normalized = payload.strip().lower()
    for raw, name in mapping.items():
        if name.lower() == normalized:
            return raw
    try:
        if normalized.startswith("0x"):
            return int(normalized, 16)
        return int(normalized)
    except ValueError:
        return None
