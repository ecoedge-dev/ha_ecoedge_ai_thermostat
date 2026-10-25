"""v0.8.0: diagnostics, delivery/poll repair issues, system health, entity
categories (audit M-07, L-04). Device lifecycle (M-06) is in test_devices.py."""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.ha_ecoedge_ai_thermostat.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.ha_ecoedge_ai_thermostat.system_health import system_health_info

from .conftest import (
    BASE,
    CLIENT_ID,
    DOMAIN,
    PROFILE,
    make_entry,
    mock_backend,
    responder,
    setup_entry,
    setup_entry_nowait,
)


def _climate(hass: HomeAssistant) -> None:
    hass.states.async_set("climate.living", "heat", {"current_temperature": 20.5, "temperature": 21.0})


def _switchable(state: dict) -> callable:
    """A backend that is down until state["up"] is set."""
    async def side_effect(method, url, data):
        if state["up"]:
            body = {"data": {"deviceProfiles": [PROFILE]}} if "graphql" in str(url) else {"ok": True}
            return AiohttpClientMockResponse(method, URL(url), json=body)
        return AiohttpClientMockResponse(method, URL(url), status=503)
    return side_effect


def _issue(hass, kind, entry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{kind}_{entry.entry_id}")


async def _settle(hass, seconds: float = 0.3):
    await asyncio.sleep(seconds)
    await hass.async_block_till_done()


# ── diagnostics ───────────────────────────────────────────────────────────────
async def test_diagnostics_are_useful_and_contain_no_secrets(hass, aioclient_mock):
    _climate(hass)
    mock_backend(aioclient_mock)
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    dump = json.dumps(diag, default=str)
    for secret in ("access-1", "refresh-1", "anna@example.test", CLIENT_ID):
        assert secret not in dump, secret
    assert diag["integration"]["api_base"] == BASE and diag["integration"]["version"]
    assert diag["outbox"]["readings_pending"] == 0 and diag["outbox"]["last_delivered_at"]
    assert diag["profile_poll"]["last_update_success"] is True
    assert diag["profile_poll"]["thermostats"] == ["climate.living"]
    assert diag["devices"]["with_sensors"] == ["climate.living"]


# ── repair issues ─────────────────────────────────────────────────────────────
async def test_unreachable_for_a_day_raises_an_issue_that_clears_on_recovery(hass, aioclient_mock):
    _climate(hass)
    state = {"up": False}
    mock_backend(aioclient_mock, push=_switchable(state), graphql=_switchable(state))
    entry = make_entry(hass)
    await setup_entry_nowait(hass, entry)
    health = entry.runtime_data.health

    health.check()
    assert _issue(hass, "backend_unreachable", entry) is None          # not yet a day

    health.started_at -= 25 * 3600          # a day without any delivery or successful poll
    health.check()
    issue = _issue(hass, "backend_unreachable", entry)
    assert issue is not None and int(issue.translation_placeholders["hours"]) >= 24

    state["up"] = True
    await entry.runtime_data.coordinator.async_refresh()
    await _settle(hass)                                                # the outbox retries and delivers
    health.check()
    assert _issue(hass, "backend_unreachable", entry) is None


async def test_outbox_close_to_its_cap_raises_readings_at_risk(hass, aioclient_mock):
    _climate(hass)
    state = {"up": False}
    mock_backend(aioclient_mock, push=_switchable(state))
    entry = make_entry(hass)
    await setup_entry_nowait(hass, entry)
    runtime = entry.runtime_data
    runtime.outbox.max_items = 10
    runtime.outbox.enqueue([{"entity_id": "climate.living", "state": "heat"}] * 7)   # 8 of 10 with the snapshot

    runtime.health.check()
    assert _issue(hass, "readings_at_risk", entry) is not None

    state["up"] = True
    await _settle(hass)
    runtime.health.check()
    assert _issue(hass, "readings_at_risk", entry) is None


async def test_rejected_readings_raise_readings_dropped_for_a_day(hass, aioclient_mock, freezer):
    _climate(hass)
    mock_backend(aioclient_mock, push=responder({"status": 400}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    health = entry.runtime_data.health

    health.check()
    issue = _issue(hass, "readings_dropped", entry)
    assert issue is not None and issue.translation_placeholders["rejected"] == "1"

    freezer.tick(timedelta(hours=25))
    health.check()
    assert _issue(hass, "readings_dropped", entry) is None


async def test_unload_clears_the_health_issues(hass, aioclient_mock):
    _climate(hass)
    mock_backend(aioclient_mock, push=responder({"status": 400}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    entry.runtime_data.health.check()
    assert _issue(hass, "readings_dropped", entry) is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert _issue(hass, "readings_dropped", entry) is None


# ── system health ─────────────────────────────────────────────────────────────
async def test_system_health_reports_reachability_and_delivery(hass, aioclient_mock):
    _climate(hass)
    mock_backend(aioclient_mock)
    aioclient_mock.get(f"{BASE}/api/health/", json={"ok": True})
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    info = await system_health_info(hass)
    assert await info["api_reachable"] == "ok"
    assert info["version"] and info["readings_waiting"] == 0
    assert info["last_delivery"] != "never" and info["last_profile_poll"] != "never"


# ── entity categories ─────────────────────────────────────────────────────────
async def test_model_k_and_confidence_are_diagnostic_entities(hass, aioclient_mock):
    mock_backend(aioclient_mock)
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    registry = er.async_get(hass)

    def category(key):
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_climate.living_{key}")
        return registry.async_get(entity_id).entity_category

    assert {k: category(k) for k in ("model", "k_per_hour", "confidence")} == dict.fromkeys(
        ("model", "k_per_hour", "confidence"), EntityCategory.DIAGNOSTIC)
    assert category("ai_setpoint") is None and category("saving_est_7d") is None
