from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from src.config import load_config
from src.decoder import decode_reading
from src.modbus_client import ModbusReader
from src.mqtt_client import MqttPublisher
from src.publisher import publish_reading


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ModQTT bridge")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config_local/modqtt.yaml"),
        help="Path to YAML config",
    )
    parser.add_argument("--once", action="store_true", help="Run one poll cycle and exit")
    parser.add_argument(
        "--write-name",
        type=str,
        help="Write mode: parameter name from config write_parameters",
    )
    parser.add_argument(
        "--write-value",
        type=float,
        help="Write mode: engineering value to write",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args()
    config = load_config(args.config)
    logger = logging.getLogger(__name__)

    if args.write_name is not None or args.write_value is not None:
        if args.write_name is None or args.write_value is None:
            raise ValueError("Both --write-name and --write-value are required in write mode")
        if not config.allow_writes:
            raise ValueError("Writes are disabled by config. Set allow_writes=true")

        definition = next(
            (item for item in config.write_parameters if item.name == args.write_name),
            None,
        )
        if definition is None:
            raise ValueError(f"Unknown write parameter: {args.write_name}")

        modbus = ModbusReader(
            host=config.modbus.host,
            port=config.modbus.port,
            unit_id=config.modbus.unit_id,
            timeout_seconds=config.modbus.timeout_seconds,
        )
        try:
            modbus.connect_with_backoff()
            modbus.write_parameter(definition, args.write_value)
            logger.info("write_completed name=%s value=%s", args.write_name, args.write_value)
        finally:
            modbus.close()
        return 0

    modbus = ModbusReader(
        host=config.modbus.host,
        port=config.modbus.port,
        unit_id=config.modbus.unit_id,
        timeout_seconds=config.modbus.timeout_seconds,
    )

    mqtt = MqttPublisher(
        host=config.mqtt.host,
        port=config.mqtt.port,
        client_id=config.mqtt.client_id,
        username=config.mqtt.username,
        password=config.mqtt.password,
        availability_topic=config.mqtt.availability_topic,
    )

    modbus.connect_with_backoff()
    mqtt.connect_with_backoff()
    # Ensure availability is online even if on_connect already published it.
    mqtt.publish_availability(config.mqtt.availability_topic, online=True)

    # Publish Home Assistant discovery if enabled
    if getattr(config.mqtt, "discovery_enabled", False):
        from src.publisher import publish_discovery

        publish_discovery(config.mqtt, mqtt, config.readings)

    logger.info("bridge_started readings=%s", len(config.readings))

    try:
        while True:
            cycle_start = time.monotonic()
            words_by_name = modbus.read_many(config.readings)

            for reading in config.readings:
                try:
                    words = words_by_name[reading.name]
                    value = decode_reading(words, reading)
                    publish_reading(config.mqtt, mqtt, reading, value)
                except Exception as exc:
                    logger.exception(
                        "reading_failed sensor=%s address=%s register_type=%s error=%s",
                        reading.name,
                        reading.address,
                        reading.register_type,
                        exc,
                    )

            if args.once:
                break

            elapsed = time.monotonic() - cycle_start
            sleep_for = max(config.modbus.poll_interval_seconds - elapsed, 0.0)
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        logger.info("bridge_stopping reason=keyboard_interrupt")
    finally:
        try:
            mqtt.publish_availability(config.mqtt.availability_topic, online=False)
        except Exception:
            logger.warning("availability_offline_publish_failed")
        mqtt.disconnect()
        modbus.close()

    logger.info("bridge_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
