"""WeLock event entity: fires on every unlock / relay action."""

from __future__ import annotations

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import DeviceType, WeLockActivity, WeLockDevice
from .const import EVENT_RELAY, EVENT_UNLOCK, KEY_EVENT
from .coordinator import WeLockConfigEntry
from .entity import WeLockEntity, async_setup_device_entities

PARALLEL_UPDATES = 0

EVENT_DESCRIPTION = EventEntityDescription(
    key=KEY_EVENT,
    translation_key="activity",
    event_types=[EVENT_UNLOCK, EVENT_RELAY],
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeLockConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up WeLock event entities."""
    coordinator = entry.runtime_data

    def _factory(device: WeLockDevice) -> list[WeLockActivityEvent]:
        if device.device_type in (DeviceType.LOCK, DeviceType.WIFIBOX3):
            return [
                WeLockActivityEvent(coordinator, device.device_id, EVENT_DESCRIPTION)
            ]
        return []

    entry.async_on_unload(
        async_setup_device_entities(coordinator, async_add_entities, _factory)
    )


class WeLockActivityEvent(WeLockEntity, EventEntity):
    """Event entity for unlock activity – ideal as automation trigger."""

    async def async_added_to_hass(self) -> None:
        """Subscribe to activities."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_activity_listener(
                self._device_id, self._on_activity
            )
        )

    @callback
    def _on_activity(self, activity: WeLockActivity) -> None:
        self._trigger_event(activity.kind, activity.as_dict())
        self.async_write_ha_state()
