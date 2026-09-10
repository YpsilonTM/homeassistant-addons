from __future__ import annotations

import inspect
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from paho.mqtt import client as mqtt

from src.mqtt_client import _PUBLISH_MAX_ATTEMPTS, _STALE_RECONNECT_SECONDS, MqttPublisher


class FakeTime:
    """Deterministic clock that fails the test instead of hanging on a runaway loop."""

    def __init__(self, max_sleeps: int = 200) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        self.max_sleeps = max_sleeps
        self.on_sleep: Callable[[FakeTime], None] | None = None

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if len(self.sleeps) > self.max_sleeps:
            raise AssertionError(f"unbounded sleep loop: {len(self.sleeps)} sleep calls")
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep(self)


@pytest.fixture
def fake_time(monkeypatch: pytest.MonkeyPatch) -> FakeTime:
    clock = FakeTime()
    monkeypatch.setattr("src.mqtt_client.time", clock)
    return clock


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake_client = MagicMock()
    fake_client.publish.return_value = MagicMock(rc=mqtt.MQTT_ERR_SUCCESS)
    monkeypatch.setattr("src.mqtt_client.mqtt.Client", lambda *a, **kw: fake_client)
    return fake_client


def _connected_publisher(client: MagicMock, **kwargs) -> MqttPublisher:
    publisher = MqttPublisher(host="core-mosquitto", port=1883, client_id="modqtt-test", **kwargs)
    client.connect.side_effect = lambda *a, **kw: publisher._on_connect(client, None, None, 0)
    publisher.connect_with_backoff()
    assert publisher._connected is True
    return publisher


def _reconnect_after(publisher: MqttPublisher, client: MagicMock, sleeps: int) -> None:
    """Simulate paho's loop thread completing a reconnect mid-wait."""

    def handler(clock: FakeTime) -> None:
        if len(clock.sleeps) >= sleeps:
            publisher._on_connect(client, None, None, 0)

    return handler


def test_reconnect_delay_configured_and_loop_started_once(
    client: MagicMock, fake_time: FakeTime
) -> None:
    publisher = _connected_publisher(client)
    client.reconnect_delay_set.assert_called_once_with(min_delay=1, max_delay=30)
    assert client.loop_start.call_count == 1

    publisher._on_disconnect(client, None, 7)
    assert publisher._connected is False

    fake_time.on_sleep = _reconnect_after(publisher, client, sleeps=1)
    publisher.publish_state(topic="sungrow/dc_power/state", payload="1317")

    # paho's own loop thread owns reconnection: no second loop_start, no racing reconnect()
    assert client.loop_start.call_count == 1
    assert client.reconnect.call_count == 0
    assert publisher._connected is True


def test_publish_rc_no_conn_clears_connected_then_republishes(
    client: MagicMock, fake_time: FakeTime
) -> None:
    publisher = _connected_publisher(client)

    failure = MagicMock(rc=mqtt.MQTT_ERR_NO_CONN)
    success = MagicMock(rc=mqtt.MQTT_ERR_SUCCESS)
    states: list[bool] = []

    def publish_side_effect(*_args, **_kwargs):
        states.append(publisher._connected)
        return failure if len(states) == 1 else success

    client.publish.side_effect = publish_side_effect
    fake_time.on_sleep = _reconnect_after(publisher, client, sleeps=1)

    publisher.publish_state(topic="sungrow/dc_power/state", payload="1317")

    assert client.publish.call_count == 2
    # rc=4 must have cleared the flag before the retry, forcing a reconnect wait
    assert fake_time.sleeps, "expected a reconnect wait after rc=4"
    assert states == [True, True]
    assert publisher._connected is True


def test_publish_gives_up_bounded_while_broker_stays_down(
    client: MagicMock, fake_time: FakeTime
) -> None:
    publisher = _connected_publisher(client)
    started_at = fake_time.now
    client.publish.return_value = MagicMock(rc=mqtt.MQTT_ERR_NO_CONN)

    with pytest.raises(RuntimeError, match="rc=4"):
        publisher.publish_state(topic="sungrow/dc_power/state", payload="1317")

    assert publisher._connected is False
    assert client.publish.call_count <= _PUBLISH_MAX_ATTEMPTS
    # The poll loop must not stall on one sensor while Mosquitto is down.
    assert fake_time.now - started_at < 5.0
    assert client.reconnect.call_count == 0


def test_publish_before_first_connect_does_not_block(
    client: MagicMock, fake_time: FakeTime
) -> None:
    publisher = MqttPublisher(host="core-mosquitto", port=1883, client_id="modqtt-test")
    client.connect.side_effect = OSError("connection refused")
    started_at = fake_time.now

    with pytest.raises(RuntimeError, match="rc=4"):
        publisher.publish_state(topic="sungrow/dc_power/state", payload="1317")

    assert client.publish.call_count == 0
    assert fake_time.now - started_at < 10.0


def test_stale_disconnect_triggers_manual_reconnect_fallback(
    client: MagicMock, fake_time: FakeTime
) -> None:
    publisher = _connected_publisher(client)
    publisher._on_disconnect(client, None, 7)
    fake_time.now += _STALE_RECONNECT_SECONDS + 1
    client.publish.return_value = MagicMock(rc=mqtt.MQTT_ERR_NO_CONN)

    with pytest.raises(RuntimeError):
        publisher.publish_state(topic="sungrow/dc_power/state", payload="1317")

    assert client.reconnect.call_count == 1


def test_availability_uses_will_and_republishes_online_on_reconnect(
    client: MagicMock, fake_time: FakeTime
) -> None:
    publisher = _connected_publisher(client, availability_topic="bridge/availability")
    client.will_set.assert_called_once_with(
        "bridge/availability", payload="offline", qos=0, retain=True
    )
    client.publish.assert_called_once_with(
        topic="bridge/availability", payload="online", qos=0, retain=True
    )

    publisher._on_disconnect(client, None, 7)
    publisher._on_connect(client, None, None, 0)

    assert client.publish.call_count == 2


def test_on_disconnect_handles_paho_v2_and_v1_signatures(
    client: MagicMock, fake_time: FakeTime
) -> None:
    publisher = _connected_publisher(client)

    # Exactly how paho 2.x invokes a VERSION2 callback.
    publisher._on_disconnect(
        client,
        None,
        mqtt.DisconnectFlags(is_disconnect_packet_from_server=True),
        mqtt.ReasonCode(mqtt.DISCONNECT >> 4, "Normal disconnection"),
        mqtt.Properties(mqtt.PacketTypes.DISCONNECT),
    )
    assert publisher._connected is False

    publisher._on_connect(client, None, None, 0)
    assert publisher._connected is True

    # paho 1.x style: single rc argument.
    publisher._on_disconnect(client, None, mqtt.MQTT_ERR_CONN_LOST)
    assert publisher._connected is False


def test_callbacks_match_real_paho_dispatch() -> None:
    """Regression: paho 2.x calls on_disconnect with 5 positional args.

    The pre-fix signature accepted 4, so paho swallowed a TypeError, `_connected`
    stayed True after a broker restart and nothing ever reconnected. This test
    drives paho's real internal dispatch instead of calling the callback directly.
    """
    publisher = MqttPublisher(host="core-mosquitto", port=1883, client_id="modqtt-test")
    publisher._connected = True
    publisher._client.suppress_exceptions = True

    publisher._client._do_on_disconnect(packet_from_broker=False, v1_rc=mqtt.MQTT_ERR_CONN_LOST)
    assert publisher._connected is False, "paho could not invoke on_disconnect"

    # on_connect is dispatched as (client, userdata, connect_flags, reason, properties).
    inspect.signature(publisher._on_connect).bind(
        publisher._client,
        None,
        mqtt.ConnectFlags(session_present=False),
        mqtt.ReasonCode(mqtt.CONNACK >> 4),
        mqtt.Properties(mqtt.PacketTypes.CONNACK),
    )


def test_connect_rejection_keeps_disconnected(client: MagicMock, fake_time: FakeTime) -> None:
    publisher = MqttPublisher(host="core-mosquitto", port=1883, client_id="modqtt-test")
    publisher._on_connect(client, None, None, 5)
    assert publisher._connected is False


def test_disconnect_stops_loop_and_is_idempotent(client: MagicMock, fake_time: FakeTime) -> None:
    publisher = _connected_publisher(client)
    publisher.disconnect()
    publisher.disconnect()

    assert client.loop_stop.call_count == 1
    assert client.disconnect.call_count == 1
    assert publisher._connected is False
