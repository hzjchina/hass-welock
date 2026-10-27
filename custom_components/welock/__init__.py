"""The WeLock integration."""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from welock_iot.client import WeLockApi
from welock_iot.manager import Resmanager
from welock_iot.models import WeLockDevice, WeLockMessageListener

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    aiohttp_client,
    config_entry_oauth2_flow,
    config_validation as cv,
    device_registry as dr,
)
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.typing import ConfigType

from . import api
from .const import DOMAIN, WELOCK_DEVICE_DISCOVER, WELOCK_DEVICE_UPDATE

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LOCK,
    Platform.SENSOR,
    # Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

type WelockConfigEntry = ConfigEntry[HomeAssistantWeLockData]


class HomeAssistantWeLockData(NamedTuple):
    """Manager all data."""

    manager: Resmanager


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up welock."""

    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WelockConfigEntry) -> bool:
    """Set up WeLock from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    auth_mgr = api.ConfigEntryAuth(
        hass, aiohttp_client.async_get_clientsession(hass), session
    )
    client = WeLockApi(auth_mgr)

    res_mgr = Resmanager(client)

    await res_mgr.update_device_list()

    linstener = DeviceListener(hass, res_mgr)
    res_mgr.add_device_listener(linstener)

    entry.runtime_data = HomeAssistantWeLockData(res_mgr)

    device_registry = dr.async_get(hass)

    # Cleanup device registry
    await cleanup_device_registry(hass, entry.entry_id, res_mgr)

    for device in res_mgr.device_map.values():
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device.device_id)},
            manufacturer="WELOCK",
            name=device.device_name,
            model=device.model_show,
        )

    # discover seonsor
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # await hass.async_add_executor_job(res_mgr.initWeMq)

    await res_mgr.initWeMq()
    return True


async def cleanup_device_registry(
    hass: HomeAssistant, config_entry_id: str, res_mgr: Resmanager
) -> None:
    """Remove deleted device registry entry if there are no remaining entities."""
    device_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, config_entry_id
    ):
        for item in device_entry.identifiers:
            if item[0] == DOMAIN and item[1] not in res_mgr.device_map:
                # if item[0] == DOMAIN:
                device_registry.async_remove_device(device_entry.id)
                break


async def async_unload_entry(hass: HomeAssistant, entry: WelockConfigEntry) -> bool:
    """Unload a config entry."""
    result = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # await hass.data[DOMAIN][entry.entry_id].home_instance.async_unload()
    # hass.data[DOMAIN].pop(entry.entry_id)
    if result:
        data = entry.runtime_data
        if data.manager.mqclient is not None:
            await data.manager.mqclient.disconnect()

    return result


async def async_remove_entry(hass: HomeAssistant, entry: WelockConfigEntry) -> None:
    """Remove a config entry.

    This will revoke the credentials from welock.
    """
    _LOGGER.debug("async_remove_entry。。。。。。")


class DeviceListener(WeLockMessageListener):
    """Add, delete and modify monitoring equipment."""

    def __init__(
        self,
        hass: HomeAssistant,
        manager: Resmanager,
    ) -> None:
        """Init DeviceListener."""
        self.hass = hass
        self.manager = manager

    def update_device(self, device: WeLockDevice) -> None:
        """Update a device."""
        dispatcher_send(self.hass, f"{WELOCK_DEVICE_UPDATE}_{device.device_id}")

    def add_device(self, device: WeLockDevice) -> None:
        """Add device added listener."""
        self.hass.add_job(self.async_remove_device, device.device_id)
        dispatcher_send(self.hass, WELOCK_DEVICE_DISCOVER, [device.device_id])

    def remove_device(self, device_id: str) -> None:
        """Add device removed listener."""
        self.hass.add_job(self.async_remove_device, device_id)

    @callback
    def async_remove_device(self, device_id: str) -> None:
        """Remove device from Home Assistant."""
        device_registry = dr.async_get(self.hass)
        device_entry = device_registry.async_get_device(
            identifiers={(DOMAIN, device_id)}
        )
        if device_entry is not None:
            device_registry.async_remove_device(device_entry.id)

    def on_message(self, msg_data: dict[str, Any]) -> None:
        """."""
        _LOGGER.debug("on_message: {msg_data}")
