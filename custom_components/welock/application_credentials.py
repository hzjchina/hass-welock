"""application_credentials platform the welock integration."""

from aiohttp import RequestInfo
from multidict import CIMultiDict, CIMultiDictProxy
from welock_iot.const import OAUTH2_AUTHORIZE, OAUTH2_TOKEN
from yarl import URL

from homeassistant.components.application_credentials import (
    AuthImplementation,
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import OAuth2TokenRequestReauthError


class WeLockAuthImplementation(AuthImplementation):
    """Recognize expired WeLock refresh tokens."""

    async def _token_request(self, data: dict) -> dict:
        """Map WeLock's successful HTTP response for invalid tokens to reauth."""
        token = await super()._token_request(data)
        if (
            data.get("grant_type") == "refresh_token"
            and token.get("msg") == "Wrong refresh_token"
        ):
            raise OAuth2TokenRequestReauthError(
                domain=self.service_domain,
                request_info=RequestInfo(
                    URL(self.token_url), "POST", CIMultiDictProxy(CIMultiDict())
                ),
            )
        return token


async def async_get_authorization_server(hass: HomeAssistant) -> AuthorizationServer:
    """2 Return authorization server."""
    return AuthorizationServer(
        authorize_url=OAUTH2_AUTHORIZE,
        token_url=OAUTH2_TOKEN,
    )


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> WeLockAuthImplementation:
    """Return an OAuth implementation that handles WeLock refresh failures."""
    return WeLockAuthImplementation(
        hass, auth_domain, credential, await async_get_authorization_server(hass)
    )
