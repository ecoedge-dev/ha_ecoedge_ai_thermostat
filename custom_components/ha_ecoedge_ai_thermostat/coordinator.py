"""EcoEdgeCoordinator — pulls thermostat profiles from EcoEdge GraphQL.

DataUpdateCoordinator replaces the hand-rolled ProfileFetcher (audit P1.1):
the 30-minute fallback poll, listener bookkeeping, and staleness tracking all
come from the platform now. Push flushes still request an early refresh after
a delay that gives the backend worker time to recompute.

The 8 queried fields are the frozen client contract — additive-only on the
backend; never rename or remove one here without a coordinated release.

v0.7.0 (audit H-06, M-10): profiles are read with `deviceProfiles`, scoped to
this installation's own device token, instead of `currentProfiles(homeId)`,
which resolved the home by its mutable name. A backend without the new field
(older or self-hosted) gets the old query.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_FALLBACK_POLL_MINUTES,
    DEFAULT_FETCH_DELAY_SECONDS,
    DOMAIN,
    PATH_GRAPHQL,
    api_url,
)
from .delivery import RefreshOutcome

_LOGGER = logging.getLogger(__name__)

_FIELDS = """
    entityId
    predictionModel
    decisionSafeSetpoint
    rcKPerHour
    rcConfidence
    savingsPct7d
    mlBlendActive
    mlBlendedSetpoint
"""
_DEVICE_QUERY = "query DeviceProfiles {\n  deviceProfiles {" + _FIELDS + "  }\n}\n"
_LEGACY_QUERY = "query Profiles($homeId: String!) {\n  currentProfiles(homeId: $homeId) {" + _FIELDS + "  }\n}\n"


def _is_unknown_field(message: str) -> bool:
    return "deviceProfiles" in message and "Cannot query field" in message


class EcoEdgeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator keyed by thermostat entity_id → profile dict."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: aiohttp.ClientSession,
        api_key: str,
        home_id: str | None,
        renew: Callable[[str | None], Awaitable[RefreshOutcome]] | None = None,
        api_base: Callable[[], str | None] | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {home_id or entry.title}",
            update_interval=timedelta(minutes=DEFAULT_FALLBACK_POLL_MINUTES),
        )
        self._session = session
        self._api_key = api_key
        self._home_id = home_id
        # v0.6.1 (audit H-05): the runtime's shared token refresh — a 401 on the
        # poll renews the access token instead of going straight to reauth
        self._renew = renew
        # v0.7.0 (H-07): read on every poll — a token refresh may move the base
        self._api_base = api_base or (lambda: None)
        self._legacy = False            # backend without deviceProfiles
        self._delayed_task: asyncio.Task | None = None

    def update_token(self, new_token: str) -> None:
        """Called by the push runtime after a token refresh."""
        self._api_key = new_token

    def schedule_refresh_after_push(self) -> None:
        """Refresh shortly after a push cycle; re-arms if another push lands first."""
        if self._delayed_task and not self._delayed_task.done():
            self._delayed_task.cancel()
        self._delayed_task = self.hass.async_create_background_task(
            self._delayed_refresh(), "ecoedge_profile_fetch"
        )

    async def _delayed_refresh(self) -> None:
        try:
            await asyncio.sleep(DEFAULT_FETCH_DELAY_SECONDS)
        except asyncio.CancelledError:
            return
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        if self._delayed_task and not self._delayed_task.done():
            self._delayed_task.cancel()
        await super().async_shutdown()

    def _request(self) -> dict[str, Any]:
        if self._legacy:
            return {"query": _LEGACY_QUERY, "variables": {"homeId": self._home_id or ""}}
        return {"query": _DEVICE_QUERY}

    async def _post_query(self) -> tuple[int, dict[str, Any] | None]:
        try:
            async with self._session.post(
                api_url(self._api_base(), PATH_GRAPHQL),
                json=self._request(),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 400:          # graphene: a query the schema rejects
                    try:
                        body = await resp.json(content_type=None)
                    except ValueError:
                        return resp.status, None
                    return (200, body) if isinstance(body, dict) and body.get("errors") else (resp.status, None)
                if resp.status >= 400:
                    return resp.status, None
                return resp.status, await resp.json()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"EcoEdge unreachable: {err}") from err

    async def _async_update_data(self) -> dict[str, Any]:
        status, payload = await self._post_query()
        if status == 401 and self._renew is not None:
            outcome = await self._renew(self._api_key)
            if outcome is RefreshOutcome.UNAVAILABLE:
                raise UpdateFailed("EcoEdge token refresh unavailable — will retry")
            if outcome is RefreshOutcome.NEW_TOKEN:   # update_token() already applied
                status, payload = await self._post_query()
        if status == 401:
            raise ConfigEntryAuthFailed("EcoEdge rejected the access token")
        if status >= 400 or payload is None:
            raise UpdateFailed(f"EcoEdge returned HTTP {status}")

        errors = payload.get("errors")
        if errors and not self._legacy and _is_unknown_field(str(errors[0].get("message", ""))):
            _LOGGER.info("EcoEdge backend has no deviceProfiles yet — using currentProfiles")
            if not self._home_id:
                # audit M-10: the old query needs a home name; without one it is empty
                _LOGGER.warning("EcoEdge entry has no home_id — the legacy profile query will be empty")
            self._legacy = True
            return await self._async_update_data()
        if errors:
            message = str(errors[0].get("message", "unknown GraphQL error"))
            # graphene reports auth problems as errors on a 200 response
            if "unauthorized" in message.lower() or "identity" in message.lower():
                raise ConfigEntryAuthFailed(message)
            raise UpdateFailed(f"EcoEdge GraphQL error: {message}")

        data = payload.get("data") or {}
        profiles = data.get("currentProfiles" if self._legacy else "deviceProfiles") or []
        return {p["entityId"]: p for p in profiles if p.get("entityId")}
