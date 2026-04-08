"""ProfileFetcher — pulls thermostat profile data from EcoEdge GraphQL.

Triggered after each successful push (with a delay to let the worker finish),
plus a 30-minute fallback poll to handle HA restarts and idle periods.
"""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Callable, Dict, Optional

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import DEFAULT_FETCH_DELAY_SECONDS, DEFAULT_FALLBACK_POLL_MINUTES, GRAPHQL_URL

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


class ProfileFetcher:
    """Fetches thermostat profiles from EcoEdge GraphQL after each push cycle.

    Usage:
        fetcher = ProfileFetcher(hass, endpoint, api_key, session)
        await fetcher.async_setup()          # register fallback poll
        fetcher.schedule_fetch_after_push()  # call after each successful flush
        ...
        await fetcher.async_unload()

    Subscribers receive the full data dict {entity_id: profile_dict} on every
    successful fetch via the listener callbacks registered with add_listener().
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api_key: str,
        home_id: str,
        session: aiohttp.ClientSession,
        fetch_delay: int = DEFAULT_FETCH_DELAY_SECONDS,
    ) -> None:
        self._hass = hass
        self._api_key = api_key
        self._home_id = home_id
        self._session = session
        self._fetch_delay = fetch_delay
        self._fetch_task: Optional[asyncio.Task] = None
        self._unsub_interval: Optional[Callable[[], None]] = None
        self._listeners: list[Callable[[Dict[str, Any]], None]] = []
        self.data: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_token(self, new_token: str) -> None:
        """Called by __init__.py after a token refresh so fetcher uses new token."""
        self._api_key = new_token

    def add_listener(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        """Register a callback invoked with fresh data after every successful fetch."""
        self._listeners.append(cb)

    def schedule_fetch_after_push(self) -> None:
        """Schedule a delayed fetch. Resets the timer if called again before it fires."""
        if self._fetch_task and not self._fetch_task.done():
            self._fetch_task.cancel()
        self._fetch_task = self._hass.async_create_background_task(
            self._delayed_fetch(), "ecoedge_profile_fetch"
        )

    async def async_setup(self) -> None:
        """Register the 30-minute fallback poll and schedule a first fetch."""
        self._unsub_interval = async_track_time_interval(
            self._hass,
            self._fallback_poll,
            timedelta(minutes=DEFAULT_FALLBACK_POLL_MINUTES),
        )
        # Schedule first fetch in background — never block HA bootstrap.
        self._fetch_task = self._hass.async_create_background_task(
            self._do_fetch(), "ecoedge_initial_profile_fetch"
        )

    async def async_unload(self) -> None:
        """Cancel all pending tasks and remove the interval listener."""
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None
        if self._fetch_task and not self._fetch_task.done():
            self._fetch_task.cancel()
            try:
                await self._fetch_task
            except asyncio.CancelledError:
                pass
            self._fetch_task = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _delayed_fetch(self) -> None:
        try:
            await asyncio.sleep(self._fetch_delay)
            await self._do_fetch()
        except asyncio.CancelledError:
            pass

    async def _fallback_poll(self, _now=None) -> None:
        await self._do_fetch()

    async def _do_fetch(self) -> None:
        url = GRAPHQL_URL
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with self._session.post(
                url,
                json={"query": _GRAPHQL_QUERY, "variables": {"homeId": self._home_id}},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 401:
                    _LOGGER.warning(
                        "EcoEdge profile fetch: token expired — re-authenticate in integration settings"
                    )
                    return
                if resp.status >= 400:
                    _LOGGER.warning(
                        "EcoEdge profile fetch: HTTP %s from %s", resp.status, url
                    )
                    return
                payload = await resp.json()
        except aiohttp.ClientError as err:
            _LOGGER.warning("EcoEdge profile fetch: network error: %s", err)
            return
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("EcoEdge profile fetch: unexpected error: %s", err)
            return

        profiles = (payload.get("data") or {}).get("currentProfiles") or []
        if not profiles:
            _LOGGER.debug("EcoEdge profile fetch: no profiles returned")
            return

        self.data = {p["entityId"]: p for p in profiles if p.get("entityId")}
        _LOGGER.debug("EcoEdge profile fetch: updated %d profile(s)", len(self.data))

        for cb in self._listeners:
            try:
                cb(self.data)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("EcoEdge profile listener error: %s", err)
