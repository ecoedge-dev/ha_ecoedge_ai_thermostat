"""Shared helpers used by both the runtime and the config flow."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .const import CONF_API_BASE, CONF_MIN_VERSION


def sync_fingerprint(thermostats: list[str], outdoor_sensors: list[str]) -> str:
    """Stable hash of the entity lists sent to /api/device/sync-entities.

    Stored in the config entry (audit P1.6) so an unchanged configuration —
    e.g. every HA restart — produces no sync call at all, removing the routine
    opportunities for the backend to archive profiles.
    """
    blob = json.dumps(
        {"thermostats": sorted(thermostats), "outdoor_sensors": sorted(outdoor_sensors)},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def contract_updates(resp: dict[str, Any]) -> dict[str, str]:
    """Entry fields from the client contract in a login/refresh response (v0.7.0).

    Only well-formed values are taken; a backend without the contract (older or
    self-hosted) leaves the entry as it is.
    """
    updates: dict[str, str] = {}
    base = resp.get("api_base")
    if isinstance(base, str) and base.startswith(("https://", "http://")):
        updates[CONF_API_BASE] = base.rstrip("/")
    min_version = resp.get("min_supported_version")
    if isinstance(min_version, str) and min_version.strip():
        updates[CONF_MIN_VERSION] = min_version.strip()
    return updates
