"""Sensors and the profile poller (EcoEdgeCoordinator)."""
from __future__ import annotations

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import (
    DOMAIN,
    GRAPHQL_URL,
    PROFILE,
    REFRESH_URL,
    calls_to,
    make_entry,
    mock_backend,
    reauth_flows,
    responder,
    setup_entry,
)


def _sensor_state(hass: HomeAssistant, entry_id: str, key: str, thermostat: str = "climate.living"):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry_id}_{thermostat}_{key}")
    assert entity_id, f"no {key} sensor registered for {thermostat}"
    return hass.states.get(entity_id)


async def test_five_sensors_per_thermostat_reflect_the_profile(hass, aioclient_mock):
    mock_backend(aioclient_mock)
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    assert _sensor_state(hass, entry.entry_id, "ai_setpoint").state == "20.5"
    assert _sensor_state(hass, entry.entry_id, "model").state == "rc"
    k = _sensor_state(hass, entry.entry_id, "k_per_hour")
    assert float(k.state) == pytest.approx(0.0123) and k.attributes["unit_of_measurement"] == "1/h"   # #10
    assert k.attributes["loss_pct_per_hour"] == 1.23 and k.attributes["time_constant_h"] == 81.0   # 1 / 0.01234
    assert k.attributes["fitted_by"] == "rc"
    assert float(_sensor_state(hass, entry.entry_id, "confidence").state) == pytest.approx(82.3)
    assert float(_sensor_state(hass, entry.entry_id, "saving_est_7d").state) == pytest.approx(9.4)
    query = calls_to(aioclient_mock, GRAPHQL_URL)[0]
    assert "deviceProfiles" in query["query"] and "variables" not in query     # H-06: scoped by token, not name


async def test_ml_blend_overrides_setpoint_and_model(hass, aioclient_mock):
    mock_backend(aioclient_mock, profiles=[{**PROFILE, "mlBlendActive": True, "mlBlendedSetpoint": 19.84}])
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    setpoint = _sensor_state(hass, entry.entry_id, "ai_setpoint")
    assert setpoint.state == "19.8" and setpoint.attributes["ml_blend_active"] is True
    assert _sensor_state(hass, entry.entry_id, "model").state == "ml"


async def test_sensors_go_unavailable_when_the_poll_fails(hass, aioclient_mock):
    mock_backend(aioclient_mock, graphql=responder({"json": {"data": {"deviceProfiles": [PROFILE]}}},
                                                   {"status": 502}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    assert _sensor_state(hass, entry.entry_id, "ai_setpoint").state == "20.5"

    coordinator = entry.runtime_data.coordinator
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _sensor_state(hass, entry.entry_id, "ai_setpoint").state == STATE_UNAVAILABLE


async def test_expired_access_token_on_poll_is_refreshed_not_reauthed(hass, aioclient_mock):
    """H-05 (was xfail): the poll renews the access token through the shared refresh."""
    mock_backend(aioclient_mock, graphql=responder({"status": 401},
                                                   {"json": {"data": {"deviceProfiles": [PROFILE]}}}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    assert reauth_flows(hass) == []
    assert _sensor_state(hass, entry.entry_id, "ai_setpoint").state == "20.5"
    assert entry.data["api_key"] == "access-2"                   # persisted for the next start
    assert len(calls_to(aioclient_mock, REFRESH_URL)) == 1


async def test_rejected_refresh_on_poll_starts_reauth(hass, aioclient_mock):
    mock_backend(aioclient_mock, graphql=responder({"status": 401}), refresh=responder({"status": 401}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    assert len(reauth_flows(hass)) == 1


async def test_unavailable_refresh_on_poll_does_not_start_reauth(hass, aioclient_mock):
    mock_backend(aioclient_mock, graphql=responder({"status": 401}), refresh=responder({"status": 502}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    coordinator = entry.runtime_data.coordinator
    assert reauth_flows(hass) == [] and coordinator.last_update_success is False
