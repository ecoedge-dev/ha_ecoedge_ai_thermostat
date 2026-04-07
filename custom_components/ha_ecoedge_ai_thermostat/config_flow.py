import asyncio
import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from aiohttp import ClientError, ClientTimeout

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import selector

from .config_schema import DOMAIN_SCHEMA
from .const import (
    CONF_API_KEY,
    CONF_DEBOUNCE_SECONDS,
    CONF_EMAIL,
    CONF_ENDPOINT,
    CONF_EXCLUDE,
    CONF_HOME_ID,
    CONF_INCLUDE,
    CONF_OUTDOOR_SENSOR,
    CONF_PASSWORD,
    CONF_ROTATE_TOKEN,
    CONF_CLIENT_ID,
    CONF_REFRESH_TOKEN,
    CONF_TIMEOUT_SECONDS,
    AUTH_LOGIN_URL,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

CLIENT_NAME = "ha_ai_push_integration"

DOMAIN_FIELDS = {
    CONF_ENDPOINT,
    CONF_HOME_ID,
    CONF_API_KEY,
    CONF_INCLUDE,
    CONF_EXCLUDE,
    CONF_OUTDOOR_SENSOR,
    CONF_DEBOUNCE_SECONDS,
    CONF_TIMEOUT_SECONDS,
    CONF_EMAIL,
}


def _normalize_user_input(user_input: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(user_input)

    items = data.get(CONF_INCLUDE)
    if isinstance(items, list):
        data[CONF_INCLUDE] = [item for item in items if item]
    elif isinstance(items, str):
        parts = [part.strip() for part in items.split(",") if part.strip()]
        if parts:
            data[CONF_INCLUDE] = parts
        elif CONF_INCLUDE in data:
            data.pop(CONF_INCLUDE)

    for key in (CONF_HOME_ID, CONF_EMAIL):
        if key not in data or not isinstance(data[key], str):
            continue
        value = data[key].strip()
        if value:
            data[key] = value
        else:
            data.pop(key)

    password = data.get(CONF_PASSWORD)
    if password is not None and isinstance(password, str):
        data[CONF_PASSWORD] = password.strip()

    data[CONF_ROTATE_TOKEN] = bool(data.get(CONF_ROTATE_TOKEN, False))

    outdoor = data.get(CONF_OUTDOOR_SENSOR)
    if outdoor is not None:
        value = str(outdoor).strip()
        if value:
            data[CONF_OUTDOOR_SENSOR] = value
        else:
            data.pop(CONF_OUTDOOR_SENSOR, None)

    return data


def _build_config_schema(defaults: Dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_EMAIL, default=defaults.get(CONF_EMAIL, "")): str,
            vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): str,
            vol.Optional(
                CONF_INCLUDE, default=defaults.get(CONF_INCLUDE, [])
            ): selector({"entity": {"domain": "climate", "multiple": True}}),
            vol.Optional(
                CONF_OUTDOOR_SENSOR,
                default=defaults.get(CONF_OUTDOOR_SENSOR, ""),
            ): selector(
                {
                    "entity": {
                        "domain": "sensor",
                        "multiple": False,
                    }
                }
            ),
            vol.Optional(
                CONF_DEBOUNCE_SECONDS,
                default=defaults.get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS),
            ): vol.Coerce(int),
        }
    )


def _build_options_schema(defaults: Dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_EMAIL, default=defaults.get(CONF_EMAIL, "")): str,
            vol.Optional(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): str,
            vol.Optional(
                CONF_INCLUDE, default=defaults.get(CONF_INCLUDE, [])
            ): selector({"entity": {"domain": "climate", "multiple": True}}),
            vol.Optional(
                CONF_EXCLUDE, default=defaults.get(CONF_EXCLUDE, [])
            ): selector({"entity": {"domain": "climate", "multiple": True}}),
            vol.Optional(
                CONF_OUTDOOR_SENSOR,
                default=defaults.get(CONF_OUTDOOR_SENSOR, ""),
            ): selector(
                {
                    "entity": {
                        "domain": "sensor",
                        "multiple": False,
                    }
                }
            ),
            vol.Optional(
                CONF_DEBOUNCE_SECONDS,
                default=defaults.get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS),
            ): vol.Coerce(int),
            vol.Optional(CONF_ROTATE_TOKEN, default=defaults.get(CONF_ROTATE_TOKEN, False)): bool,
        }
    )


def _blank_form_defaults(location_name: Optional[str] = None) -> Dict[str, Any]:
    return {
        CONF_EMAIL: "",
        CONF_PASSWORD: "",
        CONF_HOME_ID: location_name or "",
        CONF_INCLUDE: [],
        CONF_OUTDOOR_SENSOR: "",
        CONF_DEBOUNCE_SECONDS: DEFAULT_DEBOUNCE_SECONDS,
        CONF_ROTATE_TOKEN: False,
    }


def _options_form_defaults(config: Dict[str, Any]) -> Dict[str, Any]:
    defaults = _blank_form_defaults()
    defaults.update(
        {
            CONF_EMAIL: config.get(CONF_EMAIL, defaults[CONF_EMAIL]),
            CONF_HOME_ID: config.get(CONF_HOME_ID, defaults[CONF_HOME_ID]),
            CONF_INCLUDE: _ensure_list(config.get(CONF_INCLUDE)),
            CONF_OUTDOOR_SENSOR: config.get(CONF_OUTDOOR_SENSOR, ""),
            CONF_DEBOUNCE_SECONDS: config.get(
                CONF_DEBOUNCE_SECONDS, defaults[CONF_DEBOUNCE_SECONDS]
            ),
            CONF_ROTATE_TOKEN: False,
        }
    )
    defaults[CONF_PASSWORD] = ""
    return defaults


def _base_from_ha_endpoint(ha_endpoint: str) -> str:
    if not ha_endpoint:
        return ha_endpoint
    suffix = "/api/ha"
    if ha_endpoint.endswith(suffix):
        return ha_endpoint[: -len(suffix)]
    return ha_endpoint


def _ensure_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v)]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _apply_domain_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    schema_input = {k: data[k] for k in DOMAIN_FIELDS if k in data}
    validated = DOMAIN_SCHEMA(schema_input)
    for key, value in data.items():
        if key not in DOMAIN_FIELDS:
            validated[key] = value
    return validated


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class AuthRequestError(HomeAssistantError):
    """Raised when login request failed."""


class InvalidAuth(HomeAssistantError):
    """Raised when credentials are invalid."""


async def _async_update_device(flow, data: Dict[str, Any]) -> None:
    """Push updated home_id/location_name to the backend without re-authenticating."""
    token = data.get(CONF_API_KEY)
    endpoint = data.get(CONF_ENDPOINT, "").rstrip("/")
    if not token or not endpoint:
        return
    url = f"{endpoint}/api/auth/token"
    session = aiohttp_client.async_get_clientsession(flow.hass)
    try:
        async with session.post(
            url,
            json={
                "token": token,
                "home_id": data.get(CONF_HOME_ID) or flow.hass.config.location_name,
                "location_name": flow.hass.config.location_name,
            },
            timeout=ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS),
        ) as resp:
            if resp.status >= 400:
                _LOGGER.debug("Device update call returned %s", resp.status)
    except Exception as err:
        _LOGGER.debug("Device update failed (non-fatal): %s", err)


async def _async_login(flow, data: Dict[str, Any], password: str) -> Dict[str, Any]:
    url = AUTH_LOGIN_URL
    payload = {
        "email": data[CONF_EMAIL],
        "password": password,
        "home_id": data.get(CONF_HOME_ID) or flow.hass.config.location_name,
        "location_name": flow.hass.config.location_name,
        "latitude": flow.hass.config.latitude,
        "longitude": flow.hass.config.longitude,
        "timezone": str(flow.hass.config.time_zone),
        "client_name": CLIENT_NAME,
        "client_id": data.get(CONF_CLIENT_ID) or "",
    }
    session = aiohttp_client.async_get_clientsession(flow.hass)

    try:
        async with session.post(
            url,
            json=payload,
            timeout=ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS),
        ) as resp:
            if resp.status == 401:
                raise InvalidAuth
            if resp.status >= 400:
                text = await resp.text()
                _LOGGER.debug("Auth request failed for %s: %s (%s)", url, resp.status, text)
                raise AuthRequestError
            resp_data = await resp.json()
    except asyncio.TimeoutError as err:
        raise CannotConnect from err
    except ClientError as err:
        raise CannotConnect from err

    token = resp_data.get("token")
    if not token:
        raise AuthRequestError
    return resp_data


class HaAiPushConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 3

    def __init__(self) -> None:
        self._reconfigure_entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "HaAiPushOptionsFlowHandler":
        return HaAiPushOptionsFlowHandler()

    async def async_step_user(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is None and self._reconfigure_entry:
            current = {**self._reconfigure_entry.data, **self._reconfigure_entry.options}
            defaults = _options_form_defaults(current)
        else:
            defaults = (
                _blank_form_defaults(self.hass.config.location_name)
                if user_input is None
                else user_input
            )

        if user_input is not None:
            parsed = _normalize_user_input(user_input)
            password = parsed.pop(CONF_PASSWORD, "")
            # Carry over client_id from existing entry so backend reuses the same device
            if self._reconfigure_entry and not parsed.get(CONF_CLIENT_ID):
                existing = {**self._reconfigure_entry.data, **self._reconfigure_entry.options}
                if existing.get(CONF_CLIENT_ID):
                    parsed[CONF_CLIENT_ID] = existing[CONF_CLIENT_ID]
            if not password:
                errors["password"] = "password_required"
            else:
                try:
                    resp = await _async_login(self, parsed, password)
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except AuthRequestError:
                    errors["base"] = "auth_request_failed"
                else:
                    parsed[CONF_API_KEY] = resp["token"]
                    if resp.get("client_id"):
                        parsed[CONF_CLIENT_ID] = resp["client_id"]
                    if resp.get("refresh_token"):
                        parsed[CONF_REFRESH_TOKEN] = resp["refresh_token"]
                    ha_endpoint = resp.get("ha_endpoint")
                    if not ha_endpoint:
                        base = AUTH_LOGIN_URL.rsplit("/api/auth/login", 1)[0]
                        ha_endpoint = f"{base}/api/ha"
                    parsed[CONF_ENDPOINT] = _base_from_ha_endpoint(ha_endpoint)
                    if not parsed.get(CONF_HOME_ID) and resp.get("home_id"):
                        parsed[CONF_HOME_ID] = resp["home_id"]
                    try:
                        data = _apply_domain_schema(parsed)
                    except vol.Invalid:
                        errors["base"] = "invalid_config"
                    else:
                        if self._reconfigure_entry:
                            self.hass.config_entries.async_update_entry(
                                self._reconfigure_entry, data=data
                            )
                            await self.hass.config_entries.async_reload(
                                self._reconfigure_entry.entry_id
                            )
                            self._reconfigure_entry = None
                            return self.async_abort(reason="reconfigure_successful")

                        unique_id = data.get(CONF_HOME_ID) or f"{data[CONF_EMAIL]}@{data[CONF_ENDPOINT]}"
                        await self.async_set_unique_id(unique_id, raise_on_progress=False)
                        self._abort_if_unique_id_configured(updates=data)
                        title = data.get(CONF_HOME_ID) or data[CONF_EMAIL]
                        return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_config_schema(defaults),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if not entry:
            return self.async_abort(reason="unknown_entry")
        self._reconfigure_entry = entry
        return await self.async_step_user(user_input)

    async def async_step_import(self, user_input: Dict[str, Any]) -> FlowResult:
        parsed = _normalize_user_input(user_input)
        password = parsed.pop(CONF_PASSWORD, "")
        if not password:
            return self.async_abort(reason="password_required")

        try:
            resp = await _async_login(self, parsed, password)
        except InvalidAuth:
            return self.async_abort(reason="invalid_auth")
        except CannotConnect:
            return self.async_abort(reason="cannot_connect")
        except AuthRequestError:
            return self.async_abort(reason="auth_request_failed")

        parsed[CONF_API_KEY] = resp["token"]
        if resp.get("client_id"):
            parsed[CONF_CLIENT_ID] = resp["client_id"]
        if resp.get("refresh_token"):
            parsed[CONF_REFRESH_TOKEN] = resp["refresh_token"]
        ha_endpoint = resp.get("ha_endpoint")
        if not ha_endpoint:
            base = AUTH_LOGIN_URL.rsplit("/api/auth/login", 1)[0]
            ha_endpoint = f"{base}/api/ha"
        parsed[CONF_ENDPOINT] = _base_from_ha_endpoint(ha_endpoint)
        if not parsed.get(CONF_HOME_ID) and resp.get("home_id"):
            parsed[CONF_HOME_ID] = resp["home_id"]

        try:
            data = _apply_domain_schema(parsed)
        except vol.Invalid:
            return self.async_abort(reason="invalid_config")

        unique_id = data.get(CONF_HOME_ID) or f"{data[CONF_EMAIL]}@{data[CONF_ENDPOINT]}"
        await self.async_set_unique_id(unique_id, raise_on_progress=False)
        self._abort_if_unique_id_configured(updates=data)
        title = data.get(CONF_HOME_ID) or data[CONF_EMAIL]
        return self.async_create_entry(title=title, data=data)


class HaAiPushOptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        return await self.async_step_user(user_input)

    async def async_step_user(
        self, user_input: Dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        merged = {**self.config_entry.data, **self.config_entry.options}
        merged[CONF_INCLUDE] = _ensure_list(merged.get(CONF_INCLUDE))

        if user_input is not None:
            parsed = _normalize_user_input(user_input)
            parsed.setdefault(CONF_ENDPOINT, merged[CONF_ENDPOINT])
            password = parsed.pop(CONF_PASSWORD, "")
            rotate = parsed.get(CONF_ROTATE_TOKEN, False)

            if rotate and not password:
                errors["password"] = "password_required"
            else:
                try:
                    if password:
                        resp = await _async_login(self, parsed, password)
                        parsed[CONF_API_KEY] = resp["token"]
                        if resp.get("client_id"):
                            parsed[CONF_CLIENT_ID] = resp["client_id"]
                        if resp.get("refresh_token"):
                            parsed[CONF_REFRESH_TOKEN] = resp["refresh_token"]
                        ha_endpoint = resp.get("ha_endpoint")
                        if not ha_endpoint:
                            base = AUTH_LOGIN_URL.rsplit("/api/auth/login", 1)[0]
                            ha_endpoint = f"{base}/api/ha"
                        parsed[CONF_ENDPOINT] = _base_from_ha_endpoint(ha_endpoint)
                        if not parsed.get(CONF_HOME_ID) and resp.get("home_id"):
                            parsed[CONF_HOME_ID] = resp["home_id"]
                    else:
                        parsed[CONF_API_KEY] = merged.get(CONF_API_KEY)
                        parsed[CONF_CLIENT_ID] = merged.get(CONF_CLIENT_ID, "")
                        await _async_update_device(self, parsed)
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except AuthRequestError:
                    errors["base"] = "auth_request_failed"
                else:
                    try:
                        data = _apply_domain_schema(parsed)
                    except vol.Invalid:
                        errors["base"] = "invalid_config"
                    else:
                        data.pop(CONF_ROTATE_TOKEN, None)
                        self.hass.config_entries.async_update_entry(
                            self.config_entry, data=data
                        )
                        return self.async_create_entry(data={})

        form_values = (
            _options_form_defaults(merged) if user_input is None else user_input
        )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_options_schema(form_values),
            errors=errors,
        )
