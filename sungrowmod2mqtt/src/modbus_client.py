from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from pymodbus.client import ModbusTcpClient
from pymodbus.pdu import ExceptionResponse

from src.catalog import Register
from src.decoder import encode_register

LOGGER = logging.getLogger(__name__)

MAX_BATCH_WORDS = 125
MIN_WRITE_INTERVAL_SECONDS = 1.0
ILLEGAL_DATA_ADDRESS = 2
UNSUPPORTED_AFTER_FAILURES = 3


class ModbusReadError(RuntimeError):
    def __init__(self, message: str, *, illegal_address: bool = False) -> None:
        super().__init__(message)
        self.illegal_address = illegal_address


@dataclass
class _Batch:
    register_type: str
    start: int
    count: int
    registers: list[Register]


@dataclass
class ModbusReader:
    host: str
    port: int
    unit_id: int
    timeout_seconds: float
    unsupported_slugs: set[str] = field(default_factory=set)
    _last_write_monotonic: float = field(default=0.0, init=False)
    _failure_counts: dict[str, int] = field(default_factory=dict, init=False)
    _stop_event: threading.Event | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = ModbusTcpClient(
            host=self.host,
            port=self.port,
            timeout=self.timeout_seconds,
        )

    def set_stop_event(self, stop_event: threading.Event | None) -> None:
        self._stop_event = stop_event

    def connect_with_backoff(
        self,
        stop: threading.Event | None = None,
        max_delay_seconds: float = 30.0,
    ) -> None:
        delay = 1.0
        stop_event = stop if stop is not None else self._stop_event
        while not self._client.connected:
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError("modbus_connect_aborted")
            if self._client.connect():
                LOGGER.info("modbus_connected host=%s port=%s", self.host, self.port)
                return
            LOGGER.warning(
                "modbus_connect_retry host=%s port=%s next_delay=%.1f",
                self.host,
                self.port,
                delay,
            )
            if stop_event is not None:
                if stop_event.wait(delay):
                    raise RuntimeError("modbus_connect_aborted")
            else:
                time.sleep(delay)
            delay = min(delay * 2, max_delay_seconds)

    def close(self) -> None:
        self._client.close()

    def _ensure_connected(self) -> None:
        if not self._client.connected:
            self.connect_with_backoff()

    def _read_registers(self, register_type: str, wire_address: int, count: int) -> list[int]:
        self._ensure_connected()
        if register_type == "input":
            response = self._client.read_input_registers(
                address=wire_address,
                count=count,
                device_id=self.unit_id,
            )
        else:
            response = self._client.read_holding_registers(
                address=wire_address,
                count=count,
                device_id=self.unit_id,
            )

        if isinstance(response, ExceptionResponse) or response.isError():
            exception_code = getattr(response, "exception_code", None)
            illegal = exception_code == ILLEGAL_DATA_ADDRESS
            raise ModbusReadError(
                "Modbus read failed "
                f"type={register_type} wire_address={wire_address} count={count} "
                f"response={response}",
                illegal_address=illegal,
            )
        return [int(word) & 0xFFFF for word in response.registers]

    def _build_batches(self, registers: Sequence[Register]) -> list[_Batch]:
        usable = [reg for reg in registers if reg.slug not in self.unsupported_slugs]
        if not usable:
            return []

        ordered = sorted(usable, key=lambda item: (item.register_type, item.wire_address))
        batches: list[_Batch] = []
        current = _Batch(
            register_type=ordered[0].register_type,
            start=ordered[0].wire_address,
            count=ordered[0].length_words,
            registers=[ordered[0]],
        )

        for register in ordered[1:]:
            next_address = current.start + current.count
            same_type = register.register_type == current.register_type
            contiguous = register.wire_address == next_address
            next_total = (register.wire_address + register.length_words) - current.start
            fits = next_total <= MAX_BATCH_WORDS

            if same_type and contiguous and fits:
                current.registers.append(register)
                current.count = next_total
            else:
                batches.append(current)
                current = _Batch(
                    register_type=register.register_type,
                    start=register.wire_address,
                    count=register.length_words,
                    registers=[register],
                )
        batches.append(current)
        return batches

    def _mark_unsupported(self, register: Register, *, reason: str) -> None:
        self.unsupported_slugs.add(register.slug)
        self._failure_counts.pop(register.slug, None)
        LOGGER.warning(
            "modbus_register_unsupported slug=%s doc_address=%s wire=%s reason=%s",
            register.slug,
            register.address,
            register.wire_address,
            reason,
        )

    def _handle_single_failure(self, register: Register, error: ModbusReadError) -> None:
        if error.illegal_address:
            self._mark_unsupported(register, reason="illegal_address")
            return

        count = self._failure_counts.get(register.slug, 0) + 1
        self._failure_counts[register.slug] = count
        if count >= UNSUPPORTED_AFTER_FAILURES:
            self._mark_unsupported(register, reason=f"consecutive_failures={count}")
            return
        LOGGER.warning(
            "modbus_register_transient slug=%s doc_address=%s wire=%s failures=%s/%s error=%s",
            register.slug,
            register.address,
            register.wire_address,
            count,
            UNSUPPORTED_AFTER_FAILURES,
            error,
        )

    def _read_batch(self, batch: _Batch) -> dict[str, list[int]]:
        values: dict[str, list[int]] = {}
        try:
            raw = self._read_registers(batch.register_type, batch.start, batch.count)
            for register in batch.registers:
                offset = register.wire_address - batch.start
                values[register.slug] = raw[offset : offset + register.length_words]
                self._failure_counts.pop(register.slug, None)
            return values
        except ModbusReadError as error:
            if len(batch.registers) == 1:
                self._handle_single_failure(batch.registers[0], error)
                return {}
            LOGGER.warning(
                "modbus_batch_failed type=%s start=%s count=%s; retrying individually",
                batch.register_type,
                batch.start,
                batch.count,
            )
            for register in batch.registers:
                single = _Batch(
                    register_type=register.register_type,
                    start=register.wire_address,
                    count=register.length_words,
                    registers=[register],
                )
                values.update(self._read_batch(single))
            return values

    def read_many(self, registers: Sequence[Register]) -> dict[str, list[int]]:
        values: dict[str, list[int]] = {}
        for batch in self._build_batches(registers):
            values.update(self._read_batch(batch))
        return values

    def _throttle_writes(self) -> None:
        elapsed = time.monotonic() - self._last_write_monotonic
        if elapsed < MIN_WRITE_INTERVAL_SECONDS:
            time.sleep(MIN_WRITE_INTERVAL_SECONDS - elapsed)

    def write_register(self, register: Register, value: float | int | str) -> None:
        if register.register_type != "holding":
            raise ValueError(f"Cannot write non-holding register {register.slug}")
        self._ensure_connected()
        words = encode_register(register, value)
        self._throttle_writes()

        if len(words) == 1:
            response = self._client.write_register(
                address=register.wire_address,
                value=words[0],
                device_id=self.unit_id,
            )
        else:
            response = self._client.write_registers(
                address=register.wire_address,
                values=words,
                device_id=self.unit_id,
            )
        self._last_write_monotonic = time.monotonic()

        if response.isError():
            raise RuntimeError(
                f"Modbus write failed slug={register.slug} "
                f"wire={register.wire_address} value={value} response={response}"
            )
        LOGGER.info(
            "modbus_write_ok slug=%s doc_address=%s value=%s raw_words=%s",
            register.slug,
            register.address,
            value,
            words,
        )
