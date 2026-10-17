from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"


@dataclass(frozen=True)
class Register:
    slug: str
    name: str
    address: int
    register_type: str
    access: str
    data_type: str
    length_words: int
    scale: float
    unit: str
    device_class: str
    state_class: str
    platform: str
    fast: bool
    enabled_by_default: bool
    category: str
    ems_gated: bool
    decimals: int
    supported_device_codes: tuple[int, ...] | None
    unsupported_device_codes: tuple[int, ...] | None
    legacy_modqtt_suffix: str | None
    min_value: float | None
    max_value: float | None
    enum_key: str | None

    @property
    def writable(self) -> bool:
        return self.access == "RW"

    @property
    def wire_address(self) -> int:
        """Documented address minus one (Sungrow protocol requirement)."""
        return self.address - 1

    def applies_to(self, device_code: int | None) -> bool:
        if device_code is None:
            return True
        if self.unsupported_device_codes and device_code in self.unsupported_device_codes:
            return False
        if self.supported_device_codes and device_code not in self.supported_device_codes:
            return False
        return True


def _parse_register(raw: dict[str, Any]) -> Register:
    supported = raw.get("supported_device_codes")
    unsupported = raw.get("unsupported_device_codes")
    return Register(
        slug=raw["slug"],
        name=raw["name"],
        address=int(raw["address"]),
        register_type=raw["register_type"],
        access=raw["access"],
        data_type=raw["data_type"],
        length_words=int(raw["length_words"]),
        scale=float(raw.get("scale", 1.0)),
        unit=str(raw.get("unit") or ""),
        device_class=str(raw.get("device_class") or ""),
        state_class=str(raw.get("state_class") or ""),
        platform=str(raw.get("platform") or "sensor"),
        fast=bool(raw.get("fast", False)),
        enabled_by_default=bool(raw.get("enabled_by_default", False)),
        category=str(raw.get("category") or ""),
        ems_gated=bool(raw.get("ems_gated", False)),
        decimals=int(raw.get("decimals", 0)),
        supported_device_codes=tuple(supported) if supported else None,
        unsupported_device_codes=tuple(unsupported) if unsupported else None,
        legacy_modqtt_suffix=raw.get("legacy_modqtt_suffix"),
        min_value=float(raw["min_value"]) if raw.get("min_value") is not None else None,
        max_value=float(raw["max_value"]) if raw.get("max_value") is not None else None,
        enum_key=raw.get("enum_key"),
    )


def load_catalog(path: Path | None = None) -> tuple[list[Register], dict[str, str]]:
    catalog_path = path or CATALOG_PATH
    with catalog_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    registers = [_parse_register(item) for item in data["registers"]]
    device_codes = {str(k): str(v) for k, v in data.get("device_codes", {}).items()}
    return registers, device_codes


def filter_registers(
    registers: list[Register],
    *,
    device_code: int | None = None,
    include_holding: bool = True,
) -> list[Register]:
    selected: list[Register] = []
    for reg in registers:
        if not reg.applies_to(device_code):
            continue
        if reg.register_type == "holding" and not include_holding:
            continue
        selected.append(reg)
    return selected


def model_name(device_codes: dict[str, str], device_code: int | None) -> str | None:
    if device_code is None:
        return None
    key = f"0x{device_code:X}"
    alt = f"0x{device_code:04X}"
    return device_codes.get(key) or device_codes.get(key.lower()) or device_codes.get(alt)


def registers_by_slug(registers: list[Register]) -> dict[str, Register]:
    return {reg.slug: reg for reg in registers}
