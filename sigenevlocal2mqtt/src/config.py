from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

UNOFFICIAL_NOTICE = (
    "Unofficial and not affiliated with Sigenergy. "
    "Use may affect hardware warranty or mySigen Terms of Service."
)
ADDON_VERSION = "0.2.0"
ADDON_URL = "https://github.com/YpsilonTM/homeassistant-addons"
ADDON_NAME = "Sigen EV Local to MQTT"
BUNDLED_CERT_DIR = Path(__file__).resolve().parent / "certs"
DATA_DIR = Path("/data") if Path("/data").is_dir() else Path(__file__).resolve().parents[1] / ".data"
DISCOVERY_MIGRATION_MARKER = DATA_DIR / "device_discovery_migrated"


@dataclass(frozen=True)
class Settings:
    charger_host: str
    charger_port: int
    poll_interval_seconds: float
    slow_poll_multiplier: int
    min_current: int
    max_current: int
    enable_dangerous_commands: bool
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_client_id: str
    topic_prefix: str
    device_name: str
    log_level: str
    cert_path: Path
    key_path: Path


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


def _optional_path(name: str) -> Path | None:
    raw = _env(name).strip()
    if not raw:
        return None
    return Path(raw)


def load_settings() -> Settings:
    host = _env("CHARGER_HOST")
    if not host:
        raise ValueError("CHARGER_HOST is required")

    cert = _optional_path("CERT_PATH") or (BUNDLED_CERT_DIR / "client.crt")
    key = _optional_path("KEY_PATH") or (BUNDLED_CERT_DIR / "private_key.pem")
    if not cert.is_file() or not key.is_file():
        raise ValueError(f"mTLS cert/key missing: {cert} {key}")

    min_current = _env_int("MIN_CURRENT", 6)
    max_current = _env_int("MAX_CURRENT", 16)
    if min_current < 6 or max_current > 32 or min_current > max_current:
        raise ValueError(f"Invalid current range {min_current}-{max_current}")

    log_level = _env("LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError(f"Unsupported LOG_LEVEL={log_level!r}")

    slow = _env_int("SLOW_POLL_MULTIPLIER", 4)
    if slow < 1 or slow > 60:
        raise ValueError(f"Invalid SLOW_POLL_MULTIPLIER={slow}")

    return Settings(
        charger_host=host,
        charger_port=_env_int("CHARGER_PORT", 21319),
        poll_interval_seconds=_env_float("POLL_INTERVAL_SECONDS", 5.0),
        slow_poll_multiplier=slow,
        min_current=min_current,
        max_current=max_current,
        enable_dangerous_commands=_env_bool("ENABLE_DANGEROUS_COMMANDS", False),
        mqtt_host=_env("MQTT_HOST") or "core-mosquitto",
        mqtt_port=_env_int("MQTT_PORT", 1883),
        mqtt_username=_env("MQTT_USER", ""),
        mqtt_password=_env("MQTT_PASS", ""),
        mqtt_client_id=_env("MQTT_CLIENT_ID") or "sigenevlocal2mqtt-addon",
        topic_prefix=_env("MQTT_TOPIC_PREFIX") or "sigen_evac_local",
        device_name=_env("DEVICE_NAME") or "Sigen EV AC Local",
        log_level=log_level,
        cert_path=cert,
        key_path=key,
    )
