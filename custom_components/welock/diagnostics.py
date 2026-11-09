"""Diagnostics for WeLock."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import WeLockConfigEntry

TO_REDACT = {
    "access_token",
    "refresh_token",
    "token",
    "deviceName",
    "ble_name",
    "mac",
    "password",
    "username",
    "userId",
    "user_id",
    "uRemark",
    "user_name",
    "phone",
    "email",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WeLockConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    devices = {}
    for device_id, device in (coordinator.data or {}).items():
        data = asdict(device)
        data["device_type"] = device.device_type.name
        devices[device_id] = data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "push": {
            "connected": coordinator.push.connected,
            "last_error": coordinator.push.last_error,
        },
        "last_update_success": coordinator.last_update_success,
        "devices": async_redact_data(devices, TO_REDACT),
    }
