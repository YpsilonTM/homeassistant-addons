from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    username: str
    password: str
    region: str
    poll_interval_seconds: float
    min_current: int
    max_current: int
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_client_id: str
    topic_prefix: str
    device_name: str
    log_level: str
    slow_poll_multiplier: int
    state_dir: str


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


def load_settings() -> Settings:
    username = _env("SIGEN_USERNAME")
    password = _env("SIGEN_PASSWORD")
    if not username or not password:
        raise ValueError("SIGEN_USERNAME and SIGEN_PASSWORD are required")

    region = _env("SIGEN_REGION", "eu").lower()
    if region not in {"eu", "apac", "us", "cn"}:
        raise ValueError(f"Unsupported SIGEN_REGION={region!r}")

    min_current = _env_int("MIN_CURRENT", 6)
    max_current = _env_int("MAX_CURRENT", 16)
    if min_current < 6 or max_current > 32 or min_current > max_current:
        raise ValueError(f"Invalid current range {min_current}-{max_current}")

    log_level = _env("LOG_LEVEL", "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError(f"Unsupported LOG_LEVEL={log_level!r}")

    slow_poll_multiplier = _env_int("SLOW_POLL_MULTIPLIER", 4)
    if slow_poll_multiplier < 1 or slow_poll_multiplier > 60:
        raise ValueError(f"Invalid SLOW_POLL_MULTIPLIER={slow_poll_multiplier}")

    # Empty MQTT_* values (from optional add-on options) fall back to defaults.
    mqtt_host = _env("MQTT_HOST") or "core-mosquitto"
    mqtt_port = _env_int("MQTT_PORT", 1883)
    mqtt_username = _env("MQTT_USER", "")
    mqtt_password = _env("MQTT_PASS", "")

    return Settings(
        username=username,
        password=password,
        region=region,
        poll_interval_seconds=_env_float("POLL_INTERVAL_SECONDS", 15.0),
        min_current=min_current,
        max_current=max_current,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        mqtt_username=mqtt_username,
        mqtt_password=mqtt_password,
        mqtt_client_id=_env("MQTT_CLIENT_ID") or "sigenevcloud2mqtt-addon",
        topic_prefix=_env("MQTT_TOPIC_PREFIX") or "sigen_evac",
        device_name=_env("DEVICE_NAME") or "Sigen EV AC",
        log_level=log_level,
        slow_poll_multiplier=slow_poll_multiplier,
        state_dir=_env("STATE_DIR") or "/data",
    )
