"""v0.7.0: device-scoped profiles (H-06, M-10), one API base (H-07), data
minimization (M-01), YAML deprecation (M-02) and the outdated-version issue (M-08)."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from .conftest import (
    CLIENT_ID,
    DOMAIN,
    GRAPHQL_URL,
    LOGIN_URL,
    PROFILE,
    PUSH_URL,
    REFRESH_URL,
    SYNC_URL,
    calls_to,
    make_entry,
    mock_backend,
    pushed_items,
    responder,
    setup_entry,
)

STAGING = "https://api.staging.test"
STAGING_RECEIVER = "https://receiver.staging.test"


def _climate(hass: HomeAssistant) -> None:
    hass.states.async_set("climate.living", "heat", {
        "current_temperature": 20.5, "temperature": 21.0, "hvac_action": "heating", "friendly_name": "Living",
        "min_temp": 7, "max_temp": 35, "target_temp_step": 0.5,
        "vendor_serial": "SN-123456", "ip_address": "192.168.1.20", "preset_modes": ["eco", "away"],
    })
    hass.states.async_set("sensor.outdoor", "4.5", {"device_class": "temperature", "unit_of_measurement": "°C",
                                                    "battery": 87, "linkquality": 120})


# ── M-01: data minimization ───────────────────────────────────────────────────
async def test_only_allowlisted_attributes_and_no_location_are_pushed(hass, aioclient_mock):
    _climate(hass)
    mock_backend(aioclient_mock)
    await setup_entry(hass, make_entry(hass))

    items = {i["entity_id"]: i for i in pushed_items(aioclient_mock)}
    assert set(items["climate.living"]["attributes"]) == {
        "current_temperature", "temperature", "hvac_action", "friendly_name", "min_temp", "max_temp",
        "target_temp_step"}
    assert set(items["sensor.outdoor"]["attributes"]) == {"device_class", "unit_of_measurement"}
    body = calls_to(aioclient_mock, PUSH_URL)[0]
    assert not {"latitude", "longitude", "location_name", "timezone"} & set(body)


# ── H-06 / M-10: profiles by device token ─────────────────────────────────────
async def test_backend_without_device_profiles_falls_back_to_current_profiles(hass, aioclient_mock):
    unknown_field = {"status": 400, "json": {"errors": [{"message": 'Cannot query field "deviceProfiles" on type "Query".'}]}}
    mock_backend(aioclient_mock, graphql=responder(unknown_field, {"json": {"data": {"currentProfiles": [PROFILE]}}}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    queries = calls_to(aioclient_mock, GRAPHQL_URL)
    assert "deviceProfiles" in queries[0]["query"]
    assert "currentProfiles" in queries[1]["query"] and queries[1]["variables"] == {"homeId": "Home"}
    assert entry.runtime_data.coordinator.data == {"climate.living": PROFILE}


# ── H-07: one API base ────────────────────────────────────────────────────────
async def test_api_calls_use_the_stored_base_and_pushes_the_receiver(hass, aioclient_mock):
    _climate(hass)
    aioclient_mock.post(f"{STAGING_RECEIVER}/api/ha", json={"ok": True})
    aioclient_mock.post(f"{STAGING}/graphql/", json={"data": {"deviceProfiles": [PROFILE]}})
    aioclient_mock.post(f"{STAGING}/api/device/sync-entities", json={"synced": True})
    entry = make_entry(hass, endpoint=STAGING_RECEIVER, api_base=STAGING)
    await setup_entry(hass, entry)

    assert calls_to(aioclient_mock, f"{STAGING_RECEIVER}/api/ha")
    assert calls_to(aioclient_mock, f"{STAGING}/graphql/")
    assert calls_to(aioclient_mock, f"{STAGING}/api/device/sync-entities")   # was the receiver host: 404
    assert not calls_to(aioclient_mock, GRAPHQL_URL) and not calls_to(aioclient_mock, SYNC_URL)


async def test_entity_sync_goes_to_the_api_not_the_receiver(hass, aioclient_mock):
    """Production layout: pushes to receiver.ecoedge.io, the sync to ha.ecoedge.io."""
    receiver = "https://receiver.ecoedge.io"
    aioclient_mock.post(f"{receiver}/api/ha", json={"ok": True})
    mock_backend(aioclient_mock)
    await setup_entry(hass, make_entry(hass, endpoint=receiver))

    assert calls_to(aioclient_mock, SYNC_URL) == [{"thermostats": ["climate.living"], "outdoor_sensors": ["sensor.outdoor"]}]


async def test_refresh_stores_the_contract_without_a_reload(hass, aioclient_mock):
    _climate(hass)
    mock_backend(aioclient_mock, push=responder({"status": 401}, {"json": {"ok": True}}),
                 refresh=responder({"json": {"token": "access-2", "api_base": "https://ha.ecoedge.io",
                                             "min_supported_version": "0.6.1"}}))
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    runtime = entry.runtime_data

    assert entry.data["api_key"] == "access-2"
    assert entry.data["api_base"] == "https://ha.ecoedge.io" and entry.data["min_supported_version"] == "0.6.1"
    assert entry.runtime_data is runtime          # bookkeeping only: no reload
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"outdated_{entry.entry_id}") is None


async def test_login_stores_api_base_and_min_version(hass, aioclient_mock):
    aioclient_mock.post(LOGIN_URL, json={"token": "access-1", "refresh_token": "refresh-1", "client_id": CLIENT_ID,
                                         "home_id": "Home", "ha_endpoint": "https://receiver.ecoedge.io/api/ha",
                                         "api_base": "https://ha.ecoedge.io/", "min_supported_version": "0.6.1"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {
            "email": "anna@example.test", "password": "secret-pass", "include_entities": ["climate.living"],
            "outdoor_sensor": "sensor.outdoor"})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["endpoint"] == "https://receiver.ecoedge.io"
    assert result["data"]["api_base"] == "https://ha.ecoedge.io"
    assert result["data"]["min_supported_version"] == "0.6.1"


async def test_advanced_users_can_sign_in_against_another_api_base(hass, aioclient_mock):
    aioclient_mock.post(f"{STAGING}/api/auth/login", json={"token": "access-1", "refresh_token": "refresh-1",
                                                          "client_id": CLIENT_ID,
                                                          "ha_endpoint": f"{STAGING_RECEIVER}/api/ha"})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER, "show_advanced_options": True})
    assert "api_base" in result["data_schema"].schema
    with patch(f"custom_components.{DOMAIN}.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {
            "email": "anna@example.test", "password": "secret-pass", "outdoor_sensor": "sensor.outdoor",
            "api_base": f"{STAGING}/"})
        await hass.async_block_till_done()

    assert result["data"]["api_base"] == STAGING and result["data"]["endpoint"] == STAGING_RECEIVER
    assert not calls_to(aioclient_mock, LOGIN_URL)


# ── M-08: outdated client ─────────────────────────────────────────────────────
async def test_outdated_version_raises_a_repair_issue(hass, aioclient_mock):
    mock_backend(aioclient_mock)
    entry = make_entry(hass, min_supported_version="99.0.0")
    await setup_entry(hass, entry)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"outdated_{entry.entry_id}")
    assert issue is not None and issue.translation_placeholders["min_version"] == "99.0.0"


# ── M-02: YAML deprecation ────────────────────────────────────────────────────
YAML = {DOMAIN: {"endpoint": "https://ha.ecoedge.io", "email": "anna@example.test"}}


async def test_yaml_raises_the_issue_and_is_not_reimported_over_an_entry(hass, aioclient_mock):
    mock_backend(aioclient_mock)
    make_entry(hass)
    with patch.object(hass.config_entries.flow, "async_init") as flow_init:
        assert await async_setup_component(hass, DOMAIN, YAML)
        await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, "yaml_deprecated") is not None
    assert not [c for c in flow_init.call_args_list if c.kwargs.get("context", {}).get("source") == "import"]
    assert not calls_to(aioclient_mock, REFRESH_URL) and not calls_to(aioclient_mock, LOGIN_URL)


async def test_yaml_without_an_entry_is_still_imported_once(hass, aioclient_mock):
    with patch.object(hass.config_entries.flow, "async_init") as flow_init:
        assert await async_setup_component(hass, DOMAIN, YAML)
        await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, "yaml_deprecated") is not None
    assert [c.kwargs["context"]["source"] for c in flow_init.call_args_list] == ["import"]
