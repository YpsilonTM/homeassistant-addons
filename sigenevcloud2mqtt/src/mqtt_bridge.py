from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from paho.mqtt import client as mqtt

LOGGER = logging.getLogger(__name__)

PHASE_MODE_SET_OPTIONS = ("1_phase", "3_phase", "auto")
PHASE_MODE_STATE_OPTIONS = ("unknown", *PHASE_MODE_SET_OPTIONS)

_REMOVED_PHASE_BUTTON_SUFFIXES = ("phase_1", "phase_3", "phase_auto")


@dataclass(frozen=True)
class BridgeState:
    status: str = "unknown"
    power_w: float = 0.0
    current_a: float = 0.0
    voltage_v: float = 0.0
    active_phases: int = 0
    lifetime_energy_kwh: float | None = None
    setpoint_a: float = 0.0
    max_current_a: float = 0.0
    phase_mode: str = "unknown"


class MqttBridge:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        username: str = "",
        password: str = "",
        topic_prefix: str = "sigen_evac",
        device_name: str = "Sigen EV AC",
        device_id: str = "sigen_evac",
        min_current: int = 6,
        max_current: int = 16,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.topic_prefix = topic_prefix.rstrip("/")
        self.device_name = device_name
        self.device_id = device_id
        self.min_current = min_current
        self.max_current = max_current

        p = self.topic_prefix
        self.availability_topic = f"{p}/availability"
        self.state_topic = f"{p}/state"
        self.current_state_topic = f"{p}/current/state"
        self.current_set_topic = f"{p}/current/set"
        self.max_current_state_topic = f"{p}/max_current/state"
        self.status_topic = f"{p}/status"
        self.power_topic = f"{p}/power_w"
        self.current_meas_topic = f"{p}/current_a"
        self.voltage_topic = f"{p}/voltage_v"
        self.active_phases_topic = f"{p}/active_phases"
        self.lifetime_energy_topic = f"{p}/lifetime_energy_kwh"
        self.lifetime_energy_availability_topic = f"{p}/lifetime_energy/availability"
        self.phase_mode_state_topic = f"{p}/phase_mode/state"
        self.phase_mode_set_topic = f"{p}/phase_mode/set"
        self.phase_mode_availability_topic = f"{p}/phase_mode/availability"
        self.command_topic = f"{p}/command"

        if hasattr(mqtt, "CallbackAPIVersion"):
            self._client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.client_id,
            )
        else:
            self._client = mqtt.Client(client_id=self.client_id)

        if username:
            self._client.username_pw_set(username, password)

        self._connected = threading.Event()
        self._discovery_published = False
        self._setpoint_out_of_range_warned = False
        self._device_model: str | None = None
        self._device_sw_version: str | None = None
        self._on_current_set: Callable[[str], None] | None = None
        self._on_phase_mode_set: Callable[[str], None] | None = None
        self._on_command: Callable[[str], None] | None = None
        self._on_reconnect: Callable[[], None] | None = None
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.will_set(self.availability_topic, "offline", qos=0, retain=True)

    def set_current_handler(self, handler: Callable[[str], None]) -> None:
        self._on_current_set = handler

    def set_phase_mode_handler(self, handler: Callable[[str], None]) -> None:
        self._on_phase_mode_set = handler

    def set_command_handler(self, handler: Callable[[str], None]) -> None:
        self._on_command = handler

    def set_reconnect_handler(self, handler: Callable[[], None]) -> None:
        self._on_reconnect = handler

    def update_device_info(self, model: str | None, sw_version: str | None) -> None:
        if model:
            self._device_model = model
        if sw_version:
            self._device_sw_version = sw_version

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
        client.subscribe(self.phase_mode_set_topic)
        client.subscribe(self.command_topic)
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
        if topic == self.current_set_topic and self._on_current_set:
            self._on_current_set(payload)
        elif topic == self.phase_mode_set_topic and self._on_phase_mode_set:
            self._on_phase_mode_set(payload)
        elif topic == self.command_topic and self._on_command:
            self._on_command(payload)

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
            self.publish_lifetime_energy(None)
        except Exception:
            pass
        try:
            self.publish_phase_mode("unknown")
        except Exception:
            pass
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

    def publish_lifetime_energy(self, lifetime_kwh: float | None) -> None:
        """Publish lifetime total or mark the sensor unavailable (never publish 0 as a glitch)."""
        if lifetime_kwh is not None:
            self.publish(self.lifetime_energy_availability_topic, "online", retain=True)
            self.publish(self.lifetime_energy_topic, self._num(lifetime_kwh), retain=True)
        else:
            self.publish(self.lifetime_energy_availability_topic, "offline", retain=True)

    def publish_state_bundle(self, state: BridgeState) -> None:
        raw_setpoint = state.setpoint_a
        setpoint = min(float(self.max_current), max(float(self.min_current), float(raw_setpoint)))
        if setpoint != float(raw_setpoint) and not self._setpoint_out_of_range_warned:
            LOGGER.warning(
                "setpoint_clamped raw=%s clamped=%s range=[%s,%s]",
                raw_setpoint,
                setpoint,
                self.min_current,
                self.max_current,
            )
            self._setpoint_out_of_range_warned = True

        self.publish(self.status_topic, state.status, retain=True)
        self.publish(self.power_topic, self._num(state.power_w), retain=True)
        self.publish(self.current_meas_topic, self._num(state.current_a), retain=True)
        self.publish(self.voltage_topic, self._num(state.voltage_v), retain=True)
        self.publish(self.active_phases_topic, str(state.active_phases), retain=True)
        self.publish_lifetime_energy(state.lifetime_energy_kwh)
        self.publish(self.current_state_topic, self._num(setpoint), retain=True)
        self.publish(self.max_current_state_topic, self._num(state.max_current_a), retain=True)
        self.publish_phase_mode(state.phase_mode)
        bundle = {
            "status": state.status,
            "power_w": state.power_w,
            "current_a": state.current_a,
            "voltage_v": state.voltage_v,
            "active_phases": state.active_phases,
            "setpoint_a": setpoint,
            "max_current_a": state.max_current_a,
            "phase_mode": state.phase_mode,
        }
        if state.lifetime_energy_kwh is not None:
            bundle["lifetime_energy_kwh"] = state.lifetime_energy_kwh
        self.publish(self.state_topic, json.dumps(bundle), retain=True)

    def publish_phase_mode(self, mode: str) -> None:
        if mode == "unknown":
            self.publish(self.phase_mode_availability_topic, "offline", retain=True)
            return
        self.publish(self.phase_mode_availability_topic, "online", retain=True)
        self.publish(self.phase_mode_state_topic, mode, retain=True)

    def publish_discovery(self) -> None:
        device = {
            "identifiers": [self.device_id],
            "name": self.device_name,
            "manufacturer": "Sigenergy",
            "model": self._device_model or "EV AC (cloud)",
        }
        if self._device_sw_version:
            device["sw_version"] = self._device_sw_version

        availability = {"topic": self.availability_topic}
        lifetime_energy_availability = {
            "topic": self.lifetime_energy_availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
        }
        phase_mode_availability = {
            "topic": self.phase_mode_availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
        }

        def sensor(name, uid, topic, unit=None, device_class=None, state_class=None, category=None):
            payload = {
                "name": name,
                "unique_id": f"{self.device_id}_{uid}",
                "state_topic": topic,
                "availability": [availability],
                "device": device,
            }
            if unit:
                payload["unit_of_measurement"] = unit
            if device_class:
                payload["device_class"] = device_class
            if state_class:
                payload["state_class"] = state_class
            if category:
                payload["entity_category"] = category
            return payload

        configs = [
            (
                f"homeassistant/number/{self.device_id}_laadstroom/config",
                {
                    "name": "Sigen EV Laadstroom",
                    "unique_id": f"{self.device_id}_laadstroom",
                    "command_topic": self.current_set_topic,
                    "state_topic": self.current_state_topic,
                    "availability": [availability],
                    "min": self.min_current,
                    "max": self.max_current,
                    "step": 1,
                    "unit_of_measurement": "A",
                    "icon": "mdi:current-ac",
                    "mode": "slider",
                    "device": device,
                },
            ),
            (
                f"homeassistant/sensor/{self.device_id}_max_current/config",
                sensor(
                    "Sigen EV Max Current",
                    "max_current",
                    self.max_current_state_topic,
                    unit="A",
                    device_class="current",
                    category="diagnostic",
                ),
            ),
            (
                f"homeassistant/sensor/{self.device_id}_status/config",
                sensor("Sigen EV Status", "status", self.status_topic, category="diagnostic"),
            ),
            (
                f"homeassistant/sensor/{self.device_id}_power/config",
                sensor(
                    "Sigen EV Charging Power",
                    "power",
                    self.power_topic,
                    unit="W",
                    device_class="power",
                    state_class="measurement",
                ),
            ),
            (
                f"homeassistant/sensor/{self.device_id}_current/config",
                sensor(
                    "Sigen EV Charging Current",
                    "current_meas",
                    self.current_meas_topic,
                    unit="A",
                    device_class="current",
                    state_class="measurement",
                ),
            ),
            (
                f"homeassistant/sensor/{self.device_id}_voltage/config",
                sensor(
                    "Sigen EV Charging Voltage",
                    "voltage",
                    self.voltage_topic,
                    unit="V",
                    device_class="voltage",
                    state_class="measurement",
                ),
            ),
            (
                f"homeassistant/sensor/{self.device_id}_active_phases/config",
                sensor(
                    "Sigen EV Active Phases",
                    "active_phases",
                    self.active_phases_topic,
                    state_class="measurement",
                    category="diagnostic",
                ),
            ),
            (
                f"homeassistant/sensor/{self.device_id}_lifetime_energy/config",
                {
                    "name": "Sigen EV Lifetime Energy",
                    "unique_id": f"{self.device_id}_lifetime_energy",
                    "state_topic": self.lifetime_energy_topic,
                    "availability": [availability, lifetime_energy_availability],
                    "availability_mode": "all",
                    "unit_of_measurement": "kWh",
                    "device_class": "energy",
                    "state_class": "total_increasing",
                    "device": device,
                },
            ),
            (
                f"homeassistant/select/{self.device_id}_phase_mode/config",
                {
                    "name": "Sigen EV Phase Mode",
                    "unique_id": f"{self.device_id}_phase_mode",
                    "state_topic": self.phase_mode_state_topic,
                    "command_topic": self.phase_mode_set_topic,
                    "availability": [availability, phase_mode_availability],
                    "availability_mode": "all",
                    "options": list(PHASE_MODE_SET_OPTIONS),
                    "icon": "mdi:sine-wave",
                    "entity_category": "config",
                    "device": device,
                },
            ),
            (
                f"homeassistant/button/{self.device_id}_start/config",
                {
                    "name": "Sigen EV Start Charging",
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
                    "name": "Sigen EV Stop Charging",
                    "unique_id": f"{self.device_id}_stop",
                    "command_topic": self.command_topic,
                    "payload_press": "STOP",
                    "availability": [availability],
                    "icon": "mdi:stop-circle-outline",
                    "device": device,
                },
            ),
        ]

        for topic, payload in configs:
            self.publish(topic, json.dumps(payload), retain=True)

        # Clear retained discovery for removed duplicate phase buttons.
        for suffix in _REMOVED_PHASE_BUTTON_SUFFIXES:
            clear_topic = f"homeassistant/button/{self.device_id}_{suffix}/config"
            self.publish(clear_topic, "", retain=True)

        self._discovery_published = True
        LOGGER.info("mqtt_discovery_published device_id=%s entities=%s", self.device_id, len(configs))
