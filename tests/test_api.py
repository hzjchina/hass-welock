"""Tests for the HTTP client and MQTT payload handling."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.welock.api import (
    DeviceType,
    WeLockApi,
    WeLockAuth,
    WeLockAuthError,
    WeLockCommandError,
    WeLockConnectionError,
    WeLockDevice,
)

BASE = "https://iot.we-lock.com/"


class _Auth(WeLockAuth):
    async def async_get_access_token(self) -> str:
        return "tok"


@pytest.fixture
def api(hass, aioclient_mock: AiohttpClientMocker) -> WeLockApi:
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    return WeLockApi(_Auth(async_get_clientsession(hass)))


DEV = WeLockDevice("LOCK1", "x", DeviceType.LOCK, ble_name="BLE")


async def test_unlock_ok(api, aioclient_mock) -> None:
    aioclient_mock.post(BASE + "Device/GatewayUnlocking", json={"code": 0})
    await api.async_unlock(DEV)
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "tok"


async def test_unlock_rejected(api, aioclient_mock) -> None:
    aioclient_mock.post(
        BASE + "Device/GatewayUnlocking", json={"code": -1, "msg": "offline"}
    )
    with pytest.raises(WeLockCommandError, match="offline"):
        await api.async_unlock(DEV)


async def test_http_401(api, aioclient_mock) -> None:
    aioclient_mock.post(BASE + "Device/List", status=401)
    with pytest.raises(WeLockAuthError):
        await api.async_get_devices()


async def test_http_500(api, aioclient_mock) -> None:
    aioclient_mock.post(BASE + "Device/List", status=500)
    with pytest.raises(WeLockConnectionError):
        await api.async_get_devices()


async def test_device_list(api, aioclient_mock) -> None:
    aioclient_mock.post(
        BASE + "Device/List",
        json={
            "code": 0,
            "data": [
                {
                    "number": "A",
                    "deviceType": 1,
                    "bluetooth": [
                        {"deviceName": "b1", "power": 0, "position": 2},
                        {"deviceName": "b2", "power": 80, "position": 1},
                    ],
                },
                {"number": "B", "deviceType": 3, "gatewayModel": "WIFIBOX"},
                {"notes": "no number"},
            ],
        },
    )
    devs = await api.async_get_devices()
    assert [d.device_id for d in devs] == ["A", "B"]
    assert devs[0].ble_name == "b2" and devs[0].battery == 80
    assert devs[1].device_type is DeviceType.UNSUPPORTED
