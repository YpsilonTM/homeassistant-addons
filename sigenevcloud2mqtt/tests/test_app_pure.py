"""Pure-function tests for app helpers (no network)."""

from __future__ import annotations

import asyncio

import pytest
from src.app import (
    COMMAND_EXEC_ORDER,
    BridgeCommand,
    CommandKind,
    _clamp_amps,
    _drain_commands,
    _parse_lifetime_kwh,
    _snapshot_to_bridge_state,
    parse_command_payload,
)
from src.cloud import CurrentInfo, EnergyInfo, EvacSnapshot, RealtimeInfo
from src.config import Settings
from src.mqtt_bridge import BridgeState


def _settings(**overrides) -> Settings:
    base = dict(
        username="u",
        password="p",
        region="eu",
        poll_interval_seconds=15.0,
        min_current=6,
        max_current=16,
        mqtt_host="localhost",
        mqtt_port=1883,
        mqtt_username="",
        mqtt_password="",
        mqtt_client_id="test",
        topic_prefix="sigen_evac",
        device_name="Sigen EV AC",
        log_level="INFO",
        slow_poll_multiplier=4,
        state_dir="/tmp",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.mark.parametrize(
    ("requested", "cloud_max", "expected"),
    [
        (10.9, None, 10),
        (5.0, None, 6),
        (20.0, None, 16),
        (20.0, 12.0, 12),
        (8.0, 32.0, 8),
    ],
)
def test_clamp_amps(requested: float, cloud_max: float | None, expected: int) -> None:
    assert _clamp_amps(requested, _settings(), cloud_max) == expected


def test_parse_lifetime_kwh_basic() -> None:
    pending: list[float] = []
    assert _parse_lifetime_kwh(None, None, pending) is None
    assert _parse_lifetime_kwh(EnergyInfo(0.0, 0.0, 0.0), None, pending) is None
    assert _parse_lifetime_kwh(EnergyInfo(12.345, 1.0, 2.0), None, pending) == 12.35


def test_parse_lifetime_kwh_rejects_regression() -> None:
    pending: list[float] = []
    assert _parse_lifetime_kwh(EnergyInfo(10.0, 0.0, 0.0), 20.0, pending) is None
    assert pending == [10.0]


def test_parse_lifetime_kwh_reset_escape_hatch() -> None:
    pending: list[float] = []
    last = 100.0
    assert _parse_lifetime_kwh(EnergyInfo(5.01, 0.0, 0.0), last, pending) is None
    assert _parse_lifetime_kwh(EnergyInfo(5.02, 0.0, 0.0), last, pending) is None
    accepted = _parse_lifetime_kwh(EnergyInfo(5.03, 0.0, 0.0), last, pending)
    assert accepted == 5.03
    assert pending == []


def test_parse_lifetime_kwh_reset_requires_agreement() -> None:
    pending: list[float] = []
    last = 100.0
    assert _parse_lifetime_kwh(EnergyInfo(5.0, 0.0, 0.0), last, pending) is None
    assert _parse_lifetime_kwh(EnergyInfo(5.0, 0.0, 0.0), last, pending) is None
    # Spread > 0.05 kWh — do not accept yet
    assert _parse_lifetime_kwh(EnergyInfo(5.2, 0.0, 0.0), last, pending) is None
    assert len(pending) == 3


def test_parse_lifetime_kwh_clears_pending_on_normal() -> None:
    pending = [1.0]
    assert _parse_lifetime_kwh(EnergyInfo(25.0, 0.0, 0.0), 20.0, pending) == 25.0
    assert pending == []


def test_snapshot_to_bridge_state() -> None:
    snapshot = EvacSnapshot(
        status_code=3,
        status_label="charging",
        current=CurrentInfo(last_set_current=10.0, max_current=16.0),
        realtime=RealtimeInfo(
            power_kw=2.3,
            current_a=10.0,
            voltage_v=230.0,
            single_phase_power_kw=2.3,
            active_phases=1,
        ),
        energy=EnergyInfo(lifetime_kwh=42.5, weekly_kwh=1.0, monthly_kwh=2.0),
        phase_mode="1_phase",
    )
    state = _snapshot_to_bridge_state(snapshot)
    assert state == BridgeState(
        status="charging",
        power_w=2300.0,
        current_a=10.0,
        voltage_v=230.0,
        active_phases=1,
        lifetime_energy_kwh=42.5,
        setpoint_a=10.0,
        max_current_a=16.0,
        phase_mode="1_phase",
    )


def test_snapshot_to_bridge_state_without_realtime() -> None:
    snapshot = EvacSnapshot(
        status_code=1,
        status_label="idle",
        current=CurrentInfo(last_set_current=6.0, max_current=16.0),
        realtime=None,
        energy=None,
        phase_mode="unknown",
    )
    state = _snapshot_to_bridge_state(snapshot)
    assert state.status == "idle"
    assert state.power_w == 0.0
    assert state.active_phases == 0
    assert state.lifetime_energy_kwh is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("START", CommandKind.START),
        (" stop ", CommandKind.STOP),
        ("Start", CommandKind.START),
        ("STOP\n", CommandKind.STOP),
        ("PAUSE", None),
        ("", None),
        ("start charging", None),
    ],
)
def test_parse_command_payload(payload: str, expected: CommandKind | None) -> None:
    result = parse_command_payload(payload)
    if expected is None:
        assert result is None
    else:
        assert result == BridgeCommand(expected)


def test_drain_commands_coalesces_last_wins() -> None:
    queue: asyncio.Queue[BridgeCommand] = asyncio.Queue()
    for command in (
        BridgeCommand(CommandKind.SET_CURRENT, "8"),
        BridgeCommand(CommandKind.SET_PHASE_MODE, "1_phase"),
        BridgeCommand(CommandKind.SET_CURRENT, "12"),
        BridgeCommand(CommandKind.START),
        BridgeCommand(CommandKind.STOP),
        BridgeCommand(CommandKind.SET_PHASE_MODE, "auto"),
    ):
        queue.put_nowait(command)

    pending = _drain_commands(queue)
    assert set(pending) == {
        CommandKind.SET_CURRENT,
        CommandKind.SET_PHASE_MODE,
        CommandKind.STOP,
    }
    assert pending[CommandKind.SET_CURRENT].value == "12"
    assert pending[CommandKind.SET_PHASE_MODE].value == "auto"
    assert CommandKind.START not in pending

    # Fixed execution order: phase, current, start, stop
    ordered = [kind for kind in COMMAND_EXEC_ORDER if kind in pending]
    assert ordered == [
        CommandKind.SET_PHASE_MODE,
        CommandKind.SET_CURRENT,
        CommandKind.STOP,
    ]


def test_drain_commands_drops_invalid_phase_mode() -> None:
    queue: asyncio.Queue[BridgeCommand] = asyncio.Queue()
    queue.put_nowait(BridgeCommand(CommandKind.SET_PHASE_MODE, "bogus"))
    queue.put_nowait(BridgeCommand(CommandKind.SET_PHASE_MODE, "3_phase"))
    pending = _drain_commands(queue)
    assert pending[CommandKind.SET_PHASE_MODE].value == "3_phase"
