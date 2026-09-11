from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from dataclasses import dataclass
from enum import IntEnum

import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

LOGGER = logging.getLogger(__name__)

REGION_BASE_URLS = {
    "eu": "https://api-eu.sigencloud.com/",
    "cn": "https://api-cn.sigencloud.com/",
    "apac": "https://api-apac.sigencloud.com/",
    "us": "https://api-us.sigencloud.com/",
}

CLIENT_SERVER_BY_REGION = {
    "eu": "eu",
    "cn": "cn",
    "apac": "aus",
    "us": "us",
}

WEB_APP_VERSION = "3.5.2"
WEB_APP_BUILD = "1"
WEB_APP_PACKAGE = "sigen_app"
WEB_APP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) "
    "Gecko/20100101 Firefox/152.0"
)

TOKEN_REFRESH_SKEW_SECONDS = 60
HTTP_MAX_ATTEMPTS = 3
HTTP_BACKOFF_BASE_SECONDS = 1.0


def _redact_secrets(payload: object) -> object:
    """Return a copy of payload with access_token/refresh_token redacted for logs."""
    if isinstance(payload, dict):
        redacted: dict = {}
        for key, value in payload.items():
            if key in {"access_token", "refresh_token"} and value is not None:
                redacted[key] = "***"
            else:
                redacted[key] = _redact_secrets(value)
        return redacted
    if isinstance(payload, list):
        return [_redact_secrets(item) for item in payload]
    return payload


class ChargeStatus(IntEnum):
    IDLE = 1
    PREPARING = 2
    CHARGING = 3


CHARGE_STATUS_LABELS = {
    ChargeStatus.IDLE: "idle",
    ChargeStatus.PREPARING: "preparing",
    ChargeStatus.CHARGING: "charging",
}

# Cloud write `outputMode` values (0-based; matches read `phaseType`).
PHASE_MODE_SINGLE = 0
PHASE_MODE_THREE = 1

PHASE_MODE_SETTLE_SECONDS = 0.4
PHASE_MODE_RETRY_DELAY_SECONDS = 1.5


def encrypt_password(password: str) -> str:
    key = b"sigensigensigenp"
    iv = b"sigensigensigenp"
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(password.encode("utf-8"), AES.block_size))
    return base64.b64encode(encrypted).decode("utf-8")


@dataclass(frozen=True)
class CurrentInfo:
    last_set_current: float
    max_current: float
    dlm_status: int | None = None


@dataclass(frozen=True)
class RealtimeInfo:
    power_kw: float
    current_a: float
    voltage_v: float
    single_phase_power_kw: float
    active_phases: int
    internal_temperature_c: float | None = None
    charging_start_time: str | None = None


@dataclass(frozen=True)
class EnergyInfo:
    lifetime_kwh: float
    weekly_kwh: float
    monthly_kwh: float


@dataclass(frozen=True)
class MoreSettingInfo:
    phase_type: int
    phase_auto_switch: int
    dlm_status: int
    meter_phase: int
    charging_output_current: float
    home_circuit_breaker: int | None = None
    pile_circuit_breaker: float | None = None
    off_grid_charge: int | None = None
    is_support_three_phase: bool | None = None

    @staticmethod
    def decode_phase_mode(phase_type: int, phase_auto_switch: int) -> str:
        """Map cloud advanced-setting fields to HA phase_mode values."""
        if phase_auto_switch == 1:
            return "auto"
        # App read API uses phaseType 0/1; writes use outputMode 1/2.
        if phase_type == 1:
            return "3_phase"
        if phase_type in {0, 2}:
            return "1_phase"
        return "unknown"


@dataclass(frozen=True)
class EvacSnapshot:
    status_code: int
    status_label: str
    current: CurrentInfo
    realtime: RealtimeInfo | None
    energy: EnergyInfo | None
    phase_mode: str = "unknown"
    more_setting: MoreSettingInfo | None = None
    device_model: str | None = None
    firmware_version: str | None = None


class SigenCloudClient:
    """Thin Sigenergy cloud client for standalone AC EVSE (EVAC)."""

    def __init__(
        self,
        username: str,
        password: str,
        region: str = "eu",
        timeout_seconds: float = 30.0,
    ) -> None:
        if region not in REGION_BASE_URLS:
            raise ValueError(f"Unsupported region '{region}'")
        self.username = username
        self._password_encrypted = encrypt_password(password)
        self.region = region
        self.base_url = REGION_BASE_URLS[region]
        self.timeout_seconds = timeout_seconds
        self.user_device_id = str(int(time.time() * 1000))
        self.session_id = str(uuid.uuid4())
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.token_expiry: float = 0.0
        self.station_id: int | str | None = None
        self.ac_sn: str | None = None
        self._session: aiohttp.ClientSession | None = None
        self._cached_device_param: dict | None = None
        self._cached_energy: EnergyInfo | None = None
        self._cached_more_setting: MoreSettingInfo | None = None

    async def __aenter__(self) -> SigenCloudClient:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _session_or_raise(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("Client session is not open; use async with")
        return self._session

    def _web_app_origin(self) -> str:
        return self.base_url.rstrip("/").replace("https://api-", "https://app-", 1)

    def _sigen_timestamp(self) -> str:
        # Sigenergy expects epoch milliseconds multiplied by 1000 (not microseconds).
        return str(int(time.time() * 1000) * 1000)

    def _request_headers(
        self,
        content_type: str,
        path: str,
        access_token: str | None = None,
    ) -> dict[str, str]:
        origin = self._web_app_origin()
        timestamp = self._sigen_timestamp()
        headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;",
            "User-Agent": WEB_APP_USER_AGENT,
            "Content-Type": content_type,
            "Origin": origin,
            "Referer": f"{origin}/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "lang": "en_US",
            "client-server": CLIENT_SERVER_BY_REGION[self.region],
            "AUTH-CLIENT-ID": "sigen",
            "VERSION": "RELEASE",
            "sg-v": WEB_APP_VERSION,
            "sg-bui": WEB_APP_BUILD,
            "sg-env": "1",
            "sg-platform": "web",
            "sg-pkg": WEB_APP_PACKAGE,
            "sg-ts": timestamp,
            "sg-log-id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}{timestamp}")),
            "sg-session": self.session_id,
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        return headers

    async def login(self) -> None:
        url = f"{self.base_url}auth/oauth/token"
        data = {
            "scope": "server",
            "grant_type": "password",
            "userDeviceId": self.user_device_id,
            "username": self.username,
            "password": self._password_encrypted,
        }
        session = self._session_or_raise()
        async with session.post(
            url,
            data=data,
            headers=self._request_headers("application/x-www-form-urlencoded", "auth/oauth/token"),
            auth=aiohttp.BasicAuth("sigen", "sigen"),
        ) as response:
            payload = await response.json(content_type=None)
            if response.status != 200:
                raise RuntimeError(
                    f"Login failed status={response.status} body={_redact_secrets(payload)}"
                )
            token_data = payload.get("data") or {}
            if "access_token" not in token_data:
                raise RuntimeError(
                    f"Login response missing access_token: {_redact_secrets(payload)}"
                )
            self._apply_token(token_data)
            LOGGER.info("cloud_login_ok region=%s", self.region)

    async def refresh_access_token(self) -> None:
        if not self.refresh_token:
            await self.login()
            return
        url = f"{self.base_url}auth/oauth/token"
        data = {
            "scope": "server",
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        session = self._session_or_raise()
        async with session.post(
            url,
            data=data,
            headers=self._request_headers("application/x-www-form-urlencoded", "auth/oauth/token"),
            auth=aiohttp.BasicAuth("sigen", "sigen"),
        ) as response:
            payload = await response.json(content_type=None)
            if response.status != 200:
                LOGGER.warning(
                    "token_refresh_failed status=%s body=%s; re-login",
                    response.status,
                    _redact_secrets(payload),
                )
                await self.login()
                return
            token_data = payload.get("data") or {}
            if "access_token" not in token_data:
                LOGGER.warning(
                    "token_refresh_invalid; re-login body=%s",
                    _redact_secrets(payload),
                )
                await self.login()
                return
            self._apply_token(token_data)
            LOGGER.debug("cloud_token_refreshed")

    def _apply_token(self, token_data: dict) -> None:
        self.access_token = token_data["access_token"]
        self.refresh_token = token_data.get("refresh_token", self.refresh_token)
        expires_in = float(token_data.get("expires_in", 3600))
        self.token_expiry = time.time() + expires_in

    async def ensure_valid_token(self) -> None:
        if not self.access_token or time.time() >= (self.token_expiry - TOKEN_REFRESH_SKEW_SECONDS):
            if self.refresh_token and self.access_token:
                await self.refresh_access_token()
            else:
                await self.login()

    def _auth_headers(self) -> dict[str, str]:
        if not self.access_token:
            raise RuntimeError("Not authenticated")
        return self._request_headers("application/json", "", self.access_token)

    def _require_station(self) -> tuple[int | str, str]:
        if self.station_id is None or not self.ac_sn:
            raise RuntimeError("station_id/ac_sn unknown; call fetch_station_info first")
        return self.station_id, self.ac_sn

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        await self.ensure_valid_token()
        url = f"{self.base_url}{path}"
        session = self._session_or_raise()
        retried_401 = False
        attempt = 0

        while True:
            async with session.request(
                method,
                url,
                headers=self._auth_headers(),
                params=params,
                json=json_body,
            ) as response:
                payload = await response.json(content_type=None)
                status = response.status
                retry_after = response.headers.get("Retry-After")

            if status == 401 and not retried_401:
                LOGGER.warning("cloud_http_401 path=%s; re-login and retry once", path)
                self.access_token = None
                await self.login()
                retried_401 = True
                continue

            if status in (429, 503) and attempt < HTTP_MAX_ATTEMPTS - 1:
                delay = self._retry_after_delay(retry_after, attempt)
                LOGGER.warning(
                    "cloud_http_retry status=%s path=%s attempt=%s delay=%.1fs",
                    status,
                    path,
                    attempt + 1,
                    delay,
                )
                await asyncio.sleep(delay)
                attempt += 1
                continue

            if status != 200 or payload.get("code") not in (0, None):
                raise RuntimeError(
                    f"{method} {path} failed status={status} "
                    f"payload={_redact_secrets(payload)}"
                )
            return payload

    @staticmethod
    def _retry_after_delay(retry_after: str | None, attempt: int) -> float:
        """Prefer Retry-After header; otherwise exponential backoff."""
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return HTTP_BACKOFF_BASE_SECONDS * (2**attempt)

    async def fetch_station_info(self) -> dict:
        payload = await self._request_json("GET", "device/owner/station/home")
        data = payload["data"]
        self.station_id = data["stationId"]
        ac_list = data.get("acSnList") or []
        self.ac_sn = ac_list[0] if ac_list else None
        LOGGER.info(
            "station_resolved station_id=%s has_ac=%s ac_sn=%s",
            self.station_id,
            data.get("hasAcCharger"),
            self.ac_sn,
        )
        return data

    async def get_ac_ev_current(self) -> CurrentInfo:
        station_id, _ = self._require_station()
        payload = await self._request_json(
            "GET",
            "device/acevse/charge/read/current",
            params={"stationId": station_id},
        )
        data = payload["data"]
        return CurrentInfo(
            last_set_current=float(data["lastSetCurrent"]),
            max_current=float(data["maxCurrent"]),
            dlm_status=data.get("dlmStatus"),
        )

    async def set_ac_ev_current(self, amps: int) -> dict:
        station_id, _ = self._require_station()
        payload = await self._request_json(
            "PUT",
            "device/acevse/charge/current",
            params={"stationId": station_id, "current": amps},
        )
        LOGGER.info("current_set amps=%s", amps)
        return payload

    async def get_charge_status(self) -> tuple[int, str]:
        station_id, _ = self._require_station()
        payload = await self._request_json(
            "GET",
            "device/acevse/charge/status",
            params={"stationId": station_id},
        )
        code = int(payload["data"])
        try:
            label = CHARGE_STATUS_LABELS[ChargeStatus(code)]
        except ValueError:
            label = f"unknown_{code}"
        return code, label

    async def start_charging(self) -> dict:
        # Cloud quirk: enable=0 starts charging; enable=1 stops (do not "fix").
        station_id, ac_sn = self._require_station()
        payload = await self._request_json(
            "PUT",
            "device/acevse/charge/start",
            params={"enable": 0, "stationId": station_id, "snCode": ac_sn},
        )
        LOGGER.info("charge_start_ok")
        return payload

    async def stop_charging(self) -> dict:
        # Cloud quirk: enable=1 stops charging; enable=0 starts (do not "fix").
        station_id, ac_sn = self._require_station()
        payload = await self._request_json(
            "PUT",
            "device/acevse/charge/start",
            params={"enable": 1, "stationId": station_id, "snCode": ac_sn},
        )
        LOGGER.info("charge_stop_ok")
        return payload

    @staticmethod
    def _infer_active_phases(power_kw: float, current_a: float, voltage_v: float) -> int:
        """Estimate active phase count from P ≈ N · I · V.

        Assumes realtime ``chargingOutputCurrent`` is per-phase current (not total).
        That assumption is unverified against Sigenergy docs; treat results as best-effort.
        """
        if power_kw <= 0.05 or current_a <= 0.1 or voltage_v <= 0:
            return 0
        single_phase_w = current_a * voltage_v
        if single_phase_w <= 0:
            return 0
        phases = round((power_kw * 1000) / single_phase_w)
        return max(1, min(3, phases))

    async def get_realtime(self) -> RealtimeInfo | None:
        station_id, ac_sn = self._require_station()
        try:
            payload = await self._request_json(
                "GET",
                "device/acevse/charge/realtime",
                params={"stationId": station_id, "snCode": ac_sn},
            )
        except RuntimeError as exc:
            LOGGER.debug("realtime_unavailable error=%s", exc)
            return None
        data = payload.get("data") or {}
        detail = data.get("acChargeRealTimeVO") or {}
        power_kw = float(detail.get("chargingPower") or data.get("chargingPower") or 0)
        current_a = float(detail.get("chargingOutputCurrent") or 0)
        voltage_v = float(detail.get("chargingVoltage") or 0)
        single_kw = float(data.get("singleChargingPower") or 0)
        temp = detail.get("internalTemperature")
        return RealtimeInfo(
            power_kw=power_kw,
            current_a=current_a,
            voltage_v=voltage_v,
            single_phase_power_kw=single_kw,
            active_phases=self._infer_active_phases(power_kw, current_a, voltage_v),
            internal_temperature_c=float(temp) if temp is not None else None,
            charging_start_time=detail.get("chargingStartTimeStr"),
        )

    async def get_energy_totals(self) -> EnergyInfo | None:
        station_id, ac_sn = self._require_station()
        try:
            payload = await self._request_json(
                "GET",
                "data-process/acevse/energy",
                params={"stationId": station_id, "snCode": ac_sn},
            )
        except RuntimeError as exc:
            LOGGER.debug("energy_unavailable error=%s", exc)
            return None
        data = payload.get("data") or {}
        return EnergyInfo(
            lifetime_kwh=float(data.get("lifetimeEnergy") or 0),
            weekly_kwh=float(data.get("weeklyEnergy") or 0),
            monthly_kwh=float(data.get("monthlyEnergy") or 0),
        )

    async def get_device_param(self) -> dict:
        if self._cached_device_param is not None:
            return self._cached_device_param
        station_id, ac_sn = self._require_station()
        payload = await self._request_json(
            "GET",
            "device/acevse/param",
            params={"stationId": station_id, "snCode": ac_sn},
        )
        self._cached_device_param = payload.get("data") or {}
        return self._cached_device_param

    async def get_more_setting(self) -> MoreSettingInfo | None:
        """Read advanced charger settings (Output Mode, DLM, etc.) from cloud."""
        station_id, ac_sn = self._require_station()
        try:
            payload = await self._request_json(
                "GET",
                f"device/acevse/more-setting/{station_id}",
                params={"snCode": ac_sn},
            )
        except RuntimeError as exc:
            LOGGER.debug("more_setting_read_unavailable error=%s", exc)
            return None
        data = payload.get("data") or {}
        return MoreSettingInfo(
            phase_type=int(data.get("phaseType", 0)),
            phase_auto_switch=int(data.get("phaseAutoSwitch", 0)),
            dlm_status=int(data.get("dlmStatus", 0)),
            meter_phase=int(data.get("meterPhase", 0)),
            charging_output_current=float(data.get("chargingOutputCurrent") or 0),
            home_circuit_breaker=data.get("homeCircuitBreaker"),
            pile_circuit_breaker=data.get("pileCircuitBreaker"),
            off_grid_charge=data.get("offGridCharge"),
            is_support_three_phase=data.get("isSupportThreePhase"),
        )

    async def get_phase_mode(self) -> str:
        more = await self.get_more_setting()
        if more is None:
            return "unknown"
        return MoreSettingInfo.decode_phase_mode(more.phase_type, more.phase_auto_switch)

    async def _post_more_setting(self, **fields: int) -> bool:
        """POST partial more-setting update. Returns True when cloud reports success."""
        station_id, ac_sn = self._require_station()
        payload = {"stationId": station_id, "snCode": ac_sn, **fields}
        await self.ensure_valid_token()
        url = f"{self.base_url}device/acevse/more-setting"
        session = self._session_or_raise()
        async with session.post(url, headers=self._auth_headers(), json=payload) as response:
            body = await response.json(content_type=None)
            if response.status != 200 or body.get("code") not in (0, None):
                raise RuntimeError(
                    f"POST device/acevse/more-setting failed status={response.status} payload={body}"
                )
            accepted = body.get("data") is True
            LOGGER.debug("more_setting_post fields=%s accepted=%s body=%s", fields, accepted, body)
            return accepted

    async def _apply_phase_mode_once(self, mode: str) -> bool:
        mode = mode.strip().lower()
        if mode == "auto":
            return await self._post_more_setting(phaseAutoSwitch=1)

        output_mode = PHASE_MODE_SINGLE if mode == "1_phase" else PHASE_MODE_THREE
        if not await self._post_more_setting(phaseAutoSwitch=0):
            LOGGER.warning("phase_auto_disable_not_acknowledged mode=%s", mode)
        await asyncio.sleep(PHASE_MODE_SETTLE_SECONDS)
        return await self._post_more_setting(outputMode=output_mode)

    async def set_phase_mode(self, mode: str) -> None:
        """Set configured output phase mode: 1_phase, 3_phase, or auto."""
        mode = mode.strip().lower()
        if mode not in {"1_phase", "3_phase", "auto"}:
            raise ValueError(f"Unsupported phase mode {mode!r}; use 1_phase, 3_phase, or auto")

        if await self._apply_phase_mode_once(mode):
            LOGGER.info("phase_mode_set_ok mode=%s", mode)
            return

        status_code, _ = await self.get_charge_status()
        if status_code == ChargeStatus.CHARGING:
            LOGGER.info("phase_mode_retry_after_stop mode=%s", mode)
            await self.stop_charging()
            await asyncio.sleep(PHASE_MODE_RETRY_DELAY_SECONDS)
            if await self._apply_phase_mode_once(mode):
                LOGGER.info("phase_mode_set_ok_after_stop mode=%s", mode)
                return

        # Fallback: full payload (sigen library style) after stop.
        station_id, ac_sn = self._require_station()
        if mode == "auto":
            fields = {"phaseAutoSwitch": 1}
        else:
            output_mode = PHASE_MODE_SINGLE if mode == "1_phase" else PHASE_MODE_THREE
            fields = {"phaseAutoSwitch": 0, "outputMode": output_mode}
        payload = {
            "chargingOutputCurrent": None,
            "outputMode": fields.get("outputMode"),
            "stationId": station_id,
            "snCode": ac_sn,
            "dlmStatus": None,
            "meterPhase": None,
            "homeCircuitBreaker": None,
            "phaseAutoSwitch": fields.get("phaseAutoSwitch"),
            "offGridCharge": None,
        }
        await self.ensure_valid_token()
        url = f"{self.base_url}device/acevse/more-setting"
        session = self._session_or_raise()
        async with session.post(url, headers=self._auth_headers(), json=payload) as response:
            body = await response.json(content_type=None)
            if response.status != 200 or body.get("code") not in (0, None):
                raise RuntimeError(f"Phase mode fallback failed: {body}")
            if body.get("data") is True:
                LOGGER.info("phase_mode_set_ok_fallback mode=%s", mode)
                return

        raise RuntimeError(
            f"Cloud rejected phase mode {mode!r}. "
            "Try stopping charging first, or confirm in the mySigen app."
        )

    async def get_snapshot(self, include_slow: bool = True) -> EvacSnapshot:
        """Fetch a coherent EVAC state snapshot.

        When ``include_slow`` is False, reuse the last cached energy / more_setting /
        device_param values instead of refetching those slower endpoints.
        """
        if include_slow:
            (
                status_code_label,
                current,
                realtime,
                energy,
                more_setting,
                param,
            ) = await asyncio.gather(
                self.get_charge_status(),
                self.get_ac_ev_current(),
                self.get_realtime(),
                self.get_energy_totals(),
                self.get_more_setting(),
                self.get_device_param(),
            )
            status_code, status_label = status_code_label
            self._cached_energy = energy
            self._cached_more_setting = more_setting
        else:
            status_code_label, current, realtime = await asyncio.gather(
                self.get_charge_status(),
                self.get_ac_ev_current(),
                self.get_realtime(),
            )
            status_code, status_label = status_code_label
            energy = self._cached_energy
            more_setting = self._cached_more_setting
            param = self._cached_device_param or {}

        phase_mode = (
            MoreSettingInfo.decode_phase_mode(
                more_setting.phase_type,
                more_setting.phase_auto_switch,
            )
            if more_setting is not None
            else "unknown"
        )
        return EvacSnapshot(
            status_code=status_code,
            status_label=status_label,
            current=current,
            realtime=realtime,
            energy=energy,
            phase_mode=phase_mode,
            more_setting=more_setting,
            device_model=param.get("deviceModel"),
            firmware_version=param.get("acevseFirmwareVersion"),
        )
