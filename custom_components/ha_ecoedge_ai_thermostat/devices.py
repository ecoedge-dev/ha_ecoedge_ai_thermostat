"""Thermostat devices that EcoEdge no longer reports (v0.8.0, audit M-06).

The sensor platform creates one device (5 sensors) per thermostat in the
profile data. Before v0.8.0 such a device stayed forever once its thermostat
was gone (deselected, deleted in Home Assistant, archived by the backend).

- A thermostat counts as gone when a **successful** poll doesn't report it
  **and** its climate entity no longer exists in Home Assistant (deleted or
  renamed). A failed poll (backend down) never counts. A thermostat that still
  exists but isn't reported (e.g. no state change for a week, so EcoEdge has no
  recent reading) only gets unavailable sensors — it is never removed.
- After `ABSENT_REMOVE_AFTER` continuously gone, its device is removed.
  The times are kept in HA storage, so restarts don't reset the clock.
- A thermostat taken out of the include list, or excluded, is removed at setup.
- The user may delete a device by hand when its thermostat isn't reported.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
ABSENT_REMOVE_AFTER = timedelta(days=7)


def devices_key(entry_id: str) -> str:
    return f"{DOMAIN}.devices.{entry_id}"


def device_identifier(entry_id: str, thermostat: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_{thermostat}")


def thermostat_of(entry_id: str, device: dr.DeviceEntry) -> str | None:
    prefix = f"{entry_id}_"
    for domain, ident in device.identifiers:
        if domain == DOMAIN and ident.startswith(prefix):
            return ident[len(prefix):]
    return None


class DeviceTracker:
    """Tracks absent thermostats of one config entry and removes their devices."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store = Store(hass, STORAGE_VERSION, devices_key(entry_id))
        self.missing_since: dict[str, str] = {}      # thermostat → ISO time first missed
        self.registered: set[str] = set()            # thermostats with sensors in this run

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.missing_since = {k: v for k, v in (data.get("missing_since") or {}).items() if isinstance(v, str)}

    def _schedule_save(self) -> None:
        self._store.async_delay_save(lambda: {"missing_since": self.missing_since}, 2)

    def _devices(self) -> dict[str, dr.DeviceEntry]:
        registry = dr.async_get(self.hass)
        out = {}
        for device in dr.async_entries_for_config_entry(registry, self.entry_id):
            thermostat = thermostat_of(self.entry_id, device)
            if thermostat:
                out[thermostat] = device
        return out

    @callback
    def forget(self, thermostat: str) -> None:
        """Its device is gone: sensors are created again if it comes back."""
        self.registered.discard(thermostat)
        if self.missing_since.pop(thermostat, None) is not None:
            self._schedule_save()

    @callback
    def _remove(self, thermostat: str, device: dr.DeviceEntry, why: str) -> None:
        _LOGGER.info("EcoEdge: removing the device of %s (%s)", thermostat, why)
        dr.async_get(self.hass).async_update_device(device.id, remove_config_entry_id=self.entry_id)
        self.forget(thermostat)

    @callback
    def remove_deselected(self, include: list[str], exclude: list[str] | None = None) -> None:
        """At setup: drop devices of thermostats no longer selected, or excluded.

        An empty include list means "all climate entities"; then only the
        exclude list deselects.
        """
        exclude = exclude or []
        for thermostat, device in self._devices().items():
            if thermostat in exclude or (include and thermostat not in include):
                self._remove(thermostat, device, "no longer selected")

    @callback
    def update(self, present: set[str]) -> None:
        """After a successful poll: record absences, remove long-absent devices."""
        now = dt_util.utcnow()
        changed = False
        for thermostat in list(self.missing_since):
            if thermostat in present:
                del self.missing_since[thermostat]
                changed = True
        for thermostat, device in self._devices().items():
            if thermostat in present or self.hass.states.get(thermostat) is not None:
                if self.missing_since.pop(thermostat, None) is not None:
                    changed = True            # still exists in HA: unavailable, never removed
                continue
            since = self.missing_since.get(thermostat)
            if since is None:
                self.missing_since[thermostat] = now.isoformat()
                changed = True
                continue
            first_missed = dt_util.parse_datetime(since)
            if first_missed is None:                 # unreadable: restart the clock
                self.missing_since[thermostat] = now.isoformat()
                changed = True
            elif now - first_missed >= ABSENT_REMOVE_AFTER:
                self._remove(thermostat, device, f"absent since {since}")
        if changed:
            self._schedule_save()

    @property
    def absent(self) -> dict[str, str]:
        return dict(self.missing_since)

    async def async_save(self) -> None:
        await self._store.async_save({"missing_since": self.missing_since})

    async def async_remove_store(self) -> None:
        await self._store.async_remove()
