from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from pymodbus.client import ModbusTcpClient

from src.catalog import Register
from src.decoder import encode_register

LOGGER = logging.getLogger(__name__)

MAX_BATCH_WORDS = 125
MIN_WRITE_INTERVAL_SECONDS = 1.0


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

    def __post_init__(self) -> None:
        self._client = ModbusTcpClient(
            host=self.host,
            port=self.port,
            timeout=self.timeout_seconds,
        )

    def connect_with_backoff(self, max_delay_seconds: float = 30.0) -> None:
        delay = 1.0
        while not self._client.connected:
            if self._client.connect():
                LOGGER.info("modbus_connected host=%s port=%s", self.host, self.port)
                return
            LOGGER.warning(
                "modbus_connect_retry host=%s port=%s next_delay=%.1f",
                self.host,
                self.port,
                delay,
            )
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

        if response.isError():
            raise RuntimeError(
                "Modbus read failed "
                f"type={register_type} wire_address={wire_address} count={count} "
                f"response={response}"
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

    def _read_batch(self, batch: _Batch) -> dict[str, list[int]]:
        values: dict[str, list[int]] = {}
        try:
            raw = self._read_registers(batch.register_type, batch.start, batch.count)
            for register in batch.registers:
                offset = register.wire_address - batch.start
                values[register.slug] = raw[offset : offset + register.length_words]
            return values
        except RuntimeError:
            if len(batch.registers) == 1:
                register = batch.registers[0]
                self.unsupported_slugs.add(register.slug)
                LOGGER.warning(
                    "modbus_register_unsupported slug=%s doc_address=%s wire=%s",
                    register.slug,
                    register.address,
                    register.wire_address,
                )
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
