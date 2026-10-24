"""Config, options and reauth flows."""
from __future__ import annotations

from unittest.mock import patch

from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import (
    BASE,
    CLIENT_ID,
    DOMAIN,
    LOGIN_URL,
    TOKEN_URL,
    calls_to,
    make_entry,
    mock_backend,
)

LOGIN_OK = {
    "token": "access-1",
    "refresh_token": "refresh-1",
    "client_id": CLIENT_ID,
    "home_id": "Home",
    "ha_endpoint": f"{BASE}/api/ha",
}
USER_INPUT = {
    "email": "anna@example.test",
    "password": "secret-pass",
    "include_entities": ["climate.living"],
    "outdoor_sensor": "sensor.outdoor",
    "debounce_seconds": 3,
}


async def _start_user_flow(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    return result


async def test_user_flow_creates_entry_without_storing_the_password(hass, aioclient_mock):
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    result = await _start_user_flow(hass)
    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data["api_key"] == "access-1" and data["refresh_token"] == "refresh-1"
    assert data["endpoint"] == BASE and data["client_id"] == CLIENT_ID
    assert "password" not in data
    assert result["result"].unique_id == CLIENT_ID
    login = calls_to(aioclient_mock, LOGIN_URL)[0]
    assert login["client_name"] == "ha_ai_push_integration" and login["force"] is False


async def test_user_flow_invalid_auth(hass, aioclient_mock):
    aioclient_mock.post(LOGIN_URL, status=401)
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass, aioclient_mock):
    aioclient_mock.post(LOGIN_URL, exc=ClientError())
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "cannot_connect"}


async def test_options_flow_without_password_keeps_credentials(hass, aioclient_mock):
    """August audit C1 regression: saving options without a password must not
    drop refresh_token / home_id / client_id, nor rename the home backend-side."""
    mock_backend(aioclient_mock)
    entry = make_entry(hass)
    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"email": "anna@example.test", "password": "", "include_entities": ["climate.living", "climate.bed"],
             "exclude_entities": [], "outdoor_sensor": "sensor.outdoor", "debounce_seconds": 3, "rotate_token": False},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data["refresh_token"] == "refresh-1"
    assert entry.data["home_id"] == "Home" and entry.data["client_id"] == CLIENT_ID
    assert entry.data["include_entities"] == ["climate.living", "climate.bed"]
    device_update = calls_to(aioclient_mock, TOKEN_URL)[0]
    assert device_update["home_id"] == "Home"             # never a location_name fallback


async def test_reauth_flow_stores_new_tokens(hass, aioclient_mock):
    aioclient_mock.post(LOGIN_URL, json={**LOGIN_OK, "token": "access-new", "refresh_token": "refresh-new"})
    entry = make_entry(hass, api_key="revoked", refresh_token="revoked")
    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "secret-pass"})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data["api_key"] == "access-new" and entry.data["refresh_token"] == "refresh-new"
