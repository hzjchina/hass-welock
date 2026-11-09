"""Base entity for WeLock."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import WeLockDevice
from .const import DOMAIN, MANUFACTURER
from .coordinator import WeLockCoordinator


class WeLockEntity(CoordinatorEntity[WeLockCoordinator]):
    """Common base for all WeLock entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WeLockCoordinator,
        device_id: str,
        description: EntityDescription,
    ) -> None:
        """Init."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_id
        # same format as v1.x -> existing entities keep their IDs
        self._attr_unique_id = f"welock.{device_id}_{description.key}"
        device = coordinator.data[device_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            name=device.name,
            model=device.model_display,
        )

    @property
    def device(self) -> WeLockDevice:
        """Return the current device object."""
        return self.coordinator.data[self._device_id]

    @property
    def available(self) -> bool:
        """Available while the cloud is reachable (poll or push)."""
        return (
            self.coordinator.last_update_success or self.coordinator.push.connected
        ) and self._device_id in self.coordinator.data


def async_setup_device_entities(
    coordinator: WeLockCoordinator,
    add_entities: Callable[[Iterable[Entity]], None],
    factory: Callable[[WeLockDevice], Iterable[Entity]],
) -> Callable[[], None]:
    """Add entities for all devices now and for devices that appear later."""
    known: set[str] = set()

    @callback
    def _check() -> None:
        new = [d for d_id, d in coordinator.data.items() if d_id not in known]
        if not new:
            return
        known.update(d.device_id for d in new)
        add_entities([ent for device in new for ent in factory(device)])

    _check()
    return coordinator.async_add_listener(_check)
