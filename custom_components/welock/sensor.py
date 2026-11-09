"""WeLock sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .api import DeviceType, WeLockActivity, WeLockDevice
from .const import (
    EVENT_RELAY,
    EVENT_UNLOCK,
    KEY_BATTERY,
    KEY_LAST_ACTIVITY,
    KEY_RECORD,
    UNLOCK_METHODS,
)
from .coordinator import WeLockConfigEntry
from .entity import WeLockEntity, async_setup_device_entities

PARALLEL_UPDATES = 0

METHOD_OPTIONS = sorted({*UNLOCK_METHODS.values(), "relay", "other"})


def _record_value(device: WeLockDevice) -> str | None:
    act = device.last_activity
    if act is None:
        return None
    if act.kind == EVENT_RELAY and act.method in (None, "other"):
        return "relay"
    return act.method or "other"


def _record_attrs(device: WeLockDevice) -> dict[str, Any] | None:
    act = device.last_activity
    if act is None:
        return None
    return {**act.as_dict(), "event": act.kind}


@dataclass(frozen=True, kw_only=True)
class WeLockSensorDescription(SensorEntityDescription):
    """Sensor description with value getter."""

    value_fn: Callable[[WeLockDevice], Any]
    attrs_fn: Callable[[WeLockDevice], dict[str, Any] | None] = lambda _: None
    device_types: tuple[DeviceType, ...]


SENSORS: tuple[WeLockSensorDescription, ...] = (
    WeLockSensorDescription(
        key=KEY_BATTERY,
        translation_key="battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.battery,
        device_types=(DeviceType.LOCK, DeviceType.DOOR_SENSOR),
    ),
    WeLockSensorDescription(
        key=KEY_RECORD,
        translation_key="record",
        device_class=SensorDeviceClass.ENUM,
        options=METHOD_OPTIONS,
        value_fn=_record_value,
        attrs_fn=_record_attrs,
        device_types=(DeviceType.LOCK, DeviceType.WIFIBOX3),
    ),
    WeLockSensorDescription(
        key=KEY_LAST_ACTIVITY,
        translation_key="last_activity",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.last_activity.time if d.last_activity else None,
        attrs_fn=lambda d: {"user": d.last_activity.who} if d.last_activity else None,
        device_types=(DeviceType.LOCK, DeviceType.WIFIBOX3),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeLockConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up WeLock sensors."""
    coordinator = entry.runtime_data

    def _factory(device: WeLockDevice) -> list[SensorEntity]:
        entities: list[SensorEntity] = []
        for desc in SENSORS:
            if device.device_type not in desc.device_types:
                continue
            cls = WeLockRecordSensor if desc.key == KEY_RECORD else WeLockSensor
            entities.append(cls(coordinator, device.device_id, desc))
        return entities

    entry.async_on_unload(
        async_setup_device_entities(coordinator, async_add_entities, _factory)
    )


class WeLockSensor(WeLockEntity, SensorEntity):
    """Generic WeLock sensor."""

    entity_description: WeLockSensorDescription

    @property
    def native_value(self) -> Any:
        """Return the state."""
        return self.entity_description.value_fn(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        return self.entity_description.attrs_fn(self.device)


class WeLockRecordSensor(WeLockSensor, RestoreEntity):
    """Last activity; restored after restart so it isn't 'unknown'."""

    async def async_added_to_hass(self) -> None:
        """Restore the last activity."""
        await super().async_added_to_hass()
        if self.device.last_activity is not None:
            return
        last = await self.async_get_last_state()
        if last is None or last.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        attrs = last.attributes
        if "event" not in attrs:  # state from v1.x (free text) - not restorable
            return
        time: datetime | None = (
            dt_util.parse_datetime(attrs["time"]) if attrs.get("time") else None
        )
        method = last.state if last.state != "relay" else None
        self.device.last_activity = WeLockActivity(
            kind=attrs.get("event", EVENT_UNLOCK),
            method=method,
            method_id=attrs.get("method_id"),
            user_id=attrs.get("user_id"),
            user_name=attrs.get("user")
            if attrs.get("user") != attrs.get("user_id")
            else None,
            time=time,
            result=None,
        )
        # let the timestamp sensor & lock attributes pick it up
        self.coordinator.async_update_listeners()
