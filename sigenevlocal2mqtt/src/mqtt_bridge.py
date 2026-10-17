from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from paho.mqtt import client as mqtt

from src.catalog import Tag

LOGGER = logging.getLogger(__name__)

UNIT_DEVICE_CLASS = {
    "A": "current",
    "V": "voltage",
    "W": "power",
    "kW": "power",
    "kWh": "energy",
    "Hz": "frequency",
    "℃": "temperature",
    "°C": "temperature",
    "C": "temperature",
}
ENERGY_TOTAL_INCREASING = {11350, 1330, 1334, 1338}
ENERGY_TOTAL = {11298}
CURRENT_TAG = 12194
START_STOP_TAG = 12000
START_VALUE = 0
STOP_VALUE = 1
POWER_TAG = 11220
LIFETIME_TAG = 11350
PHASE_CURRENT_TAGS = (11213, 11215, 11217)
VOLTAGE_TAG = 11210
RUN_STATE_TAG = 11265


class MqttBridge:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        tags: list[Tag],
        username: str = "",
        password: str = "",
        topic_prefix: str = "sigen_evac_local",
        device_name: str = "Sigen EV AC Local",
        device_id: str = "sigen_evac_local",
        min_current: int = 6,
        max_current: int = 16,
        enable_dangerous_commands: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.tags = tags
        self.tags_by_id = {tag.id: tag for tag in tags}
        self.topic_prefix = topic_prefix.rstrip("/")
        self.device_name = device_name
        self.device_id = device_id
        self.min_current = min_current
        self.max_current = max_current
        self.enable_dangerous_commands = enable_dangerous_commands

        prefix = self.topic_prefix
        self.availability_topic = f"{prefix}/availability"
        self.state_topic = f"{prefix}/state"
        self.current_state_topic = f"{prefix}/current/state"
        self.current_set_topic = f"{prefix}/current/set"
        self.power_topic = f"{prefix}/power_w"
        self.current_meas_topic = f"{prefix}/current_a"
        self.voltage_topic = f"{prefix}/voltage_v"
        self.active_phases_topic = f"{prefix}/active_phases"
        self.lifetime_energy_topic = f"{prefix}/lifetime_energy_kwh"
        self.status_topic = f"{prefix}/status"
        self.command_topic = f"{prefix}/command"

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
        self._on_tag_set: Callable[[int, str], None] | None = None
        self._on_command: Callable[[str], None] | None = None
        self._on_reconnect: Callable[[], None] | None = None
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.will_set(self.availability_topic, "offline", qos=0, retain=True)

    def set_tag_handler(self, handler: Callable[[int, str], None]) -> None:
        self._on_tag_set = handler

    def set_command_handler(self, handler: Callable[[str], None]) -> None:
        self._on_command = handler

    def set_reconnect_handler(self, handler: Callable[[], None]) -> None:
        self._on_reconnect = handler

    def update_device_info(self, model: str | None, sw_version: str | None) -> None:
        if model:
            self._device_model = model
        if sw_version:
            self._device_sw_version = sw_version

    def tag_state_topic(self, tag: Tag) -> str:
        return f"{self.topic_prefix}/tag/{tag.id}/state"

    def tag_set_topic(self, tag: Tag) -> str:
        return f"{self.topic_prefix}/tag/{tag.id}/set"

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
        client.subscribe(self.current_set_topic)
        client.subscribe(self.command_topic)
        client.subscribe(f"{self.topic_prefix}/tag/+/set")
        LOGGER.info("mqtt_connected host=%s port=%s", self.host, self.port)
        if self._on_reconnect:
            self._on_reconnect()

    def _on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None):
        del client, userdata, disconnect_flags, properties
        self._connected.clear()
        LOGGER.warning("mqtt_disconnected reason=%s", reason_code)

    def _on_message(self, client, userdata, message):
        del client, userdata
        payload = message.payload.decode("utf-8", errors="replace").strip()
        topic = message.topic
        if topic == self.command_topic:
            if self._on_command:
                self._on_command(payload)
            return
        if self._on_tag_set is None:
            return
        if topic == self.current_set_topic:
            self._on_tag_set(CURRENT_TAG, payload)
            return
        prefix = f"{self.topic_prefix}/tag/"
        if topic.startswith(prefix) and topic.endswith("/set"):
            raw_id = topic[len(prefix) : -len("/set")]
            try:
                tag_id = int(raw_id)
            except ValueError:
                LOGGER.warning("invalid_tag_topic topic=%s", topic)
                return
            self._on_tag_set(tag_id, payload)

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
    def _num(value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.2f}"

    @classmethod
    def format_state(cls, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            return cls._num(value)
        return str(value)

    def publish_values(self, values: dict[int, Any]) -> None:
        for tag_id, value in values.items():
            tag = self.tags_by_id.get(tag_id)
            if tag is None:
                continue
            self.publish(self.tag_state_topic(tag), self.format_state(value), retain=True)

        current = values.get(CURRENT_TAG)
        if isinstance(current, (int, float)):
            clamped = min(float(self.max_current), max(float(self.min_current), float(current)))
            self.publish(self.current_state_topic, self._num(clamped), retain=True)

        power_kw = values.get(POWER_TAG)
        if isinstance(power_kw, (int, float)):
            self.publish(self.power_topic, self._num(round(float(power_kw) * 1000, 1)), retain=True)

        phase_a = values.get(11213)
        if isinstance(phase_a, (int, float)):
            self.publish(self.current_meas_topic, self._num(phase_a), retain=True)

        voltage = values.get(VOLTAGE_TAG)
        if isinstance(voltage, (int, float)):
            self.publish(self.voltage_topic, self._num(voltage), retain=True)

        lifetime = values.get(LIFETIME_TAG)
        if isinstance(lifetime, (int, float)):
            self.publish(self.lifetime_energy_topic, self._num(lifetime), retain=True)

        run_state = values.get(RUN_STATE_TAG)
        if run_state is not None:
            self.publish(self.status_topic, str(run_state), retain=True)

        self.publish(self.active_phases_topic, str(active_phases(values)), retain=True)
        self.publish(self.state_topic, json.dumps(self._state_bundle(values)), retain=True)

    def _state_bundle(self, values: dict[int, Any]) -> dict[str, Any]:
        featured = {}
        for tag in self.tags:
            if not tag.enabled_by_default or tag.secret or tag.id not in values:
                continue
            featured[tag.slug] = values[tag.id]
        power_kw = values.get(POWER_TAG)
        current = values.get(CURRENT_TAG)
        return {
            "status": values.get(RUN_STATE_TAG),
            "power_w": round(float(power_kw) * 1000, 1) if isinstance(power_kw, (int, float)) else None,
            "current_a": values.get(11213),
            "voltage_v": values.get(VOLTAGE_TAG),
            "active_phases": active_phases(values),
            "setpoint_a": current,
            "lifetime_energy_kwh": values.get(LIFETIME_TAG),
            "featured": featured,
        }

    def visible_tags(self) -> list[Tag]:
        visible = []
        for tag in self.tags:
            if tag.id == START_STOP_TAG:
                continue
            if tag.dangerous and not self.enable_dangerous_commands:
                continue
            visible.append(tag)
        return visible

    def publish_discovery(self) -> None:
        device = {
            "identifiers": [self.device_id],
            "name": self.device_name,
            "manufacturer": "Sigenergy",
            "model": self._device_model or "EV AC (local)",
        }
        if self._device_sw_version:
            device["sw_version"] = self._device_sw_version

        availability = {"topic": self.availability_topic}
        configs: list[tuple[str, dict[str, Any]]] = [
            (
                f"homeassistant/sensor/{self.device_id}_power/config",
                {
                    "name": "Charging Power",
                    "unique_id": f"{self.device_id}_power",
                    "state_topic": self.power_topic,
                    "availability": [availability],
                    "unit_of_measurement": "W",
                    "device_class": "power",
                    "state_class": "measurement",
                    "device": device,
                },
            ),
            (
                f"homeassistant/sensor/{self.device_id}_current_meas/config",
                {
                    "name": "Charging Current",
                    "unique_id": f"{self.device_id}_current_meas",
                    "state_topic": self.current_meas_topic,
                    "availability": [availability],
                    "unit_of_measurement": "A",
                    "device_class": "current",
                    "state_class": "measurement",
                    "device": device,
                },
            ),
            (
                f"homeassistant/sensor/{self.device_id}_voltage/config",
                {
                    "name": "Charging Voltage",
                    "unique_id": f"{self.device_id}_voltage",
                    "state_topic": self.voltage_topic,
                    "availability": [availability],
                    "unit_of_measurement": "V",
                    "device_class": "voltage",
                    "state_class": "measurement",
                    "device": device,
                },
            ),
            (
                f"homeassistant/sensor/{self.device_id}_active_phases/config",
                {
                    "name": "Active Phases",
                    "unique_id": f"{self.device_id}_active_phases",
                    "state_topic": self.active_phases_topic,
                    "availability": [availability],
                    "state_class": "measurement",
                    "entity_category": "diagnostic",
                    "device": device,
                },
            ),
            (
                f"homeassistant/sensor/{self.device_id}_status/config",
                {
                    "name": "Run State",
                    "unique_id": f"{self.device_id}_status",
                    "state_topic": self.status_topic,
                    "availability": [availability],
                    "entity_category": "diagnostic",
                    "device": device,
                },
            ),
            (
                f"homeassistant/button/{self.device_id}_start/config",
                {
                    "name": "Start Charging",
                    "unique_id": f"{self.device_id}_start",
                    "command_topic": self.command_topic,
                    "payload_press": "START",
                    "availability": [availability],
                    "icon": "mdi:ev-station",
                    "device": device,
                },
            ),
            (
                f"homeassistant/button/{self.device_id}_stop/config",
                {
                    "name": "Stop Charging",
                    "unique_id": f"{self.device_id}_stop",
                    "command_topic": self.command_topic,
                    "payload_press": "STOP",
                    "availability": [availability],
                    "icon": "mdi:stop-circle-outline",
                    "device": device,
                },
            ),
        ]

        for tag in self.visible_tags():
            configs.append(self._tag_discovery(tag, device, availability))

        for topic, payload in configs:
            self.publish(topic, json.dumps(payload), retain=True)

        self._discovery_published = True
        LOGGER.info("mqtt_discovery_published device_id=%s entities=%s", self.device_id, len(configs))

    def _tag_discovery(self, tag: Tag, device: dict[str, Any], availability: dict[str, str]) -> tuple[str, dict[str, Any]]:
        unique_id = f"{self.device_id}_{tag.slug}"
        payload: dict[str, Any] = {
            "name": tag.name,
            "unique_id": unique_id,
            "availability": [availability],
            "device": device,
            "enabled_by_default": tag.enabled_by_default and not tag.secret,
            "object_id": f"{self.device_id}_{tag.slug}",
        }
        if tag.secret or not tag.enabled_by_default:
            payload["entity_category"] = "diagnostic" if tag.access == "RO" else "config"
        if tag.unit:
            payload["unit_of_measurement"] = tag.unit
        device_class = UNIT_DEVICE_CLASS.get(tag.unit)
        if device_class:
            payload["device_class"] = device_class
        if tag.id in ENERGY_TOTAL_INCREASING:
            payload["state_class"] = "total_increasing"
        elif tag.id in ENERGY_TOTAL:
            payload["state_class"] = "total"
        elif device_class in {"power", "current", "voltage", "temperature", "frequency"}:
            payload["state_class"] = "measurement"

        platform = tag.platform
        if tag.id == CURRENT_TAG:
            payload.update(
                {
                    "command_topic": self.current_set_topic,
                    "state_topic": self.current_state_topic,
                    "min": self.min_current,
                    "max": self.max_current,
                    "step": 1,
                    "mode": "slider",
                    "icon": "mdi:current-ac",
                    "entity_category": "config",
                    "enabled_by_default": True,
                }
            )
            payload.pop("state_class", None)
            return f"homeassistant/number/{unique_id}/config", payload

        if platform == "sensor":
            payload["state_topic"] = self.tag_state_topic(tag)
            return f"homeassistant/sensor/{unique_id}/config", payload

        if platform == "number":
            payload.update(self._number_bounds(tag))
            payload["command_topic"] = self.tag_set_topic(tag)
            payload["state_topic"] = self.tag_state_topic(tag)
            payload["mode"] = "box"
            payload.pop("state_class", None)
            return f"homeassistant/number/{unique_id}/config", payload

        if platform == "text":
            payload["command_topic"] = self.tag_set_topic(tag)
            payload["state_topic"] = self.tag_state_topic(tag)
            if tag.secret:
                payload["mode"] = "password"
            return f"homeassistant/text/{unique_id}/config", payload

        payload["command_topic"] = self.tag_set_topic(tag)
        payload["payload_press"] = "1"
        payload["entity_category"] = "config"
        if tag.dangerous:
            payload["icon"] = "mdi:alert"
        return f"homeassistant/button/{unique_id}/config", payload

    def _number_bounds(self, tag: Tag) -> dict[str, float]:
        step = 1.0 / tag.scale if tag.scale > 1 else 1
        bounds: dict[str, float] = {"step": step}
        if tag.id == CURRENT_TAG:
            bounds["min"] = float(self.min_current)
            bounds["max"] = float(self.max_current)
            return bounds
        if 1 < tag.scale_or_max < 100000 and tag.scale == 1:
            bounds["min"] = 0
            bounds["max"] = float(tag.scale_or_max)
        return bounds


def active_phases(values: dict[int, Any]) -> int:
    count = 0
    for tag_id in PHASE_CURRENT_TAGS:
        value = values.get(tag_id)
        if isinstance(value, (int, float)) and abs(float(value)) >= 0.3:
            count += 1
    return count
