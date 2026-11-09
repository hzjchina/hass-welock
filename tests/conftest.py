"""Fixtures for WeLock tests."""

from __future__ import annotations

import time
from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.welock.api import parse_device
from custom_components.welock.const import DOMAIN

DEVICE_LIST = [
    {
        "number": "LOCK1",
        "notes": "Wohnungstür",
        "model": "TouchEBL41",
        "deviceType": 1,
        "bluetooth": [{"deviceName": "BLE-1", "power": 76, "position": 1}],
    },
    {
        "number": "WL310879",
        "notes": "",
        "model": "",
        "gatewayModel": "WIFIBOX3",
        "deviceType": 3,
    },
    {
        "number": "DOOR1",
        "notes": "Türkontakt",
        "model": "DS1",
        "deviceType": 6,
        "bluetooth": [{"deviceName": "BLE-D", "power": 55}],
    },
]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations."""
    return


@pytest.fixture
def mock_api() -> Generator[dict[str, AsyncMock]]:
    """Patch the cloud API."""
    base = "custom_components.welock.api.WeLockApi"
    with (
        patch(
            f"{base}.async_get_devices",
            AsyncMock(side_effect=lambda: [parse_device(d) for d in DEVICE_LIST]),
        ) as devices,
        patch(f"{base}.async_unlock", AsyncMock()) as unlock,
        patch(f"{base}.async_trigger", AsyncMock()) as trigger,
        patch("custom_components.welock.api.WeLockPushClient.start") as start,
    ):
        yield {"devices": devices, "unlock": unlock, "trigger": trigger, "start": start}


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A config entry with a valid token."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="welock",
        unique_id=DOMAIN,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "tok",
                "refresh_token": "ref",
                "expires_at": time.time() + 3600,
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        },
    )


@pytest.fixture
async def setup_credentials(hass: HomeAssistant) -> None:
    """Register application credentials."""
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential("cid", "secret"), DOMAIN
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, config_entry, setup_credentials, mock_api
) -> MockConfigEntry:
    """Set up the integration."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
