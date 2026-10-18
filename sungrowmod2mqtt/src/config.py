from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from src.catalog import Register

UNOFFICIAL_NOTICE = (
    "Unofficial and not affiliated with Sungrow. "
    "Use may affect hardware warranty or Sungrow Terms of Service."
)


@dataclass(frozen=True)
class Settings:
    modbus_host: str
    modbus_port: int
    modbus_unit_id: int
    modbus_timeout_seconds: float
    poll_interval_seconds: float
    slow_poll_multiplier: int
    allow_writes: bool
    enable_ems_writes: bool
    legacy_modqtt_compat: bool
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_client_id: str
    topic_prefix: str
    device_name: str
    log_level: str
    register_toggles: dict[str, bool] = field(default_factory=dict)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_register_toggles(raw: str) -> dict[str, bool]:
    if not raw or not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("REGISTER_TOGGLES must be a JSON object")
    toggles: dict[str, bool] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            raise ValueError(f"REGISTER_TOGGLES key must be string, got {key!r}")
        toggles[key] = bool(value)
    return toggles


def enabled_slugs(registers: Iterable[Register], toggles: dict[str, bool]) -> set[str]:
    """Return slugs that should be polled/published.

    Missing toggle keys fall back to each register's catalog ``enabled_by_default``.
    """
    enabled: set[str] = set()
    for register in registers:
        if register.slug in toggles:
            if toggles[register.slug]:
                enabled.add(register.slug)
        elif register.enabled_by_default:
            enabled.add(register.slug)
    return enabled


def load_settings() -> Settings:
    host = _env("MODBUS_HOST")
    if not host:
        raise ValueError("MODBUS_HOST is required")

    log_level = _env("LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError(f"Unsupported LOG_LEVEL={log_level!r}")

    slow = _env_int("SLOW_POLL_MULTIPLIER", 6)
    if slow < 1 or slow > 60:
        raise ValueError(f"Invalid SLOW_POLL_MULTIPLIER={slow}")

    allow_writes = _env_bool("ALLOW_WRITES", False)
    enable_ems = _env_bool("ENABLE_EMS_WRITES", False)
    if enable_ems and not allow_writes:
        raise ValueError("ENABLE_EMS_WRITES requires ALLOW_WRITES=true")

    return Settings(
        modbus_host=host,
        modbus_port=_env_int("MODBUS_PORT", 502),
        modbus_unit_id=_env_int("MODBUS_UNIT_ID", 1),
        modbus_timeout_seconds=_env_float("MODBUS_TIMEOUT_SECONDS", 10.0),
        poll_interval_seconds=_env_float("POLL_INTERVAL_SECONDS", 10.0),
        slow_poll_multiplier=slow,
        allow_writes=allow_writes,
        enable_ems_writes=enable_ems,
        legacy_modqtt_compat=_env_bool("LEGACY_MODQTT_COMPAT", False),
        mqtt_host=_env("MQTT_HOST") or "core-mosquitto",
        mqtt_port=_env_int("MQTT_PORT", 1883),
        mqtt_username=_env("MQTT_USER", ""),
        mqtt_password=_env("MQTT_PASS", ""),
        mqtt_client_id=_env("MQTT_CLIENT_ID") or "sungrowmod2mqtt-addon",
        topic_prefix=_env("MQTT_TOPIC_PREFIX") or "sungrowmod2mqtt",
        device_name=_env("DEVICE_NAME") or "Sungrow Inverter",
        log_level=log_level,
        register_toggles=_parse_register_toggles(_env("REGISTER_TOGGLES", "")),
    )


def load_dotenv_if_present() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path)
