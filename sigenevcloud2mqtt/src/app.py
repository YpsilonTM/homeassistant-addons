from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import signal
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.cloud import EnergyInfo, SigenCloudClient
from src.config import Settings, load_settings
from src.mqtt_bridge import PHASE_MODE_SET_OPTIONS, BridgeState, MqttBridge

LOGGER = logging.getLogger(__name__)

CLOUD_OFFLINE_AFTER_FAILURES = 3
LIFETIME_RESET_AGREE_READS = 3
LIFETIME_RESET_TOLERANCE_KWH = 0.05
STARTUP_BACKOFF_MAX_SECONDS = 60.0


class CommandKind(str, Enum):
    SET_CURRENT = "set_current"
    SET_PHASE_MODE = "set_phase_mode"
    START = "start"
    STOP = "stop"


COMMAND_EXEC_ORDER = (
    CommandKind.SET_PHASE_MODE,
    CommandKind.SET_CURRENT,
    CommandKind.START,
    CommandKind.STOP,
)


@dataclass(frozen=True)
class BridgeCommand:
    kind: CommandKind
    value: str | float | None = None


def _load_dotenv_if_present() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        LOGGER.debug("python-dotenv not installed; skipping .env load")
        return
    load_dotenv(env_path)
    LOGGER.info("loaded_env path=%s", env_path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sigenergy EV cloud → MQTT bridge")
    parser.add_argument(
        "--cloud-check",
        action="store_true",
        help="Login, fetch snapshot, exit (no MQTT)",
    )
    parser.add_argument(
        "--set-current",
        type=int,
        help="With --cloud-check: set current (amps) then read back",
    )
    parser.add_argument(
        "--set-phase-mode",
        choices=list(PHASE_MODE_SET_OPTIONS),
        help="With --cloud-check: set phase mode then exit",
    )
    return parser.parse_args()


def _clamp_amps(requested: float, settings: Settings, cloud_max: float | None) -> int:
    amps = int(math.floor(requested))
    upper = settings.max_current
    if cloud_max is not None:
        upper = min(upper, int(math.floor(cloud_max)))
    return max(settings.min_current, min(upper, amps))


def _state_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / "state.json"


def _load_persisted_lifetime(state_dir: str | Path) -> float | None:
    path = _state_path(state_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = payload.get("lifetime_energy_kwh")
        if value is None:
            return None
        return float(value)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.warning("lifetime_state_load_failed path=%s error=%s", path, exc)
        return None


def _save_persisted_lifetime(state_dir: str | Path, lifetime_kwh: float) -> None:
    path = _state_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"lifetime_energy_kwh": lifetime_kwh}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        LOGGER.warning("lifetime_state_save_failed path=%s error=%s", path, exc)


def _parse_lifetime_kwh(
    energy: EnergyInfo | None,
    last_known: float | None,
    pending_lower: list[float],
) -> float | None:
    """Return a publishable cloud total, or None so HA marks the sensor unavailable.

    ``pending_lower`` tracks consecutive lower readings. After
    ``LIFETIME_RESET_AGREE_READS`` readings that agree within
    ``LIFETIME_RESET_TOLERANCE_KWH``, a genuine counter reset is accepted.
    """
    if energy is None:
        LOGGER.debug("lifetime_energy_unavailable reason=api_failed")
        return None

    value = round(energy.lifetime_kwh, 2)
    if value <= 0:
        LOGGER.warning("lifetime_energy_unavailable reason=invalid_value value=%s", value)
        return None

    if last_known is not None and value + 0.01 < last_known:
        pending_lower.append(value)
        if len(pending_lower) >= LIFETIME_RESET_AGREE_READS:
            recent = pending_lower[-LIFETIME_RESET_AGREE_READS:]
            if max(recent) - min(recent) <= LIFETIME_RESET_TOLERANCE_KWH:
                LOGGER.warning(
                    "lifetime_energy_reset_accepted new=%s last_known=%s reads=%s",
                    value,
                    last_known,
                    recent,
                )
                pending_lower.clear()
                return value
        LOGGER.warning(
            "lifetime_energy_unavailable reason=regressed new=%s last_known=%s pending=%s",
            value,
            last_known,
            len(pending_lower),
        )
        return None

    pending_lower.clear()
    return value


def _snapshot_to_bridge_state(
    snapshot,
    last_lifetime_kwh: float | None = None,
    pending_lower: list[float] | None = None,
) -> BridgeState:
    rt = snapshot.realtime
    lower = pending_lower if pending_lower is not None else []
    lifetime_kwh = _parse_lifetime_kwh(snapshot.energy, last_lifetime_kwh, lower)
    return BridgeState(
        status=snapshot.status_label,
        power_w=round((rt.power_kw * 1000) if rt else 0.0, 1),
        current_a=round(rt.current_a if rt else 0.0, 2),
        voltage_v=round(rt.voltage_v if rt else 0.0, 1),
        active_phases=rt.active_phases if rt else 0,
        lifetime_energy_kwh=lifetime_kwh,
        setpoint_a=snapshot.current.last_set_current,
        max_current_a=snapshot.current.max_current,
        phase_mode=snapshot.phase_mode,
    )


async def cloud_check(
    settings: Settings,
    set_current: int | None,
    set_phase_mode: str | None,
) -> int:
    async with SigenCloudClient(settings.username, settings.password, settings.region) as client:
        await client.login()
        station = await client.fetch_station_info()
        if set_phase_mode:
            await client.set_phase_mode(set_phase_mode)
        if set_current is not None:
            info = await client.get_ac_ev_current()
            amps = _clamp_amps(set_current, settings, info.max_current)
            await client.set_ac_ev_current(amps)
        snapshot = await client.get_snapshot()
        LOGGER.info(
            "cloud_check station_id=%s ac_sn=%s has_ac=%s model=%s fw=%s",
            client.station_id,
            client.ac_sn,
            station.get("hasAcCharger"),
            snapshot.device_model,
            snapshot.firmware_version,
        )
        LOGGER.info(
            "cloud_check status=%s (%s) setpoint=%sA max=%sA dlm=%s",
            snapshot.status_label,
            snapshot.status_code,
            snapshot.current.last_set_current,
            snapshot.current.max_current,
            snapshot.current.dlm_status,
        )
        if snapshot.realtime:
            rt = snapshot.realtime
            LOGGER.info(
                "cloud_check realtime power=%skW current=%sA voltage=%sV active_phases=%s",
                rt.power_kw,
                rt.current_a,
                rt.voltage_v,
                rt.active_phases,
            )
        if snapshot.energy:
            LOGGER.info(
                "cloud_check energy lifetime=%skWh weekly=%skWh monthly=%skWh",
                snapshot.energy.lifetime_kwh,
                snapshot.energy.weekly_kwh,
                snapshot.energy.monthly_kwh,
            )
        LOGGER.info("cloud_check phase_mode=%s", snapshot.phase_mode)
        if snapshot.more_setting:
            ms = snapshot.more_setting
            LOGGER.info(
                "cloud_check more_setting phaseType=%s phaseAutoSwitch=%s dlmStatus=%s meterPhase=%s",
                ms.phase_type,
                ms.phase_auto_switch,
                ms.dlm_status,
                ms.meter_phase,
            )
    return 0


async def _execute_command(
    client: SigenCloudClient,
    settings: Settings,
    command: BridgeCommand,
) -> None:
    if command.kind == CommandKind.SET_CURRENT:
        info = await client.get_ac_ev_current()
        amps = _clamp_amps(float(command.value), settings, info.max_current)
        await client.set_ac_ev_current(amps)
    elif command.kind == CommandKind.SET_PHASE_MODE:
        mode = str(command.value).lower()
        await client.set_phase_mode(mode)
    elif command.kind == CommandKind.START:
        await client.start_charging()
    elif command.kind == CommandKind.STOP:
        await client.stop_charging()


def parse_command_payload(value: str) -> BridgeCommand | None:
    """Validate START/STOP command payloads; return None for anything else."""
    normalized = value.strip().upper()
    if normalized == "START":
        return BridgeCommand(CommandKind.START)
    if normalized == "STOP":
        return BridgeCommand(CommandKind.STOP)
    return None


def _drain_commands(command_queue: asyncio.Queue[BridgeCommand]) -> dict[CommandKind, BridgeCommand]:
    """Drain queue into a last-wins map; START/STOP share a single slot."""
    pending: dict[CommandKind, BridgeCommand] = {}
    while True:
        try:
            command = command_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if command.kind == CommandKind.SET_PHASE_MODE:
            mode = str(command.value).lower()
            if mode not in PHASE_MODE_SET_OPTIONS:
                LOGGER.warning("invalid_phase_mode payload=%r", command.value)
                continue
            command = BridgeCommand(CommandKind.SET_PHASE_MODE, mode)
        if command.kind in (CommandKind.START, CommandKind.STOP):
            pending.pop(CommandKind.START, None)
            pending.pop(CommandKind.STOP, None)
        pending[command.kind] = command
    return pending


async def _cloud_login_with_backoff(
    client: SigenCloudClient,
    stop_event: asyncio.Event,
) -> bool:
    """Login and fetch station info with exponential backoff. Returns False if stopped."""
    delay = 1.0
    while not stop_event.is_set():
        try:
            await client.login()
            await client.fetch_station_info()
            return True
        except Exception:
            LOGGER.exception("cloud_startup_failed; retrying in %.1fs", delay)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                return False
            except asyncio.TimeoutError:
                delay = min(delay * 2, STARTUP_BACKOFF_MAX_SECONDS)
    return False


async def run_bridge(settings: Settings) -> int:
    stop_event = asyncio.Event()
    thread_stop = threading.Event()
    loop = asyncio.get_running_loop()
    command_queue: asyncio.Queue[BridgeCommand] = asyncio.Queue()

    def _request_stop() -> None:
        stop_event.set()
        thread_stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    mqtt = MqttBridge(
        host=settings.mqtt_host,
        port=settings.mqtt_port,
        client_id=settings.mqtt_client_id,
        username=settings.mqtt_username,
        password=settings.mqtt_password,
        topic_prefix=settings.topic_prefix,
        device_name=settings.device_name,
        device_id="sigen_evac",
        min_current=settings.min_current,
        max_current=settings.max_current,
    )

    def enqueue(command: BridgeCommand) -> None:
        loop.call_soon_threadsafe(command_queue.put_nowait, command)

    def on_command_payload(value: str) -> None:
        command = parse_command_payload(value)
        if command is None:
            LOGGER.warning("invalid_command payload=%r", value)
            return
        enqueue(command)

    mqtt.set_current_handler(lambda value: enqueue(BridgeCommand(CommandKind.SET_CURRENT, value)))
    mqtt.set_phase_mode_handler(
        lambda value: enqueue(BridgeCommand(CommandKind.SET_PHASE_MODE, value.strip().lower()))
    )
    mqtt.set_command_handler(on_command_payload)

    last_state: BridgeState | None = None
    last_lifetime_kwh: float | None = _load_persisted_lifetime(settings.state_dir)
    pending_lower: list[float] = []
    consecutive_poll_failures = 0
    cloud_marked_offline = False
    poll_cycle = 0

    def _reset_last_state() -> None:
        nonlocal last_state
        last_state = None

    def on_mqtt_reconnect() -> None:
        # Handler runs on the paho thread; bounce onto the asyncio loop.
        loop.call_soon_threadsafe(_reset_last_state)

    mqtt.set_reconnect_handler(on_mqtt_reconnect)

    # MQTT first so a cloud outage does not block broker connect / LWT setup.
    await asyncio.to_thread(mqtt.connect_with_backoff, thread_stop)
    if stop_event.is_set():
        mqtt.disconnect()
        LOGGER.info("bridge_stopped")
        return 0

    async with SigenCloudClient(settings.username, settings.password, settings.region) as client:
        if not await _cloud_login_with_backoff(client, stop_event):
            mqtt.disconnect()
            LOGGER.info("bridge_stopped")
            return 0

        if client.ac_sn:
            mqtt.device_id = f"sigen_evac_{client.ac_sn}"

        mqtt.publish_discovery()
        mqtt.publish_lifetime_energy(None)

        LOGGER.info(
            "bridge_started poll=%ss slow_poll_x=%s mqtt=%s:%s prefix=%s device_id=%s",
            settings.poll_interval_seconds,
            settings.slow_poll_multiplier,
            settings.mqtt_host,
            settings.mqtt_port,
            settings.topic_prefix,
            mqtt.device_id,
        )

        while not stop_event.is_set():
            pending = _drain_commands(command_queue)
            if pending:
                try:
                    for kind in COMMAND_EXEC_ORDER:
                        command = pending.get(kind)
                        if command is None:
                            continue
                        await _execute_command(client, settings, command)
                    snapshot = await client.get_snapshot(include_slow=True)
                    mqtt.update_device_info(snapshot.device_model, snapshot.firmware_version)
                    state = _snapshot_to_bridge_state(snapshot, last_lifetime_kwh, pending_lower)
                    if state.lifetime_energy_kwh is not None:
                        if state.lifetime_energy_kwh != last_lifetime_kwh:
                            _save_persisted_lifetime(settings.state_dir, state.lifetime_energy_kwh)
                        last_lifetime_kwh = state.lifetime_energy_kwh
                    mqtt.publish_state_bundle(state)
                    last_state = state
                    consecutive_poll_failures = 0
                    if cloud_marked_offline:
                        mqtt.publish_availability(True)
                        cloud_marked_offline = False
                except Exception:
                    LOGGER.exception("command_failed kinds=%s", [k.value for k in pending])

            try:
                include_slow = poll_cycle % settings.slow_poll_multiplier == 0
                snapshot = await client.get_snapshot(include_slow=include_slow)
                mqtt.update_device_info(snapshot.device_model, snapshot.firmware_version)
                state = _snapshot_to_bridge_state(snapshot, last_lifetime_kwh, pending_lower)
                if state.lifetime_energy_kwh is not None:
                    if state.lifetime_energy_kwh != last_lifetime_kwh:
                        _save_persisted_lifetime(settings.state_dir, state.lifetime_energy_kwh)
                    last_lifetime_kwh = state.lifetime_energy_kwh
                if last_state != state:
                    mqtt.publish_state_bundle(state)
                    last_state = state
                consecutive_poll_failures = 0
                if cloud_marked_offline:
                    mqtt.publish_availability(True)
                    cloud_marked_offline = False
            except Exception:
                LOGGER.exception("poll_failed")
                consecutive_poll_failures += 1
                if (
                    consecutive_poll_failures >= CLOUD_OFFLINE_AFTER_FAILURES
                    and not cloud_marked_offline
                ):
                    try:
                        mqtt.publish_availability(False)
                        cloud_marked_offline = True
                    except Exception:
                        LOGGER.exception("availability_offline_publish_failed")

            poll_cycle += 1

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=settings.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    mqtt.disconnect()
    LOGGER.info("bridge_stopped")
    return 0


async def async_main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _load_dotenv_if_present()
    settings = load_settings()
    logging.getLogger().setLevel(getattr(logging, settings.log_level, logging.INFO))
    if args.cloud_check:
        return await cloud_check(settings, args.set_current, args.set_phase_mode)
    return await run_bridge(settings)


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
