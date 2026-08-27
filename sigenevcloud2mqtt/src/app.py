from __future__ import annotations

import argparse
import asyncio
import logging
import math
import signal
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.cloud import SigenCloudClient
from src.config import Settings, load_settings
from src.mqtt_bridge import BridgeState, MqttBridge, PHASE_MODE_SET_OPTIONS, PHASE_MODE_STATE_OPTIONS

LOGGER = logging.getLogger(__name__)


class CommandKind(str, Enum):
    SET_CURRENT = "set_current"
    SET_PHASE_MODE = "set_phase_mode"
    START = "start"
    STOP = "stop"


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


def _snapshot_to_bridge_state(snapshot) -> BridgeState:
    rt = snapshot.realtime
    energy = snapshot.energy
    return BridgeState(
        status=snapshot.status_label,
        power_w=round((rt.power_kw * 1000) if rt else 0.0, 1),
        current_a=round(rt.current_a if rt else 0.0, 2),
        voltage_v=round(rt.voltage_v if rt else 0.0, 1),
        active_phases=rt.active_phases if rt else 0,
        lifetime_energy_kwh=round(energy.lifetime_kwh if energy else 0.0, 2),
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


async def _handle_command(
    client: SigenCloudClient,
    settings: Settings,
    command: BridgeCommand,
) -> BridgeState:
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

    snapshot = await client.get_snapshot()
    return _snapshot_to_bridge_state(snapshot)


async def run_bridge(settings: Settings) -> int:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    command_queue: asyncio.Queue[BridgeCommand] = asyncio.Queue()

    def _request_stop() -> None:
        stop_event.set()

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

    mqtt.set_current_handler(lambda value: enqueue(BridgeCommand(CommandKind.SET_CURRENT, value)))
    mqtt.set_phase_mode_handler(
        lambda value: enqueue(BridgeCommand(CommandKind.SET_PHASE_MODE, value.strip().lower()))
    )
    mqtt.set_command_handler(
        lambda value: enqueue(
            BridgeCommand(
                CommandKind.START if value.strip().upper() == "START" else CommandKind.STOP
            )
        )
    )

    last_state: BridgeState | None = None

    async with SigenCloudClient(settings.username, settings.password, settings.region) as client:
        await client.login()
        await client.fetch_station_info()
        if client.ac_sn:
            mqtt.device_id = f"sigen_evac_{client.ac_sn}"

        await asyncio.to_thread(mqtt.connect_with_backoff)
        mqtt.publish_discovery()

        LOGGER.info(
            "bridge_started poll=%ss mqtt=%s:%s prefix=%s device_id=%s",
            settings.poll_interval_seconds,
            settings.mqtt_host,
            settings.mqtt_port,
            settings.topic_prefix,
            mqtt.device_id,
        )

        while not stop_event.is_set():
            try:
                while True:
                    command = command_queue.get_nowait()
                    if command.kind == CommandKind.SET_PHASE_MODE:
                        mode = str(command.value).lower()
                        if mode not in PHASE_MODE_SET_OPTIONS:
                            LOGGER.warning("invalid_phase_mode payload=%r", command.value)
                            continue
                    try:
                        state = await _handle_command(client, settings, command)
                        mqtt.publish_state_bundle(state)
                        last_state = state
                    except Exception:
                        LOGGER.exception("command_failed kind=%s", command.kind.value)
            except asyncio.QueueEmpty:
                pass

            try:
                snapshot = await client.get_snapshot()
                state = _snapshot_to_bridge_state(snapshot)
                if last_state != state:
                    mqtt.publish_state_bundle(state)
                    last_state = state
            except Exception:
                LOGGER.exception("poll_failed")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=settings.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    mqtt.disconnect()
    LOGGER.info("bridge_stopped")
    return 0


async def async_main() -> int:
    args = _parse_args()
    _load_dotenv_if_present()
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.cloud_check:
        return await cloud_check(settings, args.set_current, args.set_phase_mode)
    return await run_bridge(settings)


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
