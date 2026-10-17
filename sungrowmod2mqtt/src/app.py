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

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    reader = ModbusReader(
        settings.modbus_host,
        settings.modbus_port,
        settings.modbus_unit_id,
        settings.modbus_timeout_seconds,
    )

    delay = 1.0
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(reader.connect_with_backoff)
            break
        except Exception:
            LOGGER.exception("modbus_startup_failed; retrying in %.1fs", delay)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                reader.close()
                return 0
            except asyncio.TimeoutError:
                delay = min(delay * 2, STARTUP_BACKOFF_MAX_SECONDS)

    identity = await asyncio.to_thread(read_identity, reader, all_registers)
    device_code_raw = identity.get("device_type_code")
    device_code = int(device_code_raw) if isinstance(device_code_raw, (int, float)) else None
    serial = identity.get("serial_number") or "unknown"
    model = model_name(device_codes, device_code) or "Hybrid Inverter"
    firmware = identity.get("inverter_firmware")
    device_id = f"sungrow_{serial}" if serial != "unknown" else "sungrow"

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

    await asyncio.to_thread(mqtt.connect_with_backoff, thread_stop)
    if stop_event.is_set():
        mqtt.disconnect()
        reader.close()
        return 0

    mqtt.publish_discovery()

    last_values: dict[str, Any] = dict(identity)
    poll_cycle = 0
    last_holding_refresh = 0.0

    try:
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
                    await asyncio.to_thread(reader.write_register, register, value)
                    # Refresh written register
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
            # Always poll RO; holding only when allow_writes (to show current settings)
            # or on the holding refresh cadence even without writes for diagnostics? Plan says
            # holding sparsely. When writes off, skip holding entirely.
            if not settings.allow_writes:
                selected = [reg for reg in selected if reg.register_type == "input"]

            try:
                words = await asyncio.to_thread(reader.read_many, selected)
                decoded = decode_values(words, by_slug)
                last_values.update(decoded)
                mqtt.publish_values(last_values)
                if include_holding:
                    last_holding_refresh = time.monotonic()
            except Exception:
                LOGGER.exception("poll_failed")
                try:
                    mqtt.publish_availability(False)
                except Exception:
                    pass

            poll_cycle += 1
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
    return 0


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
