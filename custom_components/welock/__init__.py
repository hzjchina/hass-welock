"""The WeLock integration."""

from __future__ import annotations

import logging

from aiohttp import ClientError, ClientResponseError
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import (
    aiohttp_client,
    config_entry_oauth2_flow,
)
from homeassistant.helpers import (
    device_registry as dr,
)

from .api import WeLockApi, WeLockAuth, WeLockAuthError, WeLockConnectionError
from .const import DOMAIN, MANUFACTURER
from .coordinator import WeLockConfigEntry, WeLockCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.LOCK,
    Platform.SENSOR,
]


class OAuthSessionAuth(WeLockAuth):
    """Token provider backed by Home Assistant's OAuth2 session."""

    def __init__(
        self, hass: HomeAssistant, oauth: config_entry_oauth2_flow.OAuth2Session
    ) -> None:
        """Init."""
        super().__init__(aiohttp_client.async_get_clientsession(hass))
        self._oauth = oauth

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing it if needed."""
        try:
            await self._oauth.async_ensure_token_valid()
        except ClientResponseError as err:
            if 400 <= err.status < 500:
                raise WeLockAuthError(f"token refresh rejected: {err.status}") from err
            raise WeLockConnectionError(f"token refresh failed: {err}") from err
        except ClientError as err:
            raise WeLockConnectionError(f"token refresh failed: {err}") from err
        except Exception as err:
            # newer HA versions raise dedicated OAuth2 exceptions
            if "Reauth" in type(err).__name__:
                raise WeLockAuthError(str(err)) from err
            if "OAuth2" in type(err).__name__:
                raise WeLockConnectionError(str(err)) from err
            raise
        return self._oauth.token["access_token"]


async def async_setup_entry(hass: HomeAssistant, entry: WeLockConfigEntry) -> bool:
    """Set up WeLock from a config entry."""
    try:
        implementation = (
            await config_entry_oauth2_flow.async_get_config_entry_implementation(
                hass, entry
            )
        )
    except ValueError as err:
        # application credentials were deleted
        raise ConfigEntryAuthFailed("OAuth credentials missing") from err
    except HomeAssistantError as err:  # ImplementationUnavailableError
        raise ConfigEntryNotReady(str(err)) from err

    oauth = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    api = WeLockApi(OAuthSessionAuth(hass, oauth))
    coordinator = WeLockCoordinator(hass, entry, api)

    await coordinator.async_config_entry_first_refresh()

    registry = dr.async_get(hass)
    for device in coordinator.data.values():
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device.device_id)},
            manufacturer=MANUFACTURER,
            name=device.name,
            model=device.model_display,
        )
    coordinator._remove_stale_devices(set(coordinator.data))  # noqa: SLF001

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    coordinator.push.start(
        lambda coro, name: entry.async_create_background_task(hass, coro, name)
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: WeLockConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WeLockConfigEntry) -> bool:
    """Unload a config entry."""
    await entry.runtime_data.push.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: WeLockConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing devices that are no longer in the WeLock account."""
    data = entry.runtime_data.data or {}
    return not any(
        ident[0] == DOMAIN and ident[1] in data for ident in device_entry.identifiers
    )
