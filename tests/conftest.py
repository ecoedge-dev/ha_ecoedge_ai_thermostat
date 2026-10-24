"""Shared fixtures for the EcoEdge AI Thermostat tests.

Everything runs against mocked EcoEdge endpoints (`aioclient_mock`); no test
talks to a real backend. Timers that would make tests slow (push retry
backoff, the 90 s post-push profile fetch) are shortened by `fast_timers`.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

DOMAIN = "ha_ecoedge_ai_thermostat"
BASE = "https://ha.ecoedge.io"
LOGIN_URL = f"{BASE}/api/auth/login"
REFRESH_URL = f"{BASE}/api/auth/refresh"
TOKEN_URL = f"{BASE}/api/auth/token"
PUSH_URL = f"{BASE}/api/ha"
SYNC_URL = f"{BASE}/api/device/sync-entities"
GRAPHQL_URL = f"{BASE}/graphql/"
CLIENT_ID = "11111111-2222-3333-4444-555555555555"

PROFILE = {
    "entityId": "climate.living",
    "predictionModel": "RC",
    "decisionSafeSetpoint": 20.46,
    "rcKPerHour": 0.01234,
    "rcConfidence": 0.8234,
    "savingsPct7d": 9.44,
    "mlBlendActive": False,
    "mlBlendedSetpoint": None,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    yield


@pytest.fixture(autouse=True)
async def unload_entries(hass: HomeAssistant):
    """Unload every loaded entry after the test, also when an (xfail) assertion
    stopped the test early, so no push/poll tasks outlive it."""
    yield
    from homeassistant.config_entries import ConfigEntryState

    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture(autouse=True)
def fast_timers(monkeypatch):
    """Shrink the push retry backoff and the post-push fetch delay."""
    monkeypatch.setattr(f"custom_components.{DOMAIN}.DEFAULT_RETRY_BACKOFF", 0.01)
    monkeypatch.setattr(f"custom_components.{DOMAIN}.coordinator.DEFAULT_FETCH_DELAY_SECONDS", 0.01)


def entry_data(**overrides: Any) -> dict[str, Any]:
    data = {
        "endpoint": BASE,
        "api_key": "access-1",
        "refresh_token": "refresh-1",
        "client_id": CLIENT_ID,
        "home_id": "Home",
        "email": "anna@example.test",
        "include_entities": ["climate.living"],
        "exclude_entities": [],
        "outdoor_sensor": "sensor.outdoor",
        "debounce_seconds": 1,
        "timeout_seconds": 10,
    }
    data.update(overrides)
    return data


def make_entry(hass: HomeAssistant, **overrides: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=entry_data(**overrides), version=4, unique_id=CLIENT_ID, title="Home"
    )
    entry.add_to_hass(hass)
    return entry


def responder(*replies: dict[str, Any]) -> Callable:
    """side_effect that answers successive calls with `replies` (the last repeats).

    Each reply is a dict of AiohttpClientMockResponse kwargs, e.g. {"status": 401}.
    """
    calls = {"n": 0}

    async def side_effect(method, url, data):
        reply = replies[min(calls["n"], len(replies) - 1)]
        calls["n"] += 1
        if "delay" in reply:
            await asyncio.sleep(reply["delay"])
        kwargs = {k: v for k, v in reply.items() if k != "delay"}
        return AiohttpClientMockResponse(method, URL(url), **kwargs)

    return side_effect


def mock_backend(
    aioclient_mock: AiohttpClientMocker,
    *,
    push: Callable | None = None,
    refresh: Callable | None = None,
    graphql: Callable | None = None,
    profiles: list[dict[str, Any]] | None = None,
) -> None:
    """Register every endpoint the runtime calls. Defaults: all succeed."""
    aioclient_mock.post(PUSH_URL, side_effect=push or responder({"json": {"ok": True}}))
    aioclient_mock.post(
        REFRESH_URL, side_effect=refresh or responder({"json": {"token": "access-2"}})
    )
    aioclient_mock.post(
        GRAPHQL_URL,
        side_effect=graphql
        or responder({"json": {"data": {"currentProfiles": profiles if profiles is not None else [PROFILE]}}}),
    )
    aioclient_mock.post(SYNC_URL, json={"ok": True})
    aioclient_mock.post(TOKEN_URL, json={"ok": True})


def calls_to(aioclient_mock: AiohttpClientMocker, url: str) -> list[Any]:
    """Request bodies sent to `url`, oldest first."""
    target = URL(url)
    return [data for _m, u, data, _h in aioclient_mock.mock_calls if u.with_query(None) == target]


def pushed_items(aioclient_mock: AiohttpClientMocker) -> list[dict[str, Any]]:
    return [item for body in calls_to(aioclient_mock, PUSH_URL) for item in body["items"]]


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def wait_for_flush(hass: HomeAssistant, seconds: float = 1.4) -> None:
    """Let the (real-time) debounce fire and the push finish."""
    await asyncio.sleep(seconds)
    await hass.async_block_till_done(wait_background_tasks=True)


def reauth_flows(hass: HomeAssistant) -> list[dict[str, Any]]:
    return [f for f in hass.config_entries.flow.async_progress() if f["context"].get("source") == "reauth"]
