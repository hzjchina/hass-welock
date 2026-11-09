"""WeLock WIFIBOX3 remote button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import DeviceType, WeLockDevice, WeLockError
from .const import DOMAIN, KEY_REMOTE
from .coordinator import WeLockConfigEntry
from .entity import WeLockEntity, async_setup_device_entities

PARALLEL_UPDATES = 1

REMOTE_DESCRIPTION = ButtonEntityDescription(
    key=KEY_REMOTE, translation_key="remote_control"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeLockConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up WeLock buttons."""
    coordinator = entry.runtime_data

    def _factory(device: WeLockDevice) -> list[WeLockRemoteButton]:
        if device.device_type is DeviceType.WIFIBOX3:
            return [
                WeLockRemoteButton(coordinator, device.device_id, REMOTE_DESCRIPTION)
            ]
        return []

    entry.async_on_unload(
        async_setup_device_entities(coordinator, async_add_entities, _factory)
    )


class WeLockRemoteButton(WeLockEntity, ButtonEntity):
    """Triggers the WIFIBOX3 relay / remote control."""

    async def async_press(self) -> None:
        """Press the button."""
        try:
            await self.coordinator.api.async_trigger(self.device)
        except WeLockError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
