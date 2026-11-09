"""Tests for the WeLock integration."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.welock.api import (
    WeLockAuthError,
    WeLockCommandError,
    parse_push_message,
)
from custom_components.welock.const import DOMAIN
from custom_components.welock.diagnostics import async_get_config_entry_diagnostics

UNLOCK_MSG = {
    "number": "LOCK1",
    "status": [
        {
            "type": "Unlock",
            "unlockType": 3,
            "unlockResult": 1,
            "userId": 10,
            "uRemark": "Thomas",
            "catchTime": 1794210000000,
        }
    ],
}


def _eid(hass: HomeAssistant, platform: str, uid: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, uid)
    assert entity_id, uid
    return entity_id


async def test_setup_creates_entities_with_legacy_unique_ids(
    hass: HomeAssistant, init_integration
) -> None:
    """Unique IDs must match v1.x so existing entities are kept."""
    assert init_integration.state is ConfigEntryState.LOADED
    for platform, uid in (
        ("lock", "welock.LOCK1_LockOperation"),
        ("sensor", "welock.LOCK1_Battery"),
        ("sensor", "welock.LOCK1_Record"),
        ("button", "welock.WL310879_RemoteControl"),
        ("sensor", "welock.WL310879_Record"),
        ("binary_sensor", "welock.DOOR1_status"),
        ("sensor", "welock.DOOR1_Battery"),
        ("event", "welock.LOCK1_Event"),
        ("sensor", "welock.LOCK1_LastActivity"),
    ):
        _eid(hass, platform, uid)

    assert hass.states.get(_eid(hass, "sensor", "welock.LOCK1_Battery")).state == "76"
    assert (
        hass.states.get(_eid(hass, "lock", "welock.LOCK1_LockOperation")).state
        == "locked"
    )
    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), init_integration.entry_id
    )
    assert {d.name for d in devices} == {"Wohnungstür", "WL310879", "Türkontakt"}


async def test_push_unlock_event(hass: HomeAssistant, init_integration) -> None:
    """A push unlock updates record, event, timestamp and lock."""
    coordinator = init_integration.runtime_data
    coordinator._handle_push(UNLOCK_MSG)
    await hass.async_block_till_done()

    record = hass.states.get(_eid(hass, "sensor", "welock.LOCK1_Record"))
    assert record.state == "card"
    assert record.attributes["user"] == "Thomas"
    assert record.attributes["user_id"] == "10"

    event = hass.states.get(_eid(hass, "event", "welock.LOCK1_Event"))
    assert event.attributes["event_type"] == "unlock"
    assert event.attributes["method"] == "card"

    ts = hass.states.get(_eid(hass, "sensor", "welock.LOCK1_LastActivity"))
    assert ts.state.startswith("2026-11-")

    lock_id = _eid(hass, "lock", "welock.LOCK1_LockOperation")
    assert hass.states.get(lock_id).state == "unlocked"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=9))
    await hass.async_block_till_done()
    assert hass.states.get(lock_id).state == "locked"


async def test_push_does_not_leak_between_devices(
    hass: HomeAssistant, init_integration
) -> None:
    """The old library shared one status dict for all devices."""
    init_integration.runtime_data._handle_push(UNLOCK_MSG)
    await hass.async_block_till_done()
    assert (
        hass.states.get(_eid(hass, "sensor", "welock.WL310879_Record")).state
        == "unknown"
    )


async def test_push_battery_and_door(hass: HomeAssistant, init_integration) -> None:
    """Battery (hex) and door sensor messages."""
    c = init_integration.runtime_data
    c._handle_push(
        {
            "number": "DOOR1",
            "status": [{"type": "x", "unlockType": 31, "unlockResult": 1}],
        }
    )
    c._handle_push(
        {"number": "LOCK1", "status": [{"type": "Battery", "resultDescription": "3C"}]}
    )
    await hass.async_block_till_done()
    assert (
        hass.states.get(_eid(hass, "binary_sensor", "welock.DOOR1_status")).state
        == "on"
    )
    assert hass.states.get(_eid(hass, "sensor", "welock.LOCK1_Battery")).state == "60"


async def test_unlock_service(hass: HomeAssistant, init_integration, mock_api) -> None:
    """Unlock success and failure."""
    lock_id = _eid(hass, "lock", "welock.LOCK1_LockOperation")
    await hass.services.async_call(
        "lock", "unlock", {"entity_id": lock_id}, blocking=True
    )
    mock_api["unlock"].assert_awaited_once()
    assert mock_api["unlock"].await_args.args[0].ble_name == "BLE-1"
    assert hass.states.get(lock_id).state == "unlocked"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=9))
    await hass.async_block_till_done()

    mock_api["unlock"].side_effect = WeLockCommandError("gateway offline")
    with pytest.raises(HomeAssistantError, match="gateway offline"):
        await hass.services.async_call(
            "lock", "unlock", {"entity_id": lock_id}, blocking=True
        )
    assert hass.states.get(lock_id).state == "locked"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "lock", "open", {"entity_id": lock_id}, blocking=True
        )


async def test_lock_while_locked_is_noop(hass: HomeAssistant, init_integration) -> None:
    """Locking an already locked lock is fine."""
    lock_id = _eid(hass, "lock", "welock.LOCK1_LockOperation")
    await hass.services.async_call(
        "lock", "lock", {"entity_id": lock_id}, blocking=True
    )


async def test_button(hass: HomeAssistant, init_integration, mock_api) -> None:
    """WIFIBOX3 remote button."""
    btn = _eid(hass, "button", "welock.WL310879_RemoteControl")
    await hass.services.async_call("button", "press", {"entity_id": btn}, blocking=True)
    mock_api["trigger"].assert_awaited_once()


async def test_auth_error_starts_reauth(
    hass: HomeAssistant, init_integration, mock_api
) -> None:
    """401 on refresh triggers re-authentication."""
    mock_api["devices"].side_effect = WeLockAuthError("401")
    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_new_device_added_and_removed(
    hass: HomeAssistant, init_integration, mock_api
) -> None:
    """Devices appearing / disappearing in the account."""
    from custom_components.welock.api import parse_device
    from tests.conftest import DEVICE_LIST

    extra = {"number": "LOCK2", "notes": "Garage", "deviceType": 1}
    mock_api["devices"].side_effect = lambda: [
        parse_device(d) for d in [*DEVICE_LIST[:1], extra]
    ]
    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    _eid(hass, "lock", "welock.LOCK2_LockOperation")
    names = {
        d.name
        for d in dr.async_entries_for_config_entry(
            dr.async_get(hass), init_integration.entry_id
        )
    }
    assert names == {"Wohnungstür", "Garage"}


async def test_unload(hass: HomeAssistant, init_integration) -> None:
    """Unload works."""
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_setup_retry_on_connection_error(
    hass: HomeAssistant, config_entry, setup_credentials, mock_api
) -> None:
    """Cloud down at startup -> retry later."""
    from custom_components.welock.api import WeLockConnectionError

    mock_api["devices"].side_effect = WeLockConnectionError("down")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_diagnostics(hass: HomeAssistant, init_integration) -> None:
    """Diagnostics redact secrets."""
    diag = await async_get_config_entry_diagnostics(hass, init_integration)
    assert diag["entry"]["data"]["token"] == "**REDACTED**"
    assert diag["devices"]["LOCK1"]["ble_name"] == "**REDACTED**"


async def test_options_flow(hass: HomeAssistant, init_integration) -> None:
    """Relock delay option."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"relock_delay": 3}
    )
    await hass.async_block_till_done()
    assert init_integration.options == {"relock_delay": 3}


def test_parse_relay_message() -> None:
    """WIFIBOX relay action."""
    upd = parse_push_message(
        {
            "number": "WL1",
            "status": [{"type": "RelayAction", "time": 1794210000000, "userId": ""}],
        }
    )
    assert upd.activities[0].kind == "relay_action"
    assert upd.activities[0].method is None
    assert parse_push_message({"foo": 1}) is None


async def test_own_unlock_confirmation_not_shown_twice(
    hass: HomeAssistant, init_integration, mock_api
) -> None:
    """A late WIFIBOX push after our own unlock must not unlock again."""
    lock_id = _eid(hass, "lock", "welock.LOCK1_LockOperation")
    await hass.services.async_call(
        "lock", "unlock", {"entity_id": lock_id}, blocking=True
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=9))
    await hass.async_block_till_done()
    assert hass.states.get(lock_id).state == "locked"

    wifibox = {
        "number": "LOCK1",
        "status": [{"type": "Unlock", "unlockType": 17, "userId": "WL_X"}],
    }
    init_integration.runtime_data._handle_push(wifibox)
    await hass.async_block_till_done()
    assert hass.states.get(lock_id).state == "locked"
    # still recorded as activity
    record = hass.states.get(_eid(hass, "sensor", "welock.LOCK1_Record"))
    assert record.state == "wifibox"

    # a second WIFIBOX unlock (e.g. from the WeLock app) is shown again
    init_integration.runtime_data._handle_push(wifibox)
    await hass.async_block_till_done()
    assert hass.states.get(lock_id).state == "unlocked"
