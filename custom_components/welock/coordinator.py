"""Data coordinator: cloud polling + MQTT push."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.ssl import get_default_context

from .api import (
    WeLockActivity,
    WeLockApi,
    WeLockAuthError,
    WeLockDevice,
    WeLockError,
    WeLockPushClient,
    parse_push_message,
)
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

type WeLockConfigEntry = ConfigEntry[WeLockCoordinator]

ActivityListener = Callable[[WeLockActivity], None]


class WeLockCoordinator(DataUpdateCoordinator[dict[str, WeLockDevice]]):
    """Holds all WeLock devices of one account."""

    config_entry: WeLockConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: WeLockConfigEntry, api: WeLockApi
    ) -> None:
        """Init."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.api = api
        self._activity_listeners: dict[str, list[ActivityListener]] = {}

        async def _tls() -> Any:
            return get_default_context()

        self.push = WeLockPushClient(
            api, self._handle_push, self._handle_push_connection, _tls
        )

    async def _async_update_data(self) -> dict[str, WeLockDevice]:
        try:
            devices = await self.api.async_get_devices()
        except WeLockAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WeLockError as err:
            raise UpdateFailed(str(err)) from err

        old = self.data or {}
        new: dict[str, WeLockDevice] = {}
        for device in devices:
            if prev := old.get(device.device_id):
                device.merge_runtime_state(prev)
            new[device.device_id] = device

        if self.data is not None:
            self._remove_stale_devices(set(new))
        return new

    def _remove_stale_devices(self, current: set[str]) -> None:
        registry = dr.async_get(self.hass)
        for entry in dr.async_entries_for_config_entry(
            registry, self.config_entry.entry_id
        ):
            ids = {i[1] for i in entry.identifiers if i[0] == DOMAIN}
            if ids and not ids & current:
                _LOGGER.info("Removing WeLock device %s (no longer in account)", ids)
                registry.async_update_device(
                    entry.id, remove_config_entry_id=self.config_entry.entry_id
                )

    # ---- push -------------------------------------------------------------

    @callback
    def async_add_activity_listener(
        self, device_id: str, listener: ActivityListener
    ) -> Callable[[], None]:
        """Register a callback for unlock / relay activities of a device."""
        listeners = self._activity_listeners.setdefault(device_id, [])
        listeners.append(listener)

        @callback
        def _remove() -> None:
            listeners.remove(listener)

        return _remove

    @callback
    def _handle_push(self, message: dict[str, Any]) -> None:
        if self.data is None:
            return
        update = parse_push_message(message)
        if update is None:
            return
        device = self.data.get(update.device_id)
        if device is None:
            _LOGGER.debug("Push for unknown device %s, refreshing", update.device_id)
            self.hass.async_create_task(self.async_request_refresh())
            return
        if update.battery is not None:
            device.battery = update.battery
        if update.door_open is not None:
            device.door_open = update.door_open
        for activity in update.activities:
            device.last_activity = activity
            for listener in list(self._activity_listeners.get(device.device_id, [])):
                listener(activity)
        self.async_set_updated_data(self.data)

    @callback
    def _handle_push_connection(self, connected: bool) -> None:
        _LOGGER.debug("Push connection: %s", connected)
        if self.data is not None:
            self.async_update_listeners()
