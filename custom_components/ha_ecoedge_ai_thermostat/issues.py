"""Repair issues raised by the integration (v0.7.0).

- **yaml_deprecated** (audit M-02): `configuration.yaml` holds a plain-text
  password and used to log in at every start.
- **outdated** (audit M-08): the backend reports a newer minimum version than
  the one installed; an integration can't update itself.
"""
from __future__ import annotations

import logging

from awesomeversion import AwesomeVersion, AwesomeVersionException
from homeassistant.core import HomeAssistant
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
