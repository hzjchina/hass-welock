"""WeLock lock entity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityDescription
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .api import DeviceType, WeLockActivity, WeLockDevice, WeLockError
from .const import (
    CONF_RELOCK_DELAY,
    DEFAULT_RELOCK_DELAY,
    DOMAIN,
    EVENT_UNLOCK,
    KEY_LOCK,
)
from .coordinator import WeLockConfigEntry, WeLockCoordinator
from .entity import WeLockEntity, async_setup_device_entities

PARALLEL_UPDATES = 1

LOCK_DESCRIPTION = LockEntityDescription(key=KEY_LOCK, name=None)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeLockConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up WeLock locks."""
    coordinator = entry.runtime_data
    relock = int(entry.options.get(CONF_RELOCK_DELAY, DEFAULT_RELOCK_DELAY))

    def _factory(device: WeLockDevice) -> list[WeLockLock]:
        if device.device_type is DeviceType.LOCK:
            return [WeLockLock(coordinator, device.device_id, relock)]
        return []

    entry.async_on_unload(
        async_setup_device_entities(coordinator, async_add_entities, _factory)
    )


class WeLockLock(WeLockEntity, LockEntity):
    """A WeLock smart lock.

    WeLock locks relock automatically, so the cloud only offers "unlock".
    The entity shows "unlocked" for the configured relock delay after an
    unlock (from HA or reported via push by the lock itself).
    """

    def __init__(
        self, coordinator: WeLockCoordinator, device_id: str, relock_delay: int
    ) -> None:
        """Init."""
        super().__init__(coordinator, device_id, LOCK_DESCRIPTION)
        self._relock_delay = relock_delay
        self._attr_is_locked = True
        self._cancel_relock: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to activities."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_activity_listener(
                self._device_id, self._on_activity
            )
        )
        self.async_on_remove(self._cancel_timer)

    @callback
    def _on_activity(self, activity: WeLockActivity) -> None:
        if activity.kind == EVENT_UNLOCK:
            self._set_unlocked()

    @callback
    def _cancel_timer(self) -> None:
        if self._cancel_relock:
            self._cancel_relock()
            self._cancel_relock = None

    @callback
    def _set_unlocked(self) -> None:
        self._cancel_timer()
        self._attr_is_unlocking = False
        self._attr_is_locked = False
        self.async_write_ha_state()
        self._cancel_relock = async_call_later(
            self.hass, self._relock_delay, self._relocked
        )

    @callback
    def _relocked(self, _now: Any) -> None:
        self._cancel_relock = None
        self._attr_is_locked = True
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the last activity."""
        if (act := self.device.last_activity) is None:
            return None
        return {f"last_{k}": v for k, v in act.as_dict().items()}

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock via the WeLock gateway."""
        self._attr_is_unlocking = True
        self.async_write_ha_state()
        try:
            await self.coordinator.api.async_unlock(self.device)
        except WeLockError as err:
            self._attr_is_unlocking = False
            self.async_write_ha_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unlock_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self._set_unlocked()

    async def async_lock(self, **kwargs: Any) -> None:
        """WeLock locks relock by themselves."""
        if self.is_locked:
            return
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="lock_not_supported"
        )
