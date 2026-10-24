"""Push runtime: entity filter, debounced batches, token refresh, entry migration."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import (
    CLIENT_ID,
    DOMAIN,
    PUSH_URL,
    REFRESH_URL,
    calls_to,
    entry_data,
    make_entry,
    mock_backend,
    pushed_items,
    reauth_flows,
    responder,
    setup_entry,
    wait_for_flush,
)


def _states(hass: HomeAssistant) -> None:
    hass.states.async_set("climate.living", "heat", {"current_temperature": 20.5, "temperature": 21.0,
                                                    "hvac_action": "heating"})
    hass.states.async_set("climate.other", "heat", {"current_temperature": 18.0})
    hass.states.async_set("sensor.outdoor", "4.5", {"device_class": "temperature", "unit_of_measurement": "°C"})
    hass.states.async_set("sensor.fridge", "3.0", {"device_class": "temperature", "unit_of_measurement": "°C"})


async def test_startup_snapshot_pushes_only_selected_entities(hass, aioclient_mock):
    _states(hass)
    mock_backend(aioclient_mock)
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    body = calls_to(aioclient_mock, PUSH_URL)[0]
    assert {i["entity_id"] for i in body["items"]} == {"climate.living", "sensor.outdoor"}
    assert body["home_id"] == "Home" and body["event"] == "state_change_batch"
    assert body["count"] == 2 and body["integration_version"] and body["batch_id"]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_default_filter_is_climate_entities_plus_outdoor_sensor(hass, aioclient_mock):
    _states(hass)
    mock_backend(aioclient_mock)
    entry = make_entry(hass, include_entities=[])
    await setup_entry(hass, entry)

    assert {i["entity_id"] for i in pushed_items(aioclient_mock)} == {
        "climate.living", "climate.other", "sensor.outdoor"}             # never the fridge probe
    await hass.config_entries.async_unload(entry.entry_id)


async def test_state_changes_are_debounced_into_one_batch(hass, aioclient_mock):
    _states(hass)
    mock_backend(aioclient_mock)
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    aioclient_mock.mock_calls.clear()

    hass.states.async_set("climate.living", "heat", {"current_temperature": 20.6})
    hass.states.async_set("sensor.outdoor", "4.4", {"device_class": "temperature"})
    hass.states.async_set("sensor.fridge", "2.0", {"device_class": "temperature"})      # filtered out
    await wait_for_flush(hass)

    bodies = calls_to(aioclient_mock, PUSH_URL)
    assert len(bodies) == 1
    assert {i["entity_id"] for i in bodies[0]["items"]} == {"climate.living", "sensor.outdoor"}
    await hass.config_entries.async_unload(entry.entry_id)


async def test_expired_access_token_is_refreshed_once_and_the_push_retried(hass, aioclient_mock):
    _states(hass)
    mock_backend(aioclient_mock, push=responder({"status": 401}, {"json": {"ok": True}}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    assert len(calls_to(aioclient_mock, REFRESH_URL)) == 1
    assert calls_to(aioclient_mock, REFRESH_URL)[0] == {"refresh_token": "refresh-1", "client_id": CLIENT_ID}
    assert entry.data["api_key"] == "access-2"
    assert len(calls_to(aioclient_mock, PUSH_URL)) == 2                  # 401, then the retry
    assert reauth_flows(hass) == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_rejected_refresh_token_starts_reauth(hass, aioclient_mock):
    _states(hass)
    mock_backend(aioclient_mock, push=responder({"status": 401}), refresh=responder({"status": 401}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    assert len(reauth_flows(hass)) == 1
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.xfail(reason="audit H-04: any refresh failure (5xx, timeout) starts reauth; only 400/401 should")
async def test_transient_refresh_failure_does_not_start_reauth(hass, aioclient_mock):
    _states(hass)
    mock_backend(aioclient_mock, push=responder({"status": 401}), refresh=responder({"status": 503}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    assert reauth_flows(hass) == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_migration_uses_client_id_as_unique_id(hass, aioclient_mock):
    mock_backend(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data(), version=3, unique_id="Home", title="Home")
    entry.add_to_hass(hass)
    await setup_entry(hass, entry)

    assert entry.version == 5 and entry.unique_id == CLIENT_ID


@pytest.mark.parametrize("old, new", [(3, 20), (10, 10)])
async def test_migration_v5_moves_the_old_debounce_default_to_the_batching_window(hass, aioclient_mock, old, new):
    mock_backend(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data(debounce_seconds=old), version=4,
                            unique_id=CLIENT_ID, title="Home")
    entry.add_to_hass(hass)
    await setup_entry(hass, entry)

    assert entry.version == 5 and entry.data["debounce_seconds"] == new         # a chosen value is kept
    await hass.config_entries.async_unload(entry.entry_id)
