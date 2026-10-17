from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from paho.mqtt import client as mqtt

from src.catalog import Register
from src.enums import encode_enum, enum_options

LOGGER = logging.getLogger(__name__)

LEGACY_MQTT_CLIENT_ID = "modqtt-addon"
HOLDING_REFRESH_SECONDS = 300.0


class MqttBridge:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        registers: list[Register],
        *,
        username: str = "",
        password: str = "",
        topic_prefix: str = "sungrowmod2mqtt",
        device_name: str = "Omvormer Zonnepanelen",
        device_id: str = "sungrow",
        allow_writes: bool = False,
        enable_ems_writes: bool = False,
        legacy_modqtt_compat: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.registers = registers
        self.by_slug = {reg.slug: reg for reg in registers}
        self.topic_prefix = topic_prefix.rstrip("/")
        self.device_name = device_name
        self.device_id = device_id
        self.allow_writes = allow_writes
        self.enable_ems_writes = enable_ems_writes
        self.legacy_modqtt_compat = legacy_modqtt_compat

        self.availability_topic = f"{self.topic_prefix}/availability"
        self.state_topic = f"{self.topic_prefix}/state"

        if hasattr(mqtt, "CallbackAPIVersion"):
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        else:
            self._client = mqtt.Client(client_id=self.client_id)

        if username:
            self._client.username_pw_set(username, password)

        self._connected = threading.Event()
        self._discovery_published = False
        self._device_model: str | None = None
        self._device_sw_version: str | None = None
        self._on_set: Callable[[str, str], None] | None = None
        self._on_reconnect: Callable[[], None] | None = None
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.will_set(self.availability_topic, "offline", qos=0, retain=True)

    def set_write_handler(self, handler: Callable[[str, str], None]) -> None:
        self._on_set = handler

    def set_reconnect_handler(self, handler: Callable[[], None]) -> None:
        self._on_reconnect = handler

    def update_device_info(self, model: str | None, sw_version: str | None) -> None:
        if model:
            self._device_model = model
        if sw_version:
            self._device_sw_version = sw_version

    def state_topic_for(self, register: Register) -> str:
        return f"{self.topic_prefix}/{register.slug}/state"

    def set_topic_for(self, register: Register) -> str:
        return f"{self.topic_prefix}/{register.slug}/set"

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        del userdata, flags, properties
        rc = reason_code.value if hasattr(reason_code, "value") else reason_code
        if rc != 0:
            LOGGER.error("mqtt_connect_failed reason=%s", reason_code)
            return
        self._connected.set()
        self.publish_availability(True)
        if self._discovery_published:
            self.publish_discovery()
        client.subscribe(f"{self.topic_prefix}/+/set")
        LOGGER.info("mqtt_connected host=%s port=%s", self.host, self.port)
        if self._on_reconnect:
            self._on_reconnect()

    def _on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None):
        del client, userdata, disconnect_flags, properties
        self._connected.clear()
        LOGGER.warning("mqtt_disconnected reason=%s", reason_code)

    def _on_message(self, client, userdata, message):
        del client, userdata
        if self._on_set is None:
            return
        topic = message.topic
        payload = message.payload.decode("utf-8", errors="replace").strip()
        prefix = f"{self.topic_prefix}/"
        if not (topic.startswith(prefix) and topic.endswith("/set")):
            return
        slug = topic[len(prefix) : -len("/set")]
        self._on_set(slug, payload)

    def connect_with_backoff(
        self,
        stop_event: threading.Event,
        max_delay_seconds: float = 30.0,
    ) -> None:
        self._client.reconnect_delay_set(min_delay=1, max_delay=int(max_delay_seconds))
        self._client.connect_async(self.host, self.port)
        self._client.loop_start()
        while not stop_event.is_set():
            if self._connected.wait(timeout=0.5):
                return
        LOGGER.info("mqtt_connect_aborted_by_stop_event")

    def disconnect(self) -> None:
        try:
            self.publish_availability(False)
        except Exception:
            pass
        self._client.loop_stop()
        self._client.disconnect()

    def publish(self, topic: str, payload: str, retain: bool = True) -> None:
        if not self._connected.is_set():
            raise RuntimeError("MQTT not connected")
        result = self._client.publish(topic, payload, qos=0, retain=retain)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed topic={topic} rc={result.rc}")

    def publish_availability(self, online: bool) -> None:
        self.publish(self.availability_topic, "online" if online else "offline", retain=True)

    @staticmethod
    def format_state(value: Any, register: Register) -> str:
        if value is None:
            return ""
        named = encode_enum(register.enum_key, value)
        if named is not None:
            return named
        if isinstance(value, float):
            if value.is_integer():
                return str(int(value))
            return f"{value:.{register.decimals}f}".rstrip("0").rstrip(".")
        return str(value)

    def publish_values(self, values: dict[str, Any]) -> None:
        for slug, value in values.items():
            register = self.by_slug.get(slug)
            if register is None or value is None:
                continue
            self.publish(self.state_topic_for(register), self.format_state(value, register), retain=True)

        featured: dict[str, Any] = {}
        for register in self.registers:
            if not register.enabled_by_default or register.slug not in values:
                continue
            value = values[register.slug]
            if value is None:
                continue
            named = encode_enum(register.enum_key, value)
            featured[register.slug] = named if named is not None else value
        self.publish(self.state_topic, json.dumps(featured), retain=True)

    def write_allowed(self, register: Register) -> bool:
        if not register.writable:
            return False
        if not self.allow_writes:
            return False
        if register.ems_gated and not self.enable_ems_writes:
            return False
        return True

    def visible_registers(self) -> list[Register]:
        visible: list[Register] = []
        for register in self.registers:
            if register.writable and not self.write_allowed(register):
                continue
            visible.append(register)
        return visible

    def _device_block(self) -> dict[str, Any]:
        device: dict[str, Any] = {
            "identifiers": [self.device_id],
            "name": self.device_name,
            "manufacturer": "Sungrow",
            "model": self._device_model or "Hybrid Inverter",
        }
        if self._device_sw_version:
            device["sw_version"] = self._device_sw_version
        return device

    def publish_discovery(self) -> None:
        device = self._device_block()
        availability = {"topic": self.availability_topic}
        configs: list[tuple[str, dict[str, Any] | str]] = []

        for register in self.visible_registers():
            topic, payload = self._register_discovery(register, device, availability)
            configs.append((topic, payload))

        for topic, payload in configs:
            body = "" if payload == "" else json.dumps(payload)
            self.publish(topic, body, retain=True)

        self._discovery_published = True
        LOGGER.info(
            "mqtt_discovery_published device_id=%s entities=%s",
            self.device_id,
            len(configs),
        )

    def _use_legacy_discovery(self, register: Register) -> bool:
        return (
            self.legacy_modqtt_compat
            and register.legacy_modqtt_suffix is not None
            and register.register_type == "input"
            and register.platform == "sensor"
        )

    def _register_discovery(
        self,
        register: Register,
        device: dict[str, Any],
        availability: dict[str, str],
    ) -> tuple[str, dict[str, Any]]:
        use_legacy = self._use_legacy_discovery(register)
        if use_legacy:
            assert register.legacy_modqtt_suffix is not None
            unique_id = f"{LEGACY_MQTT_CLIENT_ID}_{register.legacy_modqtt_suffix}"
            discovery_topic = f"homeassistant/sensor/{register.legacy_modqtt_suffix}/config"
            object_id = f"sungrow_inverter_{register.legacy_modqtt_suffix}"
        else:
            unique_id = f"{self.device_id}_{register.slug}"
            object_id = f"{self.device_id}_{register.slug}"
            discovery_topic = None  # set below by platform

        payload: dict[str, Any] = {
            "name": register.name,
            "unique_id": unique_id,
            "object_id": object_id,
            "availability": [availability],
            "device": device,
            "enabled_by_default": register.enabled_by_default,
            "state_topic": self.state_topic_for(register),
        }

        if not register.enabled_by_default:
            payload["entity_category"] = "diagnostic" if not register.writable else "config"
        elif register.writable:
            payload["entity_category"] = "config"

        if register.unit:
            payload["unit_of_measurement"] = register.unit
        if register.device_class:
            payload["device_class"] = register.device_class
        if register.state_class:
            payload["state_class"] = register.state_class

        options = enum_options(register.enum_key)

        if use_legacy:
            return discovery_topic, payload

        if options is not None and register.writable:
            payload.update(
                {
                    "command_topic": self.set_topic_for(register),
                    "options": options,
                }
            )
            payload.pop("unit_of_measurement", None)
            payload.pop("device_class", None)
            payload.pop("state_class", None)
            return f"homeassistant/select/{unique_id}/config", payload

        if options is not None:
            payload["device_class"] = "enum"
            payload["options"] = options
            payload.pop("unit_of_measurement", None)
            payload.pop("state_class", None)
            return f"homeassistant/sensor/{unique_id}/config", payload

        if register.platform == "number" and register.writable:
            payload["command_topic"] = self.set_topic_for(register)
            payload["mode"] = "box"
            if register.min_value is not None:
                payload["min"] = register.min_value
            if register.max_value is not None:
                payload["max"] = register.max_value
            step = 1.0 / register.scale if register.scale and register.scale < 1 else 1
            if register.scale == 0.1:
                step = 0.1
            elif register.scale == 0.01:
                step = 0.01
            payload["step"] = step
            payload.pop("state_class", None)
            return f"homeassistant/number/{unique_id}/config", payload

        if register.platform == "switch" and register.writable:
            payload["command_topic"] = self.set_topic_for(register)
            payload["payload_on"] = "enable"
            payload["payload_off"] = "disable"
            return f"homeassistant/switch/{unique_id}/config", payload

        return f"homeassistant/sensor/{unique_id}/config", payload
