"""EcoEdgeCoordinator — pulls thermostat profiles from EcoEdge GraphQL.

DataUpdateCoordinator replaces the hand-rolled ProfileFetcher (audit P1.1):
the 30-minute fallback poll, listener bookkeeping, and staleness tracking all
come from the platform now. Push flushes still request an early refresh after
a delay that gives the backend worker time to recompute.

The 8 queried fields are the frozen client contract — additive-only on the
backend; never rename or remove one here without a coordinated release.
"""
from __future__ import annotations

import asyncio
import logging
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
    GRAPHQL_URL,
)

_LOGGER = logging.getLogger(__name__)

_GRAPHQL_QUERY = """
query Profiles($homeId: String!) {
  currentProfiles(homeId: $homeId) {
    entityId
    predictionModel
    decisionSafeSetpoint
    rcKPerHour
    rcConfidence
    savingsPct7d
    mlBlendActive
    mlBlendedSetpoint
  }
}
"""


class EcoEdgeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator keyed by thermostat entity_id → profile dict."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        session: aiohttp.ClientSession,
        api_key: str,
        home_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {home_id}",
            update_interval=timedelta(minutes=DEFAULT_FALLBACK_POLL_MINUTES),
        )
        self._session = session
        self._api_key = api_key
        self._home_id = home_id
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

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            async with self._session.post(
                GRAPHQL_URL,
                json={"query": _GRAPHQL_QUERY, "variables": {"homeId": self._home_id}},
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 401:
                    raise ConfigEntryAuthFailed("EcoEdge rejected the access token")
                if resp.status >= 400:
                    raise UpdateFailed(f"EcoEdge returned HTTP {resp.status}")
                payload = await resp.json()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"EcoEdge unreachable: {err}") from err

        errors = payload.get("errors")
        if errors:
            message = str(errors[0].get("message", "unknown GraphQL error"))
            # graphene reports auth problems as errors on a 200 response
            if "unauthorized" in message.lower() or "identity" in message.lower():
                raise ConfigEntryAuthFailed(message)
            raise UpdateFailed(f"EcoEdge GraphQL error: {message}")

        profiles = (payload.get("data") or {}).get("currentProfiles") or []
        return {p["entityId"]: p for p in profiles if p.get("entityId")}
