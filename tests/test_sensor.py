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
    assert float(_sensor_state(hass, entry.entry_id, "k_per_hour").state) == pytest.approx(0.0123)
    assert float(_sensor_state(hass, entry.entry_id, "confidence").state) == pytest.approx(82.3)
    assert float(_sensor_state(hass, entry.entry_id, "saving_est_7d").state) == pytest.approx(9.4)
    query = calls_to(aioclient_mock, GRAPHQL_URL)[0]
    assert query["variables"] == {"homeId": "Home"}


async def test_ml_blend_overrides_setpoint_and_model(hass, aioclient_mock):
    mock_backend(aioclient_mock, profiles=[{**PROFILE, "mlBlendActive": True, "mlBlendedSetpoint": 19.84}])
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    setpoint = _sensor_state(hass, entry.entry_id, "ai_setpoint")
    assert setpoint.state == "19.8" and setpoint.attributes["ml_blend_active"] is True
    assert _sensor_state(hass, entry.entry_id, "model").state == "ml"


async def test_sensors_go_unavailable_when_the_poll_fails(hass, aioclient_mock):
    mock_backend(aioclient_mock, graphql=responder({"json": {"data": {"currentProfiles": [PROFILE]}}},
                                                   {"status": 502}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    assert _sensor_state(hass, entry.entry_id, "ai_setpoint").state == "20.5"

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _sensor_state(hass, entry.entry_id, "ai_setpoint").state == STATE_UNAVAILABLE


@pytest.mark.xfail(reason="audit H-05: a GraphQL 401 goes straight to reauth; the refresh token is never tried")
async def test_expired_access_token_on_poll_is_refreshed_not_reauthed(hass, aioclient_mock):
    mock_backend(aioclient_mock, graphql=responder({"status": 401},
                                                   {"json": {"data": {"currentProfiles": [PROFILE]}}}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    assert reauth_flows(hass) == []
    assert _sensor_state(hass, entry.entry_id, "ai_setpoint").state == "20.5"
