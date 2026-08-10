"""Shared helpers used by both the runtime and the config flow."""
from __future__ import annotations

import hashlib
import json


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
