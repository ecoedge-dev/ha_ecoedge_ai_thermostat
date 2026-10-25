"""Diagnostics download (v0.8.0, audit M-07).

Settings → Devices & services → EcoEdge → ⋮ → Download diagnostics. Tokens,
the account e-mail and the device id are redacted; nothing here is sent
anywhere by the integration.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from . import EcoEdgeConfigEntry
from .const import (
    CONF_API_BASE,
    CONF_API_KEY,
    CONF_CLIENT_ID,
    CONF_EMAIL,
    CONF_ENDPOINT,
    CONF_MIN_VERSION,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    DEFAULT_API_BASE,
    DOMAIN,
)

TO_REDACT = {CONF_API_KEY, CONF_REFRESH_TOKEN, CONF_EMAIL, CONF_PASSWORD, CONF_CLIENT_ID, "unique_id"}


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts else None


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: EcoEdgeConfigEntry) -> dict[str, Any]:
    runtime = entry.runtime_data
    conf = {**entry.data, **entry.options}
    outbox, coordinator = runtime.outbox, runtime.coordinator

    issues = [
        issue_id for (domain, issue_id) in ir.async_get(hass).issues
        if domain == DOMAIN and (issue_id.endswith(entry.entry_id) or issue_id == "yaml_deprecated")
    ]
    return {
        "entry": async_redact_data(
            {"version": entry.version, "title": entry.title, "unique_id": entry.unique_id,
             "data": dict(entry.data), "options": dict(entry.options)},
            TO_REDACT,
        ),
        "integration": {
            "version": runtime.integration_version,
            "api_base": conf.get(CONF_API_BASE) or DEFAULT_API_BASE,
            "receiver": conf.get(CONF_ENDPOINT),
            "min_supported_version": conf.get(CONF_MIN_VERSION),
            "legacy_profile_query": coordinator.legacy_query if coordinator else None,
        },
        "outbox": None if outbox is None else {
            "batches_pending": len(outbox.batches),
            "readings_pending": outbox.pending_items,
            "oldest_batch_age_s": round(outbox.oldest_age_seconds) if outbox.batches else None,
            "max_readings": outbox.max_items,
            "dropped_overflow": outbox.dropped_overflow,
            "dropped_rejected": outbox.dropped_rejected,
            "paused_for_reauth": outbox.paused,
            "last_result": outbox.last_result,
            "last_delivered_at": _iso(outbox.last_delivered_at),
        },
        "profile_poll": None if coordinator is None else {
            "last_update_success": coordinator.last_update_success,
            "last_success_at": coordinator.last_success_at.isoformat() if coordinator.last_success_at else None,
            "last_exception": repr(coordinator.last_exception) if coordinator.last_exception else None,
            "thermostats": sorted(coordinator.data or {}),
        },
        "devices": {
            "with_sensors": sorted(runtime.devices.registered),
            "absent_since": runtime.devices.absent,
        },
        "repair_issues": sorted(issues),
    }
