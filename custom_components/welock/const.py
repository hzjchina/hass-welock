"""Constants for the WeLock integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "welock"
MANUFACTURER: Final = "WELOCK"

API_BASE: Final = "https://iot.we-lock.com/"
OAUTH2_AUTHORIZE: Final = "https://iot.we-lock.com/auth/login"
OAUTH2_TOKEN: Final = "https://iot.we-lock.com/auth/Authorize"

# The cloud device list carries the battery level; MQTT only pushes events.
# Refreshing periodically keeps battery values fresh and finds new devices.
UPDATE_INTERVAL: Final = timedelta(minutes=30)

CONF_RELOCK_DELAY: Final = "relock_delay"
DEFAULT_RELOCK_DELAY: Final = 8

# Keys are kept identical to v1.x so existing entity unique IDs survive.
KEY_LOCK: Final = "LockOperation"
KEY_REMOTE: Final = "RemoteControl"
KEY_BATTERY: Final = "Battery"
KEY_RECORD: Final = "Record"
KEY_DOOR: Final = "status"
KEY_LAST_ACTIVITY: Final = "LastActivity"
KEY_EVENT: Final = "Event"

EVENT_UNLOCK: Final = "unlock"
EVENT_RELAY: Final = "relay_action"

# unlockType -> method slug (used for translations and event attributes)
UNLOCK_METHODS: Final[dict[int, str]] = {
    1: "fingerprint",
    2: "passcode",
    3: "card",
    4: "bt_remote",
    5: "remote_433",
    6: "app",
    10: "passcode",
    11: "temporary_passcode",
    12: "temporary_passcode",
    13: "temporary_passcode",
    14: "passcode",
    15: "face",
    16: "nfc",
    17: "wifibox",
    19: "vacation",
    211: "local",
}
UNLOCK_TYPE_DOOR_SENSOR: Final = 31
