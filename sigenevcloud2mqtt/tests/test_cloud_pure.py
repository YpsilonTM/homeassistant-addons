"""Pure-function tests for cloud helpers (no network)."""

from __future__ import annotations

import pytest
from src.cloud import MoreSettingInfo, SigenCloudClient, encrypt_password


def test_encrypt_password_known_vector() -> None:
    assert encrypt_password("testpassword") == "NIWsDc+goGjF6cZx/lhWeg=="


@pytest.mark.parametrize(
    ("phase_type", "phase_auto_switch", "expected"),
    [
        (0, 1, "auto"),
        (1, 1, "auto"),
        (2, 1, "auto"),
        (1, 0, "3_phase"),
        (0, 0, "1_phase"),
        (2, 0, "1_phase"),
        (9, 0, "unknown"),
    ],
)
def test_decode_phase_mode(phase_type: int, phase_auto_switch: int, expected: str) -> None:
    assert MoreSettingInfo.decode_phase_mode(phase_type, phase_auto_switch) == expected


@pytest.mark.parametrize(
    ("power_kw", "current_a", "voltage_v", "expected"),
    [
        (0.0, 10.0, 230.0, 0),
        (0.04, 10.0, 230.0, 0),
        (2.3, 10.0, 230.0, 1),
        (6.9, 10.0, 230.0, 3),
        (4.6, 10.0, 230.0, 2),
        (2.3, 0.05, 230.0, 0),
        (2.3, 10.0, 0.0, 0),
    ],
)
def test_infer_active_phases(
    power_kw: float,
    current_a: float,
    voltage_v: float,
    expected: int,
) -> None:
    assert SigenCloudClient._infer_active_phases(power_kw, current_a, voltage_v) == expected
