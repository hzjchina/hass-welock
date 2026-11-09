"""WeLock door sensor."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api import DeviceType, WeLockDevice
from .const import KEY_DOOR
from .coordinator import WeLockConfigEntry
from .entity import WeLockEntity, async_setup_device_entities

PARALLEL_UPDATES = 0

DOOR_DESCRIPTION = BinarySensorEntityDescription(
    key=KEY_DOOR,
    translation_key="door",
    device_class=BinarySensorDeviceClass.DOOR,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeLockConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up WeLock binary sensors."""
    coordinator = entry.runtime_data

    def _factory(device: WeLockDevice) -> list[WeLockDoorSensor]:
        if device.device_type is DeviceType.DOOR_SENSOR:
            return [WeLockDoorSensor(coordinator, device.device_id, DOOR_DESCRIPTION)]
        return []

    entry.async_on_unload(
        async_setup_device_entities(coordinator, async_add_entities, _factory)
    )


class WeLockDoorSensor(WeLockEntity, BinarySensorEntity, RestoreEntity):
    """Door contact (state only arrives via push, so it is restored)."""

    async def async_added_to_hass(self) -> None:
        """Restore last known state."""
        await super().async_added_to_hass()
        if (
            self.device.door_open is None
            and (last := await self.async_get_last_state())
            and last.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
        ):
            self.device.door_open = last.state == STATE_ON

    @property
    def is_on(self) -> bool | None:
        """Return True when the door is open."""
        return self.device.door_open
