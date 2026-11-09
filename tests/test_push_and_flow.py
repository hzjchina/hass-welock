"""MQTT push loop and OAuth config flow."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiomqtt
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import config_entry_oauth2_flow
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.welock.api import WeLockPushClient
from custom_components.welock.const import DOMAIN, OAUTH2_AUTHORIZE, OAUTH2_TOKEN

CFG = {
    "url": "ssl://mq.example:8883",
    "clientId": "c",
    "username": "u",
    "password": "p",
    "topic": "t/#",
}


class FakeClient:
    """Minimal aiomqtt.Client replacement."""

    instances: list[FakeClient] = []
    fail_first = True

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.subscribe = AsyncMock()
        FakeClient.instances.append(self)

    async def __aenter__(self):
        if FakeClient.fail_first and len(FakeClient.instances) == 1:
            raise aiomqtt.MqttError("boom")
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def messages(self):
        async def gen():
            yield SimpleNamespace(payload=b"not json")
            yield SimpleNamespace(
                payload=json.dumps({"number": "X", "status": []}).encode()
            )
            await asyncio.Event().wait()

        return gen()


async def test_push_reconnects_and_delivers(hass: HomeAssistant) -> None:
    """First connect fails, second succeeds and delivers a message."""
    api = MagicMock()
    api.async_get_mqtt_config = AsyncMock(return_value=CFG)
    received, states = [], []
    client = WeLockPushClient(
        api, received.append, states.append, AsyncMock(return_value="TLS")
    )
    FakeClient.instances = []
    with (
        patch("custom_components.welock.api.aiomqtt.Client", FakeClient),
        patch("custom_components.welock.api.MQTT_RECONNECT_MIN", 0),
    ):
        client.start(lambda coro, name: hass.async_create_background_task(coro, name))
        for _ in range(50):
            await asyncio.sleep(0)
            if received:
                break
        assert received == [{"number": "X", "status": []}]
        assert client.connected and states == [True]
        kw = FakeClient.instances[-1].kwargs
        assert (
            kw["hostname"] == "mq.example"
            and kw["port"] == 8883
            and kw["tls_context"] == "TLS"
        )
        await client.stop()
    assert not client.connected


async def test_record_restored(
    hass: HomeAssistant, config_entry, setup_credentials, mock_api
) -> None:
    """Last activity survives a restart."""
    mock_restore_cache(
        hass,
        [
            State(
                "sensor.wohnungstur_letzte_offnung",
                "card",
                {
                    "event": "unlock",
                    "method_id": 3,
                    "user": "Thomas",
                    "user_id": "10",
                    "time": "2026-11-09T07:37:51+00:00",
                },
            )
        ],
    )
    from homeassistant.helpers import entity_registry as er

    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        "welock.LOCK1_Record",
        suggested_object_id="wohnungstur_letzte_offnung",
    )
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.wohnungstur_letzte_offnung").state == "card"
    ts = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, "welock.LOCK1_LastActivity"
    )
    assert hass.states.get(ts).state == "2026-11-09T07:37:51+00:00"


async def test_full_oauth_flow(
    hass,
    hass_client_no_auth,
    aioclient_mock,
    current_request_with_host,
    setup_credentials,
    mock_api,
) -> None:
    """Complete OAuth flow creates an entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    state = config_entry_oauth2_flow._encode_jwt(
        hass,
        {
            "flow_id": result["flow_id"],
            "redirect_uri": "https://example.com/auth/external/callback",
        },
    )
    assert result["url"].startswith(OAUTH2_AUTHORIZE)
    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200
    aioclient_mock.post(
        OAUTH2_TOKEN,
        json={
            "refresh_token": "r",
            "access_token": "a",
            "type": "Bearer",
            "expires_in": 60,
        },
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == "create_entry"
    assert result["title"] == "WeLock"
    await hass.async_block_till_done()

    # second attempt aborts
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "abort" and result["reason"] == "already_configured"
