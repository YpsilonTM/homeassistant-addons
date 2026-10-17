from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import threading
import time
from dataclasses import dataclass
from typing import Any

from src.catalog import Register, filter_registers, load_catalog, model_name, registers_by_slug
from src.config import UNOFFICIAL_NOTICE, Settings, load_dotenv_if_present, load_settings
from src.decoder import decode_register
from src.enums import decode_enum
from src.modbus_client import ModbusReader
from src.mqtt_bridge import HOLDING_REFRESH_SECONDS, MqttBridge

LOGGER = logging.getLogger(__name__)

IDENTITY_SLUGS = ("device_type_code", "serial_number", "nominal_output_power", "inverter_firmware")
STARTUP_BACKOFF_MAX_SECONDS = 60.0
INVERTER_OFFLINE_AFTER_FAILURES = 3
WATCHDOG_MQTT_DISCONNECT_SECONDS = 300.0
WATCHDOG_POLL_SECONDS = 5.0


@dataclass(frozen=True)
class RegisterWrite:
    slug: str
    value: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sungrow hybrid Modbus TCP → MQTT bridge")
    parser.add_argument(
        "--local-check",
        action="store_true",
        help="Connect to the inverter, dump identity + core readings, exit (no MQTT)",
    )
    return parser.parse_args()


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def decode_values(
    words_by_slug: dict[str, list[int]],
    by_slug: dict[str, Register],
) -> dict[str, Any]:
    decoded: dict[str, Any] = {}
    for slug, words in words_by_slug.items():
        register = by_slug.get(slug)
        if register is None:
            continue
        try:
            value = decode_register(words, register)
        except Exception:
            LOGGER.exception("decode_failed slug=%s", slug)
            continue
        if value is not None:
            decoded[slug] = value
    return decoded


def selectable_for_poll(
    registers: list[Register],
    *,
    include_slow: bool,
    include_holding: bool,
) -> list[Register]:
    selected: list[Register] = []
    for register in registers:
        if register.register_type == "holding":
            if include_holding:
                selected.append(register)
            continue
        if include_slow or register.fast:
            selected.append(register)
    return selected


def parse_write_value(register: Register, payload: str) -> Any:
    named = decode_enum(register.enum_key, payload)
    if named is not None:
        return named
    if register.data_type == "utf8":
        return payload
    return float(payload)


def _values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def _drain_writes(queue: asyncio.Queue[RegisterWrite]) -> dict[str, RegisterWrite]:
    pending: dict[str, RegisterWrite] = {}
    while True:
        try:
            command = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        pending[command.slug] = command
    return pending


def read_identity(reader: ModbusReader, registers: list[Register]) -> dict[str, Any]:
    by_slug = registers_by_slug(registers)
    wanted = [by_slug[slug] for slug in IDENTITY_SLUGS if slug in by_slug]
    words = reader.read_many(wanted)
    return decode_values(words, by_slug)


async def read_identity_until_serial(
    reader: ModbusReader,
    registers: list[Register],
    stop_event: asyncio.Event,
) -> dict[str, Any] | None:
    delay = 1.0
    while not stop_event.is_set():
        identity = await asyncio.to_thread(read_identity, reader, registers)
        serial = identity.get("serial_number")
        if isinstance(serial, str) and serial.strip():
            return identity
        LOGGER.warning("identity_serial_empty; retrying in %.1fs", delay)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
            return None
        except asyncio.TimeoutError:
            delay = min(delay * 2, STARTUP_BACKOFF_MAX_SECONDS)
    return None


async def local_check(settings: Settings) -> int:
    registers, device_codes = load_catalog()
    reader = ModbusReader(
        settings.modbus_host,
        settings.modbus_port,
        settings.modbus_unit_id,
        settings.modbus_timeout_seconds,
    )
    try:
        await asyncio.to_thread(reader.connect_with_backoff)
        identity = await asyncio.to_thread(read_identity, reader, registers)
        device_code = identity.get("device_type_code")
        if isinstance(device_code, float):
            device_code = int(device_code)
        model = model_name(device_codes, int(device_code) if device_code is not None else None)
        LOGGER.info(
            "local_check host=%s serial=%s device_code=%s model=%s firmware=%s",
            settings.modbus_host,
            identity.get("serial_number"),
            device_code,
            model,
            identity.get("inverter_firmware"),
        )

        active = filter_registers(registers, device_code=int(device_code) if device_code else None)
        core = [reg for reg in active if reg.enabled_by_default and reg.register_type == "input"]
        words = await asyncio.to_thread(reader.read_many, core)
        values = decode_values(words, registers_by_slug(active))
        for slug in sorted(values):
            LOGGER.info("local_check %s=%r", slug, values[slug])
        LOGGER.info(
            "local_check done registers_active=%s unsupported=%s",
            len(active),
            sorted(reader.unsupported_slugs),
        )
    finally:
        reader.close()
    return 0


async def run_bridge(settings: Settings) -> int:
    all_registers, device_codes = load_catalog()
    stop_event = asyncio.Event()
    thread_stop = threading.Event()
    loop = asyncio.get_running_loop()
    write_queue: asyncio.Queue[RegisterWrite] = asyncio.Queue()
    exit_code = 0
    last_cycle_at = time.monotonic()
    mqtt: MqttBridge | None = None

    def _request_stop() -> None:
        stop_event.set()
        thread_stop.set()

    def _watchdog_loop() -> None:
        nonlocal exit_code
        while not thread_stop.wait(WATCHDOG_POLL_SECONDS):
            stuck_limit = max(10 * settings.poll_interval_seconds, 300.0)
            age = time.monotonic() - last_cycle_at
            if age >= stuck_limit:
                LOGGER.error("watchdog_exit reason=loop_stuck age=%.1fs limit=%.1fs", age, stuck_limit)
                exit_code = 1
                _request_stop()
                return
            bridge = mqtt
            if bridge is None:
                continue
            mqtt_gap = bridge.disconnected_for_seconds()
            if mqtt_gap >= WATCHDOG_MQTT_DISCONNECT_SECONDS:
                LOGGER.error(
                    "watchdog_exit reason=mqtt_disconnected age=%.1fs",
                    mqtt_gap,
                )
                exit_code = 1
                _request_stop()
                return

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    reader = ModbusReader(
        settings.modbus_host,
        settings.modbus_port,
        settings.modbus_unit_id,
        settings.modbus_timeout_seconds,
    )
    reader.set_stop_event(thread_stop)

    try:
        await asyncio.to_thread(reader.connect_with_backoff, thread_stop)
    except Exception as exc:
        if thread_stop.is_set() or stop_event.is_set():
            reader.close()
            return 0
        LOGGER.exception("modbus_startup_failed: %s", exc)
        reader.close()
        return 1

    if stop_event.is_set():
        reader.close()
        return 0

    identity = await read_identity_until_serial(reader, all_registers, stop_event)
    if identity is None or stop_event.is_set():
        reader.close()
        return 0

    device_code_raw = identity.get("device_type_code")
    device_code = int(device_code_raw) if isinstance(device_code_raw, (int, float)) else None
    serial = str(identity["serial_number"]).strip()
    model = model_name(device_codes, device_code) or "Hybrid Inverter"
    firmware = identity.get("inverter_firmware")
    device_id = f"sungrow_{serial}"

    active = filter_registers(all_registers, device_code=device_code)
    by_slug = registers_by_slug(active)
    LOGGER.info(
        "identity serial=%s device_code=0x%X model=%s active_registers=%s",
        serial,
        device_code or 0,
        model,
        len(active),
    )

    mqtt = MqttBridge(
        settings.mqtt_host,
        settings.mqtt_port,
        settings.mqtt_client_id,
        active,
        username=settings.mqtt_username,
        password=settings.mqtt_password,
        topic_prefix=settings.topic_prefix,
        device_name=settings.device_name,
        device_id=device_id,
        allow_writes=settings.allow_writes,
        enable_ems_writes=settings.enable_ems_writes,
        legacy_modqtt_compat=settings.legacy_modqtt_compat,
    )
    mqtt.update_device_info(model, str(firmware) if firmware else None)

    def on_set(slug: str, payload: str) -> None:
        loop.call_soon_threadsafe(write_queue.put_nowait, RegisterWrite(slug, payload))

    mqtt.set_write_handler(on_set)

    last_values: dict[str, Any] = dict(identity)

    def on_mqtt_reconnect() -> None:
        # Publish cache was cleared in MqttBridge; republish current values once.
        def _republish() -> None:
            if not last_values:
                return
            try:
                mqtt.publish_values(last_values)
            except Exception:
                LOGGER.exception("republish_after_reconnect_failed")

        loop.call_soon_threadsafe(_republish)

    mqtt.set_reconnect_handler(on_mqtt_reconnect)

    await asyncio.to_thread(mqtt.connect_with_backoff, thread_stop)
    if stop_event.is_set():
        mqtt.disconnect()
        reader.close()
        return 0

    try:
        mqtt.publish_discovery()

        poll_cycle = 0
        last_holding_refresh = 0.0
        consecutive_failures = 0
        marked_offline = False
        last_cycle_at = time.monotonic()
        watchdog = threading.Thread(target=_watchdog_loop, name="sungrow-watchdog", daemon=True)
        watchdog.start()

        while not stop_event.is_set():
            cycle_start = time.monotonic()
            pending = _drain_writes(write_queue)
            for slug, command in pending.items():
                register = by_slug.get(slug)
                if register is None:
                    LOGGER.warning("write_unknown_slug slug=%s", slug)
                    continue
                if not mqtt.write_allowed(register):
                    LOGGER.warning("write_rejected_gated slug=%s", slug)
                    continue
                try:
                    value = parse_write_value(register, command.value)
                    previous = last_values.get(slug)
                    if previous is not None and _values_equal(previous, value):
                        LOGGER.debug(
                            "write_dedup_skip slug=%s value=%r",
                            slug,
                            value,
                        )
                        continue
                    await asyncio.to_thread(reader.write_register, register, value)
                    words = await asyncio.to_thread(reader.read_many, [register])
                    decoded = decode_values(words, by_slug)
                    last_values.update(decoded)
                except Exception:
                    LOGGER.exception("write_failed slug=%s payload=%r", slug, command.value)

            include_holding = (time.monotonic() - last_holding_refresh) >= HOLDING_REFRESH_SECONDS
            include_slow = poll_cycle % settings.slow_poll_multiplier == 0
            if poll_cycle == 0:
                include_holding = True
                include_slow = True

            selected = selectable_for_poll(
                active,
                include_slow=include_slow,
                include_holding=include_holding and settings.allow_writes,
            )
            if not settings.allow_writes:
                selected = [reg for reg in selected if reg.register_type == "input"]

            try:
                words = await asyncio.to_thread(reader.read_many, selected)
                decoded = decode_values(words, by_slug)
                last_values.update(decoded)
                mqtt.publish_values(last_values)
                if include_holding:
                    last_holding_refresh = time.monotonic()
                consecutive_failures = 0
                if marked_offline:
                    mqtt.publish_availability(True)
                    marked_offline = False
            except Exception:
                LOGGER.exception("poll_failed")
                consecutive_failures += 1
                if consecutive_failures >= INVERTER_OFFLINE_AFTER_FAILURES and not marked_offline:
                    try:
                        mqtt.publish_availability(False)
                        marked_offline = True
                    except Exception:
                        LOGGER.exception("availability_offline_publish_failed")

            poll_cycle += 1
            last_cycle_at = time.monotonic()
            elapsed = time.monotonic() - cycle_start
            sleep_for = max(0.1, settings.poll_interval_seconds - elapsed)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass
    finally:
        thread_stop.set()
        mqtt.disconnect()
        reader.close()
    return exit_code


def main() -> int:
    load_dotenv_if_present()
    args = _parse_args()
    settings = load_settings()
    _configure_logging(settings.log_level)
    LOGGER.info("%s", UNOFFICIAL_NOTICE)

    if args.local_check:
        return asyncio.run(local_check(settings))
    return asyncio.run(run_bridge(settings))


if __name__ == "__main__":
    raise SystemExit(main())
