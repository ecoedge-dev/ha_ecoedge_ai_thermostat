"""v0.8.0 (audit M-06): devices of thermostats EcoEdge no longer reports."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.ha_ecoedge_ai_thermostat import async_remove_config_entry_device
from custom_components.ha_ecoedge_ai_thermostat.coordinator import EcoEdgeCoordinator

from .conftest import DOMAIN, PROFILE, make_entry, mock_backend, responder, setup_entry

PRESENT = {"json": {"data": {"deviceProfiles": [PROFILE]}}}
ABSENT = {"json": {"data": {"deviceProfiles": []}}}
DOWN = {"status": 502}


def _device(hass, entry, thermostat="climate.living"):
    ident = (DOMAIN, f"{entry.entry_id}_{thermostat}")
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return next((d for d in devices if ident in d.identifiers), None)


def _setpoint(hass, entry, thermostat="climate.living"):
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{thermostat}_ai_setpoint")
    return hass.states.get(entity_id) if entity_id else None


async def _poll(hass, entry):
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_absent_thermostat_makes_its_sensors_unavailable(hass, aioclient_mock):
    mock_backend(aioclient_mock, graphql=responder(PRESENT, ABSENT))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    assert _setpoint(hass, entry).state == "20.5"

    await _poll(hass, entry)
    assert _setpoint(hass, entry).state == STATE_UNAVAILABLE
    assert _device(hass, entry) is not None                       # kept for now


async def test_not_removed_before_seven_days(hass, aioclient_mock, freezer):
    mock_backend(aioclient_mock, graphql=responder(PRESENT, ABSENT))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    await _poll(hass, entry)                                      # absence recorded

    freezer.tick(timedelta(days=6, hours=23))
    await _poll(hass, entry)
    assert _device(hass, entry) is not None


async def test_a_removed_thermostat_that_comes_back_gets_new_sensors(hass, aioclient_mock, freezer):
    mock_backend(aioclient_mock, graphql=responder(PRESENT, ABSENT, ABSENT, PRESENT))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    await _poll(hass, entry)
    freezer.tick(timedelta(days=7, minutes=1))
    await _poll(hass, entry)
    assert _device(hass, entry) is None

    await _poll(hass, entry)                                      # reported again
    assert _device(hass, entry) is not None
    assert _setpoint(hass, entry).state == "20.5"


async def test_device_is_removed_after_seven_days_absent(hass, aioclient_mock, freezer):
    mock_backend(aioclient_mock, graphql=responder(PRESENT, ABSENT))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    await _poll(hass, entry)

    freezer.tick(timedelta(days=7, minutes=1))
    await _poll(hass, entry)
    assert _device(hass, entry) is None
    assert _setpoint(hass, entry) is None                         # its entities went with it


async def test_absence_clock_survives_a_reload(hass, aioclient_mock, freezer):
    mock_backend(aioclient_mock, graphql=responder(PRESENT, ABSENT))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    await _poll(hass, entry)
    first_missed = entry.runtime_data.devices.absent["climate.living"]

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.devices.absent["climate.living"] == first_missed

    freezer.tick(timedelta(days=7, minutes=1))
    await _poll(hass, entry)
    assert _device(hass, entry) is None


async def test_a_failed_poll_never_counts_as_absence(hass, aioclient_mock, freezer):
    mock_backend(aioclient_mock, graphql=responder(PRESENT, DOWN))
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    for _ in range(3):
        freezer.tick(timedelta(days=4))
        await _poll(hass, entry)
    assert _device(hass, entry) is not None
    assert entry.runtime_data.devices.absent == {}


async def test_deselected_thermostat_is_removed_at_the_next_setup(hass, aioclient_mock):
    mock_backend(aioclient_mock, graphql=responder(PRESENT, ABSENT))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    assert _device(hass, entry) is not None

    hass.config_entries.async_update_entry(entry, data={**entry.data, "include_entities": ["climate.bedroom"]})
    await hass.async_block_till_done(wait_background_tasks=True)       # the update listener reloads
    assert _device(hass, entry) is None


async def test_manual_delete_only_for_an_absent_thermostat(hass, aioclient_mock):
    mock_backend(aioclient_mock, graphql=responder(PRESENT, ABSENT))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    device = _device(hass, entry)

    assert await async_remove_config_entry_device(hass, entry, device) is False   # still reported
    await _poll(hass, entry)
    assert await async_remove_config_entry_device(hass, entry, device) is True
    assert "climate.living" not in entry.runtime_data.devices.registered


async def test_startup_before_the_first_poll_does_not_count_as_absence(hass, aioclient_mock):
    """Until the first poll finishes the coordinator has no data (but reports
    success); that must not start anyone's 7-day clock."""
    mock_backend(aioclient_mock, graphql=responder(PRESENT))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    assert _device(hass, entry) is not None

    with patch.object(EcoEdgeCoordinator, "async_refresh", return_value=None):   # first poll still pending
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.runtime_data.coordinator.data is None
    assert entry.runtime_data.devices.absent == {}


async def test_a_thermostat_that_still_exists_in_ha_is_never_removed(hass, aioclient_mock, freezer):
    """No reading for a week (e.g. heating off all summer) drops a thermostat from
    EcoEdge's list; while its climate entity exists it only goes unavailable."""
    mock_backend(aioclient_mock, graphql=responder(PRESENT, ABSENT))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    hass.states.async_set("climate.living", "off", {"current_temperature": 21.0})   # exists in HA
    await _poll(hass, entry)
    assert _setpoint(hass, entry).state == STATE_UNAVAILABLE

    freezer.tick(timedelta(days=30))
    await _poll(hass, entry)
    assert _device(hass, entry) is not None
    assert entry.runtime_data.devices.absent == {}


async def test_excluded_thermostat_is_removed_at_the_next_setup(hass, aioclient_mock):
    mock_backend(aioclient_mock, graphql=responder(PRESENT, ABSENT))
    entry = make_entry(hass, include_entities=[])
    await setup_entry(hass, entry)
    assert _device(hass, entry) is not None

    hass.config_entries.async_update_entry(entry, data={**entry.data, "exclude_entities": ["climate.living"]})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _device(hass, entry) is None
