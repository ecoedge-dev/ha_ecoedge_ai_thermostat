import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, Event, callback
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.typing import ConfigType

from .config_schema import DOMAIN_SCHEMA
from .const import (
    DOMAIN,
    CONF_ENDPOINT,
    CONF_API_KEY,
    CONF_INCLUDE,
    CONF_EXCLUDE,
    CONF_DEBOUNCE_SECONDS,
    CONF_TIMEOUT_SECONDS,
    CONF_HOME_ID,
    CONF_OUTDOOR_SENSOR,
    CONF_CLIENT_ID,
    CONF_REFRESH_TOKEN,
    AUTH_REFRESH_URL,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF,
    DEFAULT_REFRESH_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# Home Assistant validates configuration.yaml via this
CONFIG_SCHEMA = vol.Schema({DOMAIN: DOMAIN_SCHEMA}, extra=vol.ALLOW_EXTRA)


class TokenExpiredError(Exception):
    """Raised when the receiver returns HTTP 401."""


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_climate_entity(entity_id: str) -> bool:
    return entity_id.startswith("climate.")


def _is_temperature_sensor(hass: HomeAssistant, entity_id: str) -> bool:
    if not entity_id.startswith("sensor."):
        return False
    st = hass.states.get(entity_id)
    if st is None:
        return False
    attrs = st.attributes or {}
    device_class = (attrs.get("device_class") or "").lower()
    if device_class == "temperature":
        return True
    unit = (attrs.get("unit_of_measurement") or "").lower()
    return unit in ("c", "°c", "degc", "fahrenheit", "°f", "degf") or "celsius" in unit or "fahrenheit" in unit


def _filter_entities(
    hass: HomeAssistant,
    entity_id: str,
    include: List[str],
    exclude: List[str],
    extra_sensors: Optional[List[str]] = None,
) -> bool:
    if exclude and entity_id in exclude:
        return False
    if extra_sensors and entity_id in extra_sensors:
        return True
    if include:
        return entity_id in include
    return _is_climate_entity(entity_id) or _is_temperature_sensor(hass, entity_id)


def _state_to_payload(hass: HomeAssistant, entity_id: str) -> Optional[Dict[str, Any]]:
    st = hass.states.get(entity_id)
    if st is None:
        return None
    return {
        "entity_id": entity_id,
        "state": st.state,
        "attributes": dict(st.attributes),
        "last_changed": st.last_changed.isoformat() if st.last_changed else None,
        "last_updated": st.last_updated.isoformat() if st.last_updated else None,
    }


class PushClient:
    def __init__(
        self,
        hass: HomeAssistant,
        endpoint: str,
        api_key: Optional[str],
        refresh_token: Optional[str],
        client_id: Optional[str],
        timeout_seconds: int,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.session = aiohttp_client.async_get_clientsession(hass)

    async def post(self, payload: Dict[str, Any]) -> None:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.endpoint}/api/ha"

        async with self.session.post(url, json=payload, headers=headers, timeout=self.timeout) as resp:
            txt = await resp.text()
            if resp.status == 401:
                raise TokenExpiredError()
            if resp.status >= 300:
                raise RuntimeError(f"HTTP {resp.status}: {txt[:300]}")

    async def async_refresh(self) -> Optional[str]:
        """Exchange refresh_token for a new access token. Returns new token or None."""
        if not self.refresh_token:
            return None
        try:
            async with self.session.post(
                AUTH_REFRESH_URL,
                json={"refresh_token": self.refresh_token, "client_id": self.client_id or ""},
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REFRESH_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("Token refresh returned HTTP %s", resp.status)
                    return None
                data = await resp.json()
                new_token = data.get("token")
                if new_token:
                    self.api_key = new_token
                return new_token
        except Exception as exc:
            _LOGGER.warning("Token refresh request failed: %s", exc)
            return None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Handle YAML import and init the domain storage."""

    hass.data.setdefault(DOMAIN, {})

    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_IMPORT},
                data=config[DOMAIN],
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = HaAiPushRuntime(hass, entry)
    await runtime.async_setup()
    hass.data[DOMAIN][entry.entry_id] = runtime
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: "HaAiPushRuntime" | None = hass.data[DOMAIN].pop(entry.entry_id, None)
    if runtime:
        await runtime.async_unload()
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


class DebouncedQueue:
    def __init__(self, debounce_seconds: int):
        self.debounce_seconds = debounce_seconds
        self._lock = asyncio.Lock()
        self._pending: set[str] = set()
        self._task: Optional[asyncio.Task] = None

    async def add(self, entity_id: str, flush_cb):
        async with self._lock:
            self._pending.add(entity_id)
            if self._task and not self._task.done():
                self._task.cancel()
            self._task = asyncio.create_task(self._flush_later(flush_cb))

    async def _flush_later(self, flush_cb):
        await asyncio.sleep(self.debounce_seconds)
        async with self._lock:
            ids = sorted(self._pending)
            self._pending.clear()
        await flush_cb(ids)

    async def async_cancel(self) -> None:
        async with self._lock:
            task = self._task
            self._task = None
            self._pending.clear()
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


class HaAiPushRuntime:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._unsub: Optional[Callable[[], None]] = None
        self._queue: Optional[DebouncedQueue] = None
        self._initial_task: Optional[asyncio.Task] = None

    async def async_setup(self) -> None:
        conf = {**self.entry.data, **self.entry.options}

        endpoint = conf[CONF_ENDPOINT]
        api_key = conf.get(CONF_API_KEY)
        refresh_token = conf.get(CONF_REFRESH_TOKEN)
        client_id = conf.get(CONF_CLIENT_ID)
        include = conf.get(CONF_INCLUDE, [])
        exclude = conf.get(CONF_EXCLUDE, [])
        outdoor_sensor = conf.get(CONF_OUTDOOR_SENSOR)
        extra_sensors = [outdoor_sensor] if outdoor_sensor else []
        debounce_seconds = conf.get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS)
        timeout_seconds = conf.get(CONF_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS)
        home_id = conf.get(CONF_HOME_ID)

        client = PushClient(self.hass, endpoint, api_key, refresh_token, client_id, timeout_seconds)
        queue = DebouncedQueue(debounce_seconds)
        self._queue = queue

        async def flush(entity_ids: List[str]) -> None:
            items = []
            for eid in entity_ids:
                p = _state_to_payload(self.hass, eid)
                if p:
                    items.append(p)

            if not items:
                return

            payload = {
                "source": "homeassistant",
                "home_id": home_id or self.hass.config.location_name,
                "location_name": self.hass.config.location_name,
                "latitude": self.hass.config.latitude,
                "longitude": self.hass.config.longitude,
                "timezone": str(self.hass.config.time_zone),
                "ts_utc": _utc_iso(),
                "event": "state_change_batch",
                "count": len(items),
                "items": items,
            }

            for attempt in range(1, DEFAULT_RETRY_ATTEMPTS + 1):
                try:
                    await client.post(payload)
                    _LOGGER.debug(
                        "Pushed %s climate states to %s", len(items), client.endpoint
                    )
                    return
                except TokenExpiredError:
                    _LOGGER.debug("Access token expired, attempting refresh")
                    new_token = await client.async_refresh()
                    if new_token:
                        new_data = {**self.entry.data, CONF_API_KEY: new_token}
                        self.hass.config_entries.async_update_entry(self.entry, data=new_data)
                        try:
                            await client.post(payload)
                            _LOGGER.debug("Pushed %s states after token refresh", len(items))
                        except Exception as e2:
                            _LOGGER.warning("Push failed after token refresh: %s", e2)
                    else:
                        _LOGGER.warning(
                            "Token expired and refresh failed for %s — re-authentication required",
                            client.endpoint,
                        )
                    return
                except Exception as e:
                    if attempt == DEFAULT_RETRY_ATTEMPTS:
                        _LOGGER.warning("Push failed after %s retries: %s", DEFAULT_RETRY_ATTEMPTS, e)
                        return
                    await asyncio.sleep(DEFAULT_RETRY_BACKOFF * attempt)

        @callback
        def _handle_state_change(event: Event) -> None:
            # ignore removals / invalid events
            if event.data.get("new_state") is None:
                return

            entity_id = event.data.get("entity_id")
            if not entity_id:
                return
            if not _filter_entities(self.hass, entity_id, include, exclude, extra_sensors):
                return

            self.hass.async_create_task(queue.add(entity_id, flush))

        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, _handle_state_change)

        async def push_initial_snapshot() -> None:
            ids = [
                s.entity_id
                for s in self.hass.states.async_all()
                if _filter_entities(self.hass, s.entity_id, include, exclude, extra_sensors)
            ]
            await flush(ids)

        self._initial_task = self.hass.async_create_task(push_initial_snapshot())

        _LOGGER.info(
            "HA AI Push loaded. Endpoint=%s, include=%s, exclude=%s",
            endpoint,
            include,
            exclude,
        )

    async def async_unload(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._queue:
            await self._queue.async_cancel()
            self._queue = None
        if self._initial_task:
            self._initial_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._initial_task
            self._initial_task = None


async def async_get_options_flow(config_entry: ConfigEntry):
    from .config_flow import HaAiPushOptionsFlowHandler

    return HaAiPushOptionsFlowHandler(config_entry)
