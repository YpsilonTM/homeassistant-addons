"""Named values for EVAC enum16 tags (from mySigen / firmware)."""

from __future__ import annotations

# value -> option name (snake_case; shown in HA selects/sensors)
TAG_ENUMS: dict[int, dict[int, str]] = {
    # ChargerDeviceStatus (app gK3 labels + unique occupied_* slots)
    11265: {
        0: "unplugged",
        1: "plugged",
        2: "preparing",
        3: "charging",
        4: "fault",
        5: "reserving",
        6: "occupied_a",
        7: "occupied_b",
        8: "occupied_c",
        9: "unavailable",
        44: "offline",
    },
    # Plug sense
    11510: {
        0: "unplugged",
        1: "plugged",
    },
    # Firmware: OCPP control mode vs local (app / RFID / modbus)
    11625: {
        0: "local",
        1: "ocpp",
    },
    # AC charge mode (mySigen lcz / chargeMode)
    12101: {
        0: "fast_charging",
        1: "green_charging",
        2: "eco_charging",
    },
    # Authentication toggle (cloud set/authentication/mode value 0|1)
    12106: {
        0: "auto_charge",
        1: "authentication",
    },
    # DLM enable (bpm on/off)
    12268: {
        0: "off",
        1: "on",
    },
    # Meter / DLM phase pick (advanced setting meterPhaseItemList)
    12269: {
        0: "L1",
        1: "L2",
        2: "L3",
        3: "system_automatic",
    },
    # Output / phaseType (OutputTypeEnum + cloud 1_phase/3_phase)
    12520: {
        0: "1_phase",
        1: "3_phase",
        2: "3_phase_4wire",
        3: "biphase",
    },
}


def enum_options(tag_id: int) -> list[str] | None:
    mapping = TAG_ENUMS.get(tag_id)
    if not mapping:
        return None
    return [mapping[key] for key in sorted(mapping)]


def encode_enum(tag_id: int, value: object) -> str | None:
    """Map a raw numeric tag value to an option name, if known."""
    mapping = TAG_ENUMS.get(tag_id)
    if mapping is None or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return mapping.get(number)


def decode_enum(tag_id: int, payload: str) -> int | None:
    """Map an option name (or digit string) to the raw enum value."""
    mapping = TAG_ENUMS.get(tag_id)
    if mapping is None:
        return None
    text = payload.strip()
    if not text:
        return None
    lowered = text.lower()
    for number, name in mapping.items():
        if name.lower() == lowered:
            return number
    try:
        number = int(float(text))
    except ValueError:
        return None
    if number in mapping:
        return number
    return None
