from __future__ import annotations

from src.catalog import load_catalog
from src.modbus_client import ModbusReader


def test_batch_uses_wire_addresses() -> None:
    registers, _ = load_catalog()
    wanted = [
        next(reg for reg in registers if reg.slug == "mppt1_voltage"),
        next(reg for reg in registers if reg.slug == "mppt1_current"),
        next(reg for reg in registers if reg.slug == "mppt2_voltage"),
        next(reg for reg in registers if reg.slug == "mppt2_current"),
    ]
    reader = ModbusReader("127.0.0.1", 502, 1, 3.0)
    batches = reader._build_batches(wanted)
    assert len(batches) == 1
    assert batches[0].start == 5010  # doc 5011 - 1
    assert batches[0].count == 4
