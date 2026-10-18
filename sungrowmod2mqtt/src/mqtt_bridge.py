from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from paho.mqtt import client as mqtt

from src.catalog import Register
from src.enums import encode_enum, enum_options

LOGGER = logging.getLogger(__name__)

LEGACY_MQTT_CLIENT_ID = "modqtt-addon"
HOLDING_REFRESH_SECONDS = 300.0
ADDON_NAME = "Sungrow Modbus to MQTT"
ADDON_VERSION = "0.3.0"
ADDON_URL = "https://github.com/YpsilonTM/homeassistant-addons"
DISCOVERY_MARKER_NAME = "sungrowmod2mqtt_device_discovery_v1"


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
        device_name: str = "Sungrow Inverter",
        device_id: str = "sungrow",
        allow_writes: bool = False,
        enable_ems_writes: bool = False,
        legacy_modqtt_compat: bool = False,
        enabled_slugs: set[str] | None = None,
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
        if enabled_slugs is None:
            self.enabled_slugs = {reg.slug for reg in registers if reg.enabled_by_default}
        else:
            self.enabled_slugs = set(enabled_slugs)

        self.availability_topic = f"{self.topic_prefix}/availability"
        self.state_topic = f"{self.topic_prefix}/state"
        self.device_discovery_topic = f"homeassistant/device/{self.device_id}/config"

        if hasattr(mqtt, "CallbackAPIVersion"):
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.client_id)
        else:
            self._client = mqtt.Client(client_id=self.client_id)

        if username:
            self._client.username_pw_set(username, password)

        self._connected = threading.Event()
        self._disconnected_at: float | None = time.monotonic()
        self._discovery_published = False
        self._device_model: str | None = None
        self._device_sw_version: str | None = None
        self._on_set: Callable[[str, str], None] | None = None
        self._on_reconnect: Callable[[], None] | None = None
        self._publish_cache: dict[str, str] = {}
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

    def is_connected(self) -> bool:
        return self._connected.is_set()

    def disconnected_for_seconds(self) -> float:
        if self._connected.is_set() or self._disconnected_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._disconnected_at)

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
        self._disconnected_at = None
        self._publish_cache.clear()
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
        if self._disconnected_at is None:
            self._disconnected_at = time.monotonic()
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
        if retain and self._publish_cache.get(topic) == payload:
            LOGGER.debug("mqtt_publish_skip_unchanged topic=%s", topic)
            return
        result = self._client.publish(topic, payload, qos=0, retain=retain)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed topic={topic} rc={result.rc}")
        if retain:
            self._publish_cache[topic] = payload

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

    def register_enabled(self, register: Register) -> bool:
        return register.slug in self.enabled_slugs

    def publish_values(self, values: dict[str, Any]) -> None:
        for slug, value in values.items():
            register = self.by_slug.get(slug)
            if register is None or value is None:
                continue
            if not self.register_enabled(register):
                continue
            self.publish(self.state_topic_for(register), self.format_state(value, register), retain=True)

        featured: dict[str, Any] = {}
        for register in self.registers:
            if not self.register_enabled(register) or register.slug not in values:
                continue
            value = values[register.slug]
            if value is None:
                continue
            named = encode_enum(register.enum_key, value)
            featured[register.slug] = named if named is not None else value
        self.publish(self.state_topic, json.dumps(featured), retain=True)

    def clear_disabled_retained_state(self) -> None:
        """Clear retained MQTT state for registers that are turned off."""
        for register in self.registers:
            if self.register_enabled(register):
                continue
            try:
                self.publish(self.state_topic_for(register), "", retain=True)
            except Exception:
                LOGGER.exception(
                    "clear_disabled_state_failed slug=%s topic=%s",
                    register.slug,
                    self.state_topic_for(register),
                )

    def write_allowed(self, register: Register) -> bool:
        if not self.register_enabled(register):
            return False
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
            if not self.register_enabled(register):
                continue
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

    def _use_legacy_discovery(self, register: Register) -> bool:
        return (
            self.legacy_modqtt_compat
            and register.legacy_modqtt_suffix is not None
            and register.register_type == "input"
            and register.platform == "sensor"
        )

    def _unique_id(self, register: Register) -> str:
        if self._use_legacy_discovery(register):
            assert register.legacy_modqtt_suffix is not None
            return f"{LEGACY_MQTT_CLIENT_ID}_{register.legacy_modqtt_suffix}"
        return f"{self.device_id}_{register.slug}"

    def _object_id(self, register: Register) -> str:
        if self._use_legacy_discovery(register):
            assert register.legacy_modqtt_suffix is not None
            return f"sungrow_inverter_{register.legacy_modqtt_suffix}"
        return f"{self.device_id}_{register.slug}"

    def _platform_for(self, register: Register) -> str:
        options = enum_options(register.enum_key)
        if options is not None and register.writable:
            return "select"
        if options is not None:
            return "sensor"
        if register.platform == "number" and register.writable:
            return "number"
        if register.platform == "switch" and register.writable:
            return "switch"
        return "sensor"

    def _legacy_discovery_topic(self, register: Register) -> str | None:
        if not self._use_legacy_discovery(register):
            return None
        assert register.legacy_modqtt_suffix is not None
        return f"homeassistant/sensor/{register.legacy_modqtt_suffix}/config"

    def _single_discovery_topic(self, register: Register) -> str:
        legacy = self._legacy_discovery_topic(register)
        if legacy is not None:
            return legacy
        unique_id = self._unique_id(register)
        platform = self._platform_for(register)
        return f"homeassistant/{platform}/{unique_id}/config"

    def _number_step(self, register: Register) -> float:
        if register.scale and register.scale > 0:
            return float(register.scale)
        return 1.0

    def _component_payload(self, register: Register) -> dict[str, Any]:
        """Build a device-discovery component dict (includes platform `p`)."""
        platform = self._platform_for(register)
        if not self.register_enabled(register):
            return {"p": platform}
        if register.writable and not self.write_allowed(register):
            return {"p": platform}

        unique_id = self._unique_id(register)
        object_id = self._object_id(register)
        payload: dict[str, Any] = {
            "p": platform,
            "name": register.name,
            "unique_id": unique_id,
            "default_entity_id": f"{platform}.{object_id}",
            "enabled_by_default": True,
            "state_topic": self.state_topic_for(register),
        }

        if register.writable:
            payload["entity_category"] = "config"

        if register.unit:
            payload["unit_of_measurement"] = register.unit
        if register.device_class:
            payload["device_class"] = register.device_class
        if register.state_class:
            payload["state_class"] = register.state_class

        options = enum_options(register.enum_key)

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
            return payload

        if options is not None:
            payload["device_class"] = "enum"
            payload["options"] = options
            payload.pop("unit_of_measurement", None)
            payload.pop("state_class", None)
            return payload

        if platform == "number":
            payload["command_topic"] = self.set_topic_for(register)
            payload["mode"] = "box"
            if register.min_value is not None:
                payload["min"] = register.min_value
            if register.max_value is not None:
                payload["max"] = register.max_value
            payload["step"] = self._number_step(register)
            payload.pop("state_class", None)
            return payload

        if platform == "switch":
            payload["command_topic"] = self.set_topic_for(register)
            payload["payload_on"] = "enable"
            payload["payload_off"] = "disable"
            return payload

        return payload

    def build_device_discovery(self) -> dict[str, Any]:
        cmps: dict[str, dict[str, Any]] = {}
        for register in self.registers:
            cmps[register.slug] = self._component_payload(register)
        return {
            "dev": self._device_block(),
            "o": {
                "name": ADDON_NAME,
                "sw": ADDON_VERSION,
                "url": ADDON_URL,
            },
            "avty": [{"topic": self.availability_topic}],
            "cmps": cmps,
        }

    @staticmethod
    def discovery_marker_path() -> Path:
        data_dir = Path("/data")
        if data_dir.is_dir():
            return data_dir / DISCOVERY_MARKER_NAME
        return Path(__file__).resolve().parents[1] / f".{DISCOVERY_MARKER_NAME}"

    def _migrate_from_single_discovery(self) -> None:
        migrate_payload = json.dumps({"migrate_discovery": True})
        for register in self.registers:
            topic = self._single_discovery_topic(register)
            try:
                self.publish(topic, migrate_payload, retain=True)
            except Exception:
                LOGGER.exception("discovery_migrate_publish_failed topic=%s", topic)

    def _clear_single_discovery(self) -> None:
        for register in self.registers:
            topic = self._single_discovery_topic(register)
            try:
                # Bypass publish-on-change cache so cleanup always lands.
                if not self._connected.is_set():
                    raise RuntimeError("MQTT not connected")
                result = self._client.publish(topic, "", qos=0, retain=True)
                if result.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError(f"MQTT publish failed topic={topic} rc={result.rc}")
                self._publish_cache[topic] = ""
            except Exception:
                LOGGER.exception("discovery_clear_failed topic=%s", topic)

    def publish_discovery(self) -> None:
        marker = self.discovery_marker_path()
        migrating = not marker.exists()
        if migrating:
            LOGGER.info("discovery_migration_start marker=%s", marker)
            self._migrate_from_single_discovery()

        self.device_discovery_topic = f"homeassistant/device/{self.device_id}/config"
        payload = self.build_device_discovery()
        self.publish(self.device_discovery_topic, json.dumps(payload), retain=True)
        self.clear_disabled_retained_state()

        if migrating:
            self._clear_single_discovery()
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(f"{ADDON_VERSION}\n", encoding="utf-8")
            except OSError:
                LOGGER.exception("discovery_marker_write_failed path=%s", marker)
            else:
                LOGGER.info("discovery_migration_complete marker=%s", marker)

        visible = sum(1 for cmp in payload["cmps"].values() if "unique_id" in cmp)
        hidden = len(payload["cmps"]) - visible
        self._discovery_published = True
        LOGGER.info(
            "mqtt_discovery_published device_id=%s components=%s hidden=%s",
            self.device_id,
            visible,
            hidden,
        )
