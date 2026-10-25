"""System information (v0.8.0, audit M-07): Settings → System → Repairs →
⋮ → System information → EcoEdge AI Thermostat."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import CONF_API_BASE, CONF_MIN_VERSION, DOMAIN, api_url


@callback
def async_register(hass: HomeAssistant, register: system_health.SystemHealthRegistration) -> None:
    register.async_register_info(system_health_info)


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if getattr(e, "runtime_data", None)]
    if not entries:
        return {"configured": False}
    runtime = entries[0].runtime_data
    conf = {**entries[0].data, **entries[0].options}
    outbox, coordinator = runtime.outbox, runtime.coordinator
    last_delivery = outbox.last_delivered_at if outbox else None
    return {
        "api_reachable": system_health.async_check_can_reach_url(
            hass, api_url(conf.get(CONF_API_BASE), "/api/health/")
        ),
        "version": runtime.integration_version,
        "min_supported_version": conf.get(CONF_MIN_VERSION) or "unknown",
        "readings_waiting": sum(e.runtime_data.outbox.pending_items for e in entries if e.runtime_data.outbox),
        "last_delivery": datetime.fromtimestamp(last_delivery, UTC).isoformat() if last_delivery else "never",
        "last_profile_poll": (
            coordinator.last_success_at.isoformat() if coordinator and coordinator.last_success_at else "never"
        ),
    }
