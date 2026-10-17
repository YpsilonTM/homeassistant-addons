from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.catalog import Tag, load_catalog
from src.config import UNOFFICIAL_NOTICE, Settings, load_settings
from src.enums import decode_enum
from src.mqtt_bridge import CURRENT_TAG, START_STOP_TAG, START_VALUE, STOP_VALUE, MqttBridge
from src.near import NearClient, NearError

LOGGER = logging.getLogger(__name__)

CHARGER_OFFLINE_AFTER_FAILURES = 3
STARTUP_BACKOFF_MAX_SECONDS = 60.0
MQTT_OUTAGE_EXIT_SECONDS = 300.0


@dataclass(frozen=True)
class TagWrite:
    tag_id: int
    value: str


def _load_dotenv_if_present() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        LOGGER.debug("python-dotenv not installed; skipping .env load")
        return
    load_dotenv(env_path)
    LOGGER.info("loaded_env path=%s", env_path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sigenergy EV local NearTerminal → MQTT bridge")
    parser.add_argument(
        "--local-check",
        action="store_true",
        help="Connect to the charger, dump identity + featured tags, exit (no MQTT)",
    )
    parser.add_argument(
        "--set-current",
        type=float,
        help="With --local-check: write charge current (tag 12194) then read back",
    )
    return parser.parse_args()


def clamp_amps(requested: float, settings: Settings) -> float:
    amps = float(requested)
    return max(float(settings.min_current), min(float(settings.max_current), amps))


def parse_command_payload(value: str) -> int | None:
    normalized = value.strip().upper()
    if normalized == "START":
        return START_VALUE
    if normalized == "STOP":
        return STOP_VALUE
    return None


def parse_write_value(tag: Tag, payload: str) -> Any:
    if tag.platform == "button":
        return 1 if payload in {"", "1", "PRESS", "press"} else int(float(payload))
    if tag.dtype == "string" or tag.platform == "text":
        return payload
    if tag.dtype == "bytes":
        return payload
    named = decode_enum(tag.id, payload)
    if named is not None:
        return named
    return float(payload)


def values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) < 1e-9
    return left == right


def readable_tags(tags: list[Tag], include_slow: bool) -> list[Tag]:
    selected = []
    for tag in tags:
        if tag.access == "CMD":
            continue
        if tag.secret:
            continue
        if include_slow or tag.fast:
            selected.append(tag)
    return selected


def watchdog_timeout_seconds(poll_interval: float) -> float:
    return max(10.0 * float(poll_interval), 300.0)


def _drain_writes(queue: asyncio.Queue[TagWrite]) -> dict[int, TagWrite]:
    pending: dict[int, TagWrite] = {}
    while True:
        try:
            command = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        pending[command.tag_id] = command
    return pending


async def _connect_near_with_backoff(
    client: NearClient,
    stop_event: asyncio.Event,
) -> bool:
    delay = 1.0
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(client.connect)
            return True
        except Exception:
            LOGGER.exception("near_startup_failed; retrying in %.1fs", delay)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                return False
            except asyncio.TimeoutError:
                delay = min(delay * 2, STARTUP_BACKOFF_MAX_SECONDS)
    return False


async def local_check(settings: Settings, set_current: float | None) -> int:
    tags = load_catalog()
    by_id = {tag.id: tag for tag in tags}
    client = NearClient(settings.charger_host, settings.charger_port, settings.cert_path, settings.key_path)
    try:
        await asyncio.to_thread(client.connect)
        featured = [tag for tag in tags if tag.enabled_by_default and tag.access != "CMD" and not tag.secret]
        values = await asyncio.to_thread(client.read_tags, featured)
        LOGGER.info(
            "local_check host=%s serial=%s model=%s fw=%s",
            settings.charger_host,
            client.serial,
            values.get(8) or client.model,
            values.get(55) or client.firmware,
        )
        for tag_id in sorted(values):
            tag = by_id.get(tag_id)
            name = tag.slug if tag else str(tag_id)
            LOGGER.info("local_check %s=%r", name, values[tag_id])
        if set_current is not None:
            current_tag = by_id[CURRENT_TAG]
            amps = clamp_amps(set_current, settings)
            await asyncio.to_thread(client.write_tag, current_tag, amps)
            values = await asyncio.to_thread(client.read_tags, [current_tag])
            LOGGER.info("local_check charge_current=%r", values.get(CURRENT_TAG))
    finally:
        client.close()
    return 0


async def run_bridge(settings: Settings) -> int:
    tags = load_catalog()
    by_id = {tag.id: tag for tag in tags}
    stop_event = asyncio.Event()
    thread_stop = threading.Event()
    loop = asyncio.get_running_loop()
    write_queue: asyncio.Queue[TagWrite] = asyncio.Queue()
    exit_code = 0
    last_cycle_at = time.monotonic()
    watchdog_limit = watchdog_timeout_seconds(settings.poll_interval_seconds)

    def _request_stop() -> None:
        stop_event.set()
        thread_stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    mqtt = MqttBridge(
        host=settings.mqtt_host,
        port=settings.mqtt_port,
        client_id=settings.mqtt_client_id,
        tags=tags,
        username=settings.mqtt_username,
        password=settings.mqtt_password,
        topic_prefix=settings.topic_prefix,
        device_name=settings.device_name,
        device_id="sigen_evac_local",
        min_current=settings.min_current,
        max_current=settings.max_current,
        enable_dangerous_commands=settings.enable_dangerous_commands,
    )
    near = NearClient(settings.charger_host, settings.charger_port, settings.cert_path, settings.key_path)

    def enqueue(tag_id: int, value: str) -> None:
        loop.call_soon_threadsafe(write_queue.put_nowait, TagWrite(tag_id, value))

    def on_tag_set(tag_id: int, value: str) -> None:
        tag = by_id.get(tag_id)
        if tag is None:
            LOGGER.warning("unknown_tag id=%s", tag_id)
            return
        if not tag.writable:
            LOGGER.warning("tag_not_writable id=%s slug=%s", tag.id, tag.slug)
            return
        if tag.dangerous and not settings.enable_dangerous_commands:
            LOGGER.warning("dangerous_command_blocked id=%s slug=%s", tag.id, tag.slug)
            return
        enqueue(tag_id, value)

    def on_command(value: str) -> None:
        mapped = parse_command_payload(value)
        if mapped is None:
            LOGGER.warning("invalid_command payload=%r", value)
            return
        enqueue(START_STOP_TAG, str(mapped))

    mqtt.set_tag_handler(on_tag_set)
    mqtt.set_command_handler(on_command)

    last_values: dict[int, Any] = {}
    consecutive_failures = 0
    marked_offline = False
    poll_cycle = 0

    def on_mqtt_reconnect() -> None:
        loop.call_soon_threadsafe(last_values.clear)

    mqtt.set_reconnect_handler(on_mqtt_reconnect)

    def _watchdog_tick() -> None:
        nonlocal exit_code, last_cycle_at
        while not thread_stop.is_set():
            stalled = time.monotonic() - last_cycle_at
            mqtt_outage = mqtt.mqtt_outage_seconds()
            if stalled > watchdog_limit:
                LOGGER.error(
                    "watchdog_exit reason=loop_stalled stalled=%.1fs limit=%.1fs",
                    stalled,
                    watchdog_limit,
                )
                exit_code = 1
                _request_stop()
                os._exit(1)
            if mqtt_outage > MQTT_OUTAGE_EXIT_SECONDS:
                LOGGER.error(
                    "watchdog_exit reason=mqtt_outage outage=%.1fs",
                    mqtt_outage,
                )
                exit_code = 1
                _request_stop()
                os._exit(1)
            thread_stop.wait(5.0)

    await asyncio.to_thread(mqtt.connect_with_backoff, thread_stop)
    if stop_event.is_set():
        mqtt.disconnect()
        LOGGER.info("bridge_stopped")
        return 0

    if not await _connect_near_with_backoff(near, stop_event):
        mqtt.disconnect()
        LOGGER.info("bridge_stopped")
        return 0

    if near.serial:
        mqtt.device_id = f"sigen_evac_local_{near.serial}"
    mqtt.update_device_info(near.model, near.firmware)
    mqtt.publish_discovery()

    watchdog = threading.Thread(target=_watchdog_tick, name="sigen-watchdog", daemon=True)
    watchdog.start()

    LOGGER.info(
        "bridge_started poll=%ss slow_poll_x=%s mqtt=%s:%s prefix=%s device_id=%s host=%s",
        settings.poll_interval_seconds,
        settings.slow_poll_multiplier,
        settings.mqtt_host,
        settings.mqtt_port,
        settings.topic_prefix,
        mqtt.device_id,
        settings.charger_host,
    )

    try:
        while not stop_event.is_set():
            pending = _drain_writes(write_queue)
            for tag_id, command in pending.items():
                tag = by_id[tag_id]
                try:
                    value = parse_write_value(tag, command.value)
                    if tag.id == CURRENT_TAG:
                        value = clamp_amps(float(value), settings)
                    if tag.access == "RW" and tag.id in last_values and values_equal(last_values[tag.id], value):
                        LOGGER.debug(
                            "write_dedup tag=%s slug=%s value=%r",
                            tag.id,
                            tag.slug,
                            value,
                        )
                        continue
                    await asyncio.to_thread(near.write_tag, tag, value)
                    consecutive_failures = 0
                except ValueError:
                    LOGGER.warning(
                        "invalid_payload tag=%s slug=%s payload=%r",
                        tag.id,
                        tag.slug,
                        command.value,
                    )
                except Exception:
                    LOGGER.exception("write_failed tag=%s slug=%s", tag.id, tag.slug)
                    consecutive_failures += 1

            try:
                include_slow = poll_cycle % settings.slow_poll_multiplier == 0
                selected = readable_tags(tags, include_slow)
                values = await asyncio.to_thread(near.read_tags, selected)
                last_values.update(values)
                mqtt.update_device_info(
                    str(last_values.get(8) or near.model or ""),
                    str(last_values.get(55) or near.firmware or ""),
                )
                mqtt.publish_values(last_values)
                consecutive_failures = 0
                if marked_offline:
                    mqtt.publish_availability(True)
                    marked_offline = False
            except (NearError, OSError) as exc:
                LOGGER.warning("poll_failed %s: %s", type(exc).__name__, exc)
                consecutive_failures += 1
                if consecutive_failures >= CHARGER_OFFLINE_AFTER_FAILURES and not marked_offline:
                    try:
                        mqtt.publish_availability(False)
                        marked_offline = True
                    except Exception:
                        LOGGER.exception("availability_offline_publish_failed")
            except Exception:
                LOGGER.exception("poll_failed")
                consecutive_failures += 1
                if consecutive_failures >= CHARGER_OFFLINE_AFTER_FAILURES and not marked_offline:
                    try:
                        mqtt.publish_availability(False)
                        marked_offline = True
                    except Exception:
                        LOGGER.exception("availability_offline_publish_failed")

            poll_cycle += 1
            last_cycle_at = time.monotonic()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=settings.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        thread_stop.set()
        near.close()
        mqtt.disconnect()
        LOGGER.info("bridge_stopped")
    return exit_code


async def async_main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _load_dotenv_if_present()
    settings = load_settings()
    logging.getLogger().setLevel(getattr(logging, settings.log_level, logging.INFO))
    LOGGER.warning("%s", UNOFFICIAL_NOTICE)
    if args.local_check:
        return await local_check(settings, args.set_current)
    return await run_bridge(settings)


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
