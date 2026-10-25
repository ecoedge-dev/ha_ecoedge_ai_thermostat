"""Repair issues raised by the integration (v0.7.0).

- **yaml_deprecated** (audit M-02): `configuration.yaml` holds a plain-text
  password and used to log in at every start.
- **outdated** (audit M-08): the backend reports a newer minimum version than
  the one installed; an integration can't update itself.
- **backend_unreachable / readings_at_risk / readings_dropped** (v0.8.0,
  audit M-07): see HealthMonitor.
"""
from __future__ import annotations

import logging
import time

from awesomeversion import AwesomeVersion, AwesomeVersionException
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, RELEASES_URL

_LOGGER = logging.getLogger(__name__)

ISSUE_YAML = "yaml_deprecated"


def outdated_issue_id(entry_id: str) -> str:
    return f"outdated_{entry_id}"


def async_raise_yaml_deprecated(hass: HomeAssistant) -> None:
    ir.async_create_issue(
        hass, DOMAIN, ISSUE_YAML,
        is_fixable=False, is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_YAML,
    )


def is_outdated(installed: str, minimum: str | None) -> bool:
    if not minimum:
        return False
    try:
        return AwesomeVersion(installed) < AwesomeVersion(minimum)
    except AwesomeVersionException:
        _LOGGER.debug("Unparsable version comparison: %s < %s", installed, minimum)
        return False


def async_check_version(hass: HomeAssistant, entry_id: str, installed: str, minimum: str | None) -> None:
    """Raise or clear the 'outdated' issue for one entry."""
    issue_id = outdated_issue_id(entry_id)
    if is_outdated(installed, minimum):
        ir.async_create_issue(
            hass, DOMAIN, issue_id,
            is_fixable=False, is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="outdated",
            translation_placeholders={"version": installed, "min_version": str(minimum)},
            learn_more_url=RELEASES_URL,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


# ── v0.8.0 (audit M-07): delivery and poll health ─────────────────────────────
UNREACHABLE_AFTER_SECONDS = 24 * 3600
AT_RISK_FRACTION = 0.8                  # of the outbox's reading cap
AT_RISK_AGE_SECONDS = 20 * 3600         # batches are dropped at 24 h
CLEAR_FRACTION = 0.5
CLEAR_AGE_SECONDS = 12 * 3600
DROPPED_CLEAR_AFTER_SECONDS = 24 * 3600
HEALTH_ISSUES = ("backend_unreachable", "readings_at_risk", "readings_dropped")


def health_issue_id(kind: str, entry_id: str) -> str:
    return f"{kind}_{entry_id}"


class HealthMonitor:
    """Raises and clears the delivery/poll Repair issues of one entry.

    `check()` runs every few minutes. Each issue clears itself once the
    condition is gone; readings_at_risk has hysteresis so it doesn't flap.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, outbox, coordinator, started_at: float) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.outbox = outbox
        self.coordinator = coordinator
        self.started_at = started_at
        self.at_risk = False
        self._dropped_seen = outbox.dropped_overflow + outbox.dropped_rejected
        self._last_drop_at: float | None = None
        self.raised: set[str] = set()

    def _set(self, kind: str, active: bool, placeholders: dict[str, str] | None = None) -> None:
        issue_id = health_issue_id(kind, self.entry_id)
        if active:
            ir.async_create_issue(
                self.hass, DOMAIN, issue_id,
                is_fixable=False, is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=kind,
                translation_placeholders=placeholders or {},
            )
            self.raised.add(kind)
        elif kind in self.raised:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            self.raised.discard(kind)

    @callback
    def check(self) -> None:
        now = time.time()
        ob, co = self.outbox, self.coordinator

        # EcoEdge unreachable: nothing delivered, or no successful poll, for > 24 h
        delivered_ref = ob.last_delivered_at or self.started_at
        delivery_stalled = bool(ob.batches) and not ob.paused and now - delivered_ref > UNREACHABLE_AFTER_SECONDS
        poll_ref = co.last_success_at.timestamp() if co.last_success_at else self.started_at
        poll_stalled = not co.last_update_success and now - poll_ref > UNREACHABLE_AFTER_SECONDS
        since = min(delivered_ref if delivery_stalled else now, poll_ref if poll_stalled else now)
        self._set("backend_unreachable", delivery_stalled or poll_stalled,
                  {"hours": str(int((now - since) // 3600))})

        # readings at risk: outbox close to its cap or its oldest batch close to 24 h
        fraction = ob.pending_items / ob.max_items if ob.max_items else 0.0
        age = ob.oldest_age_seconds or 0.0
        if not self.at_risk and (fraction >= AT_RISK_FRACTION or age >= AT_RISK_AGE_SECONDS):
            self.at_risk = True
        elif self.at_risk and fraction < CLEAR_FRACTION and age < CLEAR_AGE_SECONDS:
            self.at_risk = False
        self._set("readings_at_risk", self.at_risk,
                  {"pending": str(ob.pending_items), "max": str(ob.max_items), "hours": str(int(age // 3600))})

        # readings dropped since the last check; clears after a day without new drops
        dropped = ob.dropped_overflow + ob.dropped_rejected
        if dropped > self._dropped_seen:
            self._last_drop_at = now
            self._dropped_seen = dropped
        recent = self._last_drop_at is not None and now - self._last_drop_at < DROPPED_CLEAR_AFTER_SECONDS
        self._set("readings_dropped", recent,
                  {"overflow": str(ob.dropped_overflow), "rejected": str(ob.dropped_rejected)})

    @callback
    def clear_all(self) -> None:
        for kind in HEALTH_ISSUES:
            ir.async_delete_issue(self.hass, DOMAIN, health_issue_id(kind, self.entry_id))
        self.raised.clear()
