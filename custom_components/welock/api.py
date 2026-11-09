"""WeLock cloud API and MQTT push client.

This replaces the external ``welock-iot`` package. The protocol is unchanged,
but the following problems of that library are fixed here:

* all devices shared one class-level ``status`` dict, so values of one device
  could show up on another device
* failed unlock / remote commands were reported as success
* HTTP 401 / expired tokens did not trigger a re-authentication
* the MQTT task died silently on unexpected errors and never reconnected
* the library forced DEBUG logging and installed its own log handler
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiomqtt
from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout
from homeassistant.util import dt as dt_util

from .const import (
    API_BASE,
    EVENT_RELAY,
    EVENT_UNLOCK,
    UNLOCK_METHODS,
    UNLOCK_TYPE_DOOR_SENSOR,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = ClientTimeout(total=15)
MQTT_RECONNECT_MIN = 5
MQTT_RECONNECT_MAX = 300


class WeLockError(Exception):
    """Base error."""


class WeLockAuthError(WeLockError):
    """Authentication failed / token no longer valid."""


class WeLockConnectionError(WeLockError):
    """Cloud could not be reached."""


class WeLockCommandError(WeLockError):
    """The cloud rejected a command."""


class DeviceType(IntEnum):
    """WeLock device types reported by the cloud."""

    LOCK = 1
    WIFIBOX3 = 3
    DOOR_SENSOR = 6
    UNSUPPORTED = 100

    @classmethod
    def from_value(cls, value: Any) -> DeviceType:
        """Map a raw value to a device type."""
        try:
            return cls(int(value))
        except (TypeError, ValueError):
            return cls.UNSUPPORTED


@dataclass(slots=True)
class WeLockActivity:
    """One unlock / relay event."""

    kind: str  # EVENT_UNLOCK or EVENT_RELAY
    method: str | None
    method_id: int | None
    user_id: str | None
    user_name: str | None
    time: datetime | None
    result: int | None

    @property
    def who(self) -> str | None:
        """Best human-readable identification of the user."""
        return self.user_name or self.user_id

    def as_dict(self) -> dict[str, Any]:
        """Return attributes for entities / events."""
        return {
            "method": self.method,
            "method_id": self.method_id,
            "user": self.who,
            "user_id": self.user_id,
            "time": self.time.isoformat() if self.time else None,
        }


@dataclass(slots=True)
class WeLockDevice:
    """A WeLock device (lock, WIFIBOX3, door sensor)."""

    device_id: str
    name: str
    device_type: DeviceType
    model: str | None = None
    gateway_model: str | None = None
    ble_name: str | None = None
    battery: int | None = None
    door_open: bool | None = None
    last_activity: WeLockActivity | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def model_display(self) -> str | None:
        """Model shown in the device registry."""
        return self.model or self.gateway_model

    def merge_runtime_state(self, old: WeLockDevice) -> None:
        """Keep push-derived state when the device list is refreshed."""
        self.door_open = old.door_open
        self.last_activity = old.last_activity
        if self.battery is None:
            self.battery = old.battery


def _parse_time(value: Any) -> datetime | None:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return dt_util.utc_from_timestamp(ms / 1000)


def parse_device(item: Mapping[str, Any]) -> WeLockDevice | None:
    """Parse one entry of ``Device/List``."""
    device_id = item.get("number")
    if not device_id:
        return None
    device_id = str(device_id)
    device_type = DeviceType.from_value(item.get("deviceType", 1))
    gateway = item.get("gatewayModel")
    # Only the WIFIBOX3 supports the remote trigger
    if device_type is DeviceType.WIFIBOX3 and gateway and gateway != "WIFIBOX3":
        device_type = DeviceType.UNSUPPORTED

    ble_name: str | None = None
    battery: int | None = None
    ble_infos = item.get("bluetooth")
    if isinstance(ble_infos, list) and ble_infos:
        chosen = ble_infos[0] if len(ble_infos) == 1 else None
        if chosen is None:
            chosen = next(
                (b for b in ble_infos if b.get("position") in (0, 1)), ble_infos[0]
            )
        ble_name = chosen.get("deviceName")
        try:
            power = int(chosen.get("power"))
        except (TypeError, ValueError):
            power = 0
        battery = power if 0 < power <= 100 else None

    return WeLockDevice(
        device_id=device_id,
        name=item.get("notes") or device_id,
        device_type=device_type,
        model=item.get("model") or None,
        gateway_model=gateway or None,
        ble_name=ble_name,
        battery=battery,
        raw=dict(item),
    )


@dataclass(slots=True)
class PushUpdate:
    """Result of parsing one MQTT message."""

    device_id: str
    battery: int | None = None
    door_open: bool | None = None
    activities: list[WeLockActivity] = field(default_factory=list)


def parse_push_message(data: Mapping[str, Any]) -> PushUpdate | None:
    """Parse an MQTT payload ``{"number": ..., "status": [...]}``."""
    device_id = data.get("number")
    status = data.get("status")
    if not device_id or not isinstance(status, list):
        return None
    update = PushUpdate(device_id=str(device_id))

    for item in status:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("type", "")).strip()
        try:
            unlock_type = int(item.get("unlockType", 0))
        except (TypeError, ValueError):
            unlock_type = 0
        try:
            result = int(item.get("unlockResult", 0))
        except (TypeError, ValueError):
            result = None

        if unlock_type == UNLOCK_TYPE_DOOR_SENSOR:
            update.door_open = result == 1
            continue

        if kind == "Battery":
            desc = item.get("resultDescription")
            try:
                level = int(str(desc), 16)
            except (TypeError, ValueError):
                _LOGGER.debug("Unparsable battery value %r", desc)
                continue
            if 0 <= level <= 100:
                update.battery = level
            continue

        if kind in ("Unlock", "RelayAction"):
            if kind == "Unlock":
                ts = item.get("catchTime", item.get("time"))
                method_id = unlock_type or 17
                event = EVENT_UNLOCK
            else:
                ts = item.get("time", item.get("catchTime"))
                method_id = unlock_type or None
                event = EVENT_RELAY
            user_id = item.get("userId")
            update.activities.append(
                WeLockActivity(
                    kind=event,
                    method=UNLOCK_METHODS.get(method_id, "other")
                    if method_id
                    else None,
                    method_id=method_id,
                    user_id=str(user_id) if user_id not in (None, "") else None,
                    user_name=item.get("uRemark") or None,
                    time=_parse_time(ts),
                    result=result,
                )
            )
    return update


class WeLockAuth:
    """Provides a valid access token (implemented in __init__.py)."""

    def __init__(self, session: ClientSession) -> None:
        """Init."""
        self.session = session

    async def async_get_access_token(self) -> str:
        """Return a valid access token."""
        raise NotImplementedError


class WeLockApi:
    """Thin async client for the WeLock IoT cloud."""

    def __init__(self, auth: WeLockAuth) -> None:
        """Init."""
        self._auth = auth

    async def _post(
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        token = await self._auth.async_get_access_token()
        try:
            async with self._auth.session.post(
                urljoin(API_BASE, path),
                params=dict(params) if params else None,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": token,
                },
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise WeLockAuthError(f"HTTP {resp.status} for {path}")
                resp.raise_for_status()
                body = await resp.json(content_type=None)
        except WeLockError:
            raise
        except ClientResponseError as err:
            raise WeLockConnectionError(f"{path}: HTTP {err.status}") from err
        except (ClientError, TimeoutError, json.JSONDecodeError, ValueError) as err:
            raise WeLockConnectionError(f"{path}: {err!r}") from err

        if not isinstance(body, dict):
            raise WeLockConnectionError(f"{path}: unexpected response {body!r}")
        _LOGGER.debug("%s -> code=%s msg=%s", path, body.get("code"), body.get("msg"))
        code = body.get("code")
        if code in (401, 403, "401", "403"):
            raise WeLockAuthError(body.get("msg") or f"code {code}")
        return body

    async def async_get_devices(self) -> list[WeLockDevice]:
        """Return all devices of the account."""
        body = await self._post("Device/List")
        if body.get("code") != 0:
            raise WeLockConnectionError(
                f"Device/List failed: code={body.get('code')} msg={body.get('msg')}"
            )
        data = body.get("data") or []
        if not isinstance(data, list):
            return []
        return [dev for item in data if (dev := parse_device(item)) is not None]

    async def _command(self, path: str, params: Mapping[str, Any]) -> None:
        body = await self._post(path, params)
        if body.get("code") != 0:
            raise WeLockCommandError(
                body.get("msg") or f"{path} failed (code {body.get('code')})"
            )

    async def async_unlock(self, device: WeLockDevice) -> None:
        """Unlock a lock through its gateway."""
        await self._command(
            "Device/GatewayUnlocking",
            {"number": device.device_id, "deviceName": device.ble_name or ""},
        )

    async def async_trigger(self, device: WeLockDevice) -> None:
        """Trigger the WIFIBOX3 relay / remote control."""
        await self._command("Device/triggerSwitch", {"number": device.device_id})

    async def async_get_mqtt_config(self) -> dict[str, Any]:
        """Return MQTT broker credentials for push messages."""
        body = await self._post("MQTT/info")
        data = body.get("data")
        if body.get("code") != 0 or not isinstance(data, dict) or not data.get("url"):
            raise WeLockConnectionError(f"MQTT/info failed: {body.get('msg')}")
        return data


class WeLockPushClient:
    """Keeps an MQTT connection to the WeLock broker alive."""

    def __init__(
        self,
        api: WeLockApi,
        on_message: Callable[[dict[str, Any]], None],
        on_connection_change: Callable[[bool], None],
        tls_context_factory: Callable[[], Awaitable[Any]],
    ) -> None:
        """Init."""
        self._api = api
        self._on_message = on_message
        self._on_connection_change = on_connection_change
        self._tls_context_factory = tls_context_factory
        self._task: asyncio.Task | None = None
        self.connected = False
        self.last_error: str | None = None

    def start(self, create_task: Callable[..., asyncio.Task]) -> None:
        """Start the background loop."""
        if self._task is None:
            self._task = create_task(self._run(), "welock_mqtt")

    async def stop(self) -> None:
        """Stop the background loop."""
        if (task := self._task) is None:
            return
        self._task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._set_connected(False)

    def _set_connected(self, value: bool) -> None:
        if self.connected != value:
            self.connected = value
            self._on_connection_change(value)

    async def _run(self) -> None:
        delay = MQTT_RECONNECT_MIN
        config: dict[str, Any] | None = None
        while True:
            try:
                if config is None:
                    config = await self._api.async_get_mqtt_config()
                url = urlsplit(config["url"])
                tls = None
                if url.scheme in ("ssl", "mqtts", "tls", "wss"):
                    tls = await self._tls_context_factory()
                async with aiomqtt.Client(
                    hostname=url.hostname or "",
                    port=url.port or (8883 if tls else 1883),
                    identifier=config.get("clientId") or None,
                    username=config.get("username") or None,
                    password=config.get("password") or None,
                    keepalive=int(config.get("keepalive") or 60),
                    tls_context=tls,
                ) as client:
                    await client.subscribe(config["topic"])
                    _LOGGER.info("Connected to WeLock push service")
                    self.last_error = None
                    self._set_connected(True)
                    delay = MQTT_RECONNECT_MIN
                    async for message in client.messages:
                        self._handle(message.payload)
                # iterator ended -> treat as disconnect
                raise aiomqtt.MqttError("message stream ended")
            except asyncio.CancelledError:
                raise
            except aiomqtt.MqttCodeError as err:
                self.last_error = str(err)
                # bad credentials / not authorised -> fetch fresh credentials
                if getattr(err, "rc", None) in (3, 4, 5, 134, 135):
                    config = None
            except WeLockAuthError as err:
                # token problem; coordinator will trigger reauth on next poll
                self.last_error = str(err)
                config = None
                delay = MQTT_RECONNECT_MAX
            except Exception as err:  # noqa: BLE001 - must never die
                self.last_error = repr(err)
                if not isinstance(err, aiomqtt.MqttError):
                    config = None

            self._set_connected(False)
            _LOGGER.warning(
                "WeLock push connection lost (%s), retrying in %ss",
                self.last_error,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, MQTT_RECONNECT_MAX)

    def _handle(self, payload: Any) -> None:
        try:
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode("utf-8")
            data = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as err:
            _LOGGER.debug("Ignoring invalid push payload %r: %s", payload, err)
            return
        _LOGGER.debug("Push message: %s", data)
        if isinstance(data, dict):
            try:
                self._on_message(data)
            except Exception:
                _LOGGER.exception("Error handling WeLock push message %s", data)
