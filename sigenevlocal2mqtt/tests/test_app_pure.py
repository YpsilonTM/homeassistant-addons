from __future__ import annotations

from src.app import (
    clamp_amps,
    parse_command_payload,
    parse_write_value,
    readable_tags,
    values_equal,
    watchdog_timeout_seconds,
)
from src.catalog import load_catalog, tags_by_id
from src.config import Settings


def _settings(**overrides) -> Settings:
    from pathlib import Path

    base = dict(
        charger_host="192.0.2.10",
        charger_port=21319,
        poll_interval_seconds=5.0,
        slow_poll_multiplier=4,
        min_current=6,
        max_current=16,
        enable_dangerous_commands=False,
        mqtt_host="localhost",
        mqtt_port=1883,
        mqtt_username="",
        mqtt_password="",
        mqtt_client_id="test",
        topic_prefix="sigen_evac_local",
        device_name="Sigen EV AC Local",
        log_level="INFO",
        cert_path=Path("src/certs/client.crt"),
        key_path=Path("src/certs/private_key.pem"),
    )
    base.update(overrides)
    return Settings(**base)


def test_clamp_amps() -> None:
    settings = _settings()
    assert clamp_amps(8, settings) == 8
    assert clamp_amps(5, settings) == 6
    assert clamp_amps(32, settings) == 16


def test_parse_command_payload() -> None:
    assert parse_command_payload("START") == 0
    assert parse_command_payload("stop") == 1
    assert parse_command_payload("nope") is None


def test_parse_write_value() -> None:
    by_id = tags_by_id()
    assert parse_write_value(by_id[12194], "8") == 8.0
    assert parse_write_value(by_id[12001], "PRESS") == 1
    assert parse_write_value(by_id[12197], "ABC123") == "ABC123"
    assert parse_write_value(by_id[12106], "authentication") == 1
    assert parse_write_value(by_id[12106], "0") == 0
    assert parse_write_value(by_id[12520], "3_phase") == 1
    assert parse_write_value(by_id[12101], "eco_charging") == 2


def test_readable_tags_fast_vs_slow() -> None:
    tags = load_catalog()
    fast = readable_tags(tags, include_slow=False)
    slow = readable_tags(tags, include_slow=True)
    assert all(tag.fast for tag in fast)
    assert all(tag.access != "CMD" for tag in slow)
    assert all(not tag.secret for tag in slow)
    assert len(slow) > len(fast)


def test_values_equal_and_watchdog_timeout() -> None:
    assert values_equal(8.0, 8)
    assert not values_equal(8.0, 9)
    assert watchdog_timeout_seconds(5) == 300.0
    assert watchdog_timeout_seconds(40) == 400.0
