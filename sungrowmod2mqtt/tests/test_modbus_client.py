from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pymodbus.exceptions import ConnectionException
from pymodbus.pdu import ExceptionResponse
from src.catalog import load_catalog
from src.modbus_client import ILLEGAL_DATA_ADDRESS, UNSUPPORTED_AFTER_FAILURES, ModbusReader


def _single_register():
    registers, _ = load_catalog()
    return next(reg for reg in registers if reg.slug == "mppt1_voltage")


def test_illegal_address_marks_unsupported() -> None:
    reader = ModbusReader("127.0.0.1", 502, 1, 3.0)
    register = _single_register()
    reader._client = MagicMock()
    reader._client.connected = True
    reader._client.read_input_registers.return_value = ExceptionResponse(4, ILLEGAL_DATA_ADDRESS)

    assert reader.read_many([register]) == {}
    assert register.slug in reader.unsupported_slugs


def test_transient_errors_need_three_failures() -> None:
    reader = ModbusReader("127.0.0.1", 502, 1, 3.0)
    register = _single_register()
    reader._client = MagicMock()
    reader._client.connected = True
    # Gateway target failed to respond (0x0B) — transient
    reader._client.read_input_registers.return_value = ExceptionResponse(4, 0x0B)

    for _ in range(UNSUPPORTED_AFTER_FAILURES - 1):
        assert reader.read_many([register]) == {}
        assert register.slug not in reader.unsupported_slugs

    assert reader.read_many([register]) == {}
    assert register.slug in reader.unsupported_slugs


def test_connect_with_backoff_honours_stop() -> None:
    reader = ModbusReader("127.0.0.1", 502, 1, 0.1)
    reader._client = MagicMock()
    reader._client.connected = False
    reader._client.connect.return_value = False
    stop = threading.Event()
    stop.set()
    try:
        reader.connect_with_backoff(stop)
        raised = False
    except RuntimeError as exc:
        raised = "aborted" in str(exc)
    assert raised


def test_successful_read_clears_failure_count() -> None:
    reader = ModbusReader("127.0.0.1", 502, 1, 3.0)
    register = _single_register()
    reader._client = MagicMock()
    reader._client.connected = True
    reader._failure_counts[register.slug] = 2
    reader._client.read_input_registers.return_value = SimpleNamespace(
        isError=lambda: False,
        registers=[123],
    )
    values = reader.read_many([register])
    assert values[register.slug] == [123]
    assert register.slug not in reader._failure_counts


def test_transport_error_resets_and_retries_once() -> None:
    reader = ModbusReader("127.0.0.1", 502, 1, 3.0)
    register = _single_register()
    old_client = MagicMock()
    old_client.connected = True
    old_client.read_input_registers.side_effect = ConnectionException("broken")
    reader._client = old_client

    fresh_client = MagicMock()
    fresh_client.connected = False
    fresh_client.connect.return_value = True
    fresh_client.read_input_registers.return_value = SimpleNamespace(
        isError=lambda: False,
        registers=[321],
    )

    with patch.object(reader, "_new_client", return_value=fresh_client):
        values = reader.read_many([register])

    old_client.close.assert_called()
    fresh_client.connect.assert_called()
    assert values[register.slug] == [321]


def test_broken_pipe_resets_and_retries_once() -> None:
    reader = ModbusReader("127.0.0.1", 502, 1, 3.0)
    register = _single_register()
    old_client = MagicMock()
    old_client.connected = True
    old_client.read_input_registers.side_effect = BrokenPipeError(32, "Broken pipe")
    reader._client = old_client

    fresh_client = MagicMock()
    fresh_client.connected = False
    fresh_client.connect.return_value = True
    fresh_client.read_input_registers.return_value = SimpleNamespace(
        isError=lambda: False,
        registers=[99],
    )

    with patch.object(reader, "_new_client", return_value=fresh_client):
        values = reader.read_many([register])

    assert values[register.slug] == [99]
