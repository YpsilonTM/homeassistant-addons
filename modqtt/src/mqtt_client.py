from __future__ import annotations

import logging
import time

from paho.mqtt import client as mqtt

LOGGER = logging.getLogger(__name__)

# paho's network loop reconnects on its own once loop_start() ran, so the publish
# path waits briefly for that instead of driving reconnects from this thread.
_PUBLISH_MAX_ATTEMPTS = 2
_RECONNECT_GRACE_SECONDS = 1.0
_CONNECT_WAIT_SECONDS = 2.0
_RECONNECT_MIN_DELAY = 1
_RECONNECT_MAX_DELAY = 30
# Fallback for the rare case where the loop thread is gone and paho stopped
# retrying by itself.
_STALE_RECONNECT_SECONDS = 60.0


class MqttPublisher:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        username: str | None = None,
        password: str | None = None,
        availability_topic: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self._username = username
        self._password = password
        self._availability_topic = availability_topic
        if hasattr(mqtt, "CallbackAPIVersion"):
            self._client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.client_id,
            )
        else:
            self._client = mqtt.Client(client_id=self.client_id)
        if self._username:
            self._client.username_pw_set(self._username, self._password)
        if self._availability_topic:
            self._client.will_set(
                self._availability_topic,
                payload="offline",
                qos=0,
                retain=True,
            )
        self._connected = False
        self._loop_started = False
        self._last_connected_at: float | None = None
        self._last_manual_reconnect_at: float | None = None
        self._client.reconnect_delay_set(
            min_delay=_RECONNECT_MIN_DELAY,
            max_delay=_RECONNECT_MAX_DELAY,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        del client, userdata, flags, properties
        is_failure = getattr(reason_code, "is_failure", None)
        if is_failure is True or (isinstance(reason_code, int) and reason_code != 0):
            self._connected = False
            LOGGER.warning("mqtt_connect_rejected reason=%s", reason_code)
            return

        self._connected = True
        self._last_connected_at = time.monotonic()
        LOGGER.info("mqtt_connected host=%s port=%s", self.host, self.port)
        if self._availability_topic:
            result = self._client.publish(
                topic=self._availability_topic,
                payload="online",
                qos=0,
                retain=True,
            )
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                LOGGER.warning(
                    "mqtt_availability_online_failed topic=%s rc=%s",
                    self._availability_topic,
                    result.rc,
                )

    def _on_disconnect(self, client, userdata, *args, **kwargs):
        del client, userdata
        # paho v1 passes (rc,); paho v2 passes (disconnect_flags, reason_code, properties).
        reason_code = kwargs.get("reason_code")
        if reason_code is None and args:
            reason_code = args[1] if len(args) >= 2 else args[0]
        self._connected = False
        LOGGER.warning(
            "mqtt_disconnected reason=%s host=%s awaiting_auto_reconnect=%s",
            reason_code,
            self.host,
            self._loop_started,
        )

    def _wait_for_connection(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while not self._connected and time.monotonic() < deadline:
            time.sleep(0.1)
        return self._connected

    def _attempt_connect(self) -> bool:
        """One time-bounded connect attempt. Starts the network loop at most once."""
        try:
            if not self._loop_started:
                self._client.connect(host=self.host, port=self.port)
                self._client.loop_start()
                self._loop_started = True
            return self._wait_for_connection(_CONNECT_WAIT_SECONDS)
        except Exception as exc:
            LOGGER.warning(
                "mqtt_connect_error host=%s port=%s error=%s",
                self.host,
                self.port,
                exc,
            )
            return False

    def connect_with_backoff(self, max_delay_seconds: float = 30.0) -> None:
        """Block until connected. Used at startup; the network loop starts once."""
        if self._connected:
            return

        delay = 1.0
        while not self._attempt_connect():
            LOGGER.warning(
                "mqtt_connect_retry host=%s port=%s next_delay=%.1f",
                self.host,
                self.port,
                delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, max_delay_seconds)

    def disconnect(self) -> None:
        try:
            if self._connected:
                self._client.disconnect()
        finally:
            if self._loop_started:
                self._client.loop_stop()
                self._loop_started = False
            self._connected = False

    def _maybe_manual_reconnect(self) -> None:
        now = time.monotonic()
        last_activity = max(
            self._last_connected_at or 0.0,
            self._last_manual_reconnect_at or 0.0,
        )
        if now - last_activity < _STALE_RECONNECT_SECONDS:
            return

        self._last_manual_reconnect_at = now
        LOGGER.warning("mqtt_manual_reconnect host=%s port=%s", self.host, self.port)
        try:
            self._client.reconnect()
        except Exception as exc:
            LOGGER.warning("mqtt_manual_reconnect_failed host=%s error=%s", self.host, exc)
            return
        self._wait_for_connection(_RECONNECT_GRACE_SECONDS)

    def _ensure_connected(self) -> bool:
        """Best-effort, time-bounded connection check. Never blocks indefinitely."""
        if self._connected:
            return True
        if not self._loop_started:
            # Never connected yet: try once rather than blocking the caller.
            return self._attempt_connect()
        if self._wait_for_connection(_RECONNECT_GRACE_SECONDS):
            return True

        self._maybe_manual_reconnect()
        return self._connected

    def publish_state(self, topic: str, payload: str, retain: bool = True) -> None:
        last_rc: int | None = None
        for attempt in range(1, _PUBLISH_MAX_ATTEMPTS + 1):
            if not self._ensure_connected():
                last_rc = mqtt.MQTT_ERR_NO_CONN
                break

            result = self._client.publish(topic=topic, payload=payload, qos=0, retain=retain)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                return

            last_rc = result.rc
            # rc=4 (MQTT_ERR_NO_CONN) after a broker restart: the flag can still
            # say connected, so clear it and let the loop thread reconnect.
            self._connected = False
            LOGGER.warning(
                "mqtt_publish_failed topic=%s rc=%s attempt=%s/%s",
                topic,
                result.rc,
                attempt,
                _PUBLISH_MAX_ATTEMPTS,
            )

        raise RuntimeError(f"Failed to publish topic={topic} rc={last_rc}")

    def publish_availability(self, topic: str, online: bool) -> None:
        message = "online" if online else "offline"
        self.publish_state(topic=topic, payload=message, retain=True)
