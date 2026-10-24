"""Delivery under a slow or failing backend (audit H-01, H-02).

DebouncedQueue is tested directly with sub-second timings; the outage test
runs the full runtime against a mocked backend.
"""
from __future__ import annotations

import asyncio

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.ha_ecoedge_ai_thermostat import DebouncedQueue

from .conftest import make_entry, mock_backend, setup_entry, wait_for_flush


async def test_rapid_adds_flush_once_after_the_debounce():
    flushed: list[list[str]] = []

    async def flush(ids):
        flushed.append(ids)

    queue = DebouncedQueue(debounce_seconds=0.05, max_wait_seconds=1)
    for eid in ("climate.a", "climate.b", "climate.a"):
        await queue.add(eid, flush)
    await asyncio.sleep(0.15)
    assert flushed == [["climate.a", "climate.b"]]


async def test_max_wait_caps_a_continuous_event_storm():
    flushed: list[list[str]] = []

    async def flush(ids):
        flushed.append(ids)

    queue = DebouncedQueue(debounce_seconds=0.05, max_wait_seconds=0.2)
    for _ in range(12):                              # an event every 30 ms for ~0.36 s
        await queue.add("climate.a", flush)
        await asyncio.sleep(0.03)
    assert flushed, "no flush although max_wait elapsed during the storm"
    await queue.async_cancel()


@pytest.mark.xfail(reason="audit H-01: add() cancels the task that is running the flush (upload/retries)")
async def test_new_event_does_not_cancel_an_inflight_flush():
    started, completed = [], []

    async def slow_flush(ids):                       # a slow backend: upload takes 0.3 s
        started.append(ids)
        await asyncio.sleep(0.3)
        completed.append(ids)

    queue = DebouncedQueue(debounce_seconds=0.05, max_wait_seconds=1)
    await queue.add("climate.a", slow_flush)
    await asyncio.sleep(0.1)                         # the flush for climate.a is now uploading
    await queue.add("climate.b", slow_flush)         # a new state change arrives meanwhile
    await asyncio.sleep(0.6)

    assert ["climate.a"] in completed, f"started={started} completed={completed}"


@pytest.mark.xfail(reason="audit H-02: a batch is dropped after 3 failed attempts; nothing is resent")
async def test_readings_from_an_outage_are_delivered_after_recovery(hass, aioclient_mock):
    accepted: list[dict] = []
    attempts = {"n": 0}

    async def push(method, url, data):               # down for the first 3 attempts, then healthy
        attempts["n"] += 1
        if attempts["n"] <= 3:
            return AiohttpClientMockResponse(method, URL(url), status=500)
        accepted.extend(data["items"])
        return AiohttpClientMockResponse(method, URL(url), json={"ok": True})

    hass.states.async_set("climate.living", "heat", {"current_temperature": 19.0})
    hass.states.async_set("sensor.outdoor", "4.0", {"device_class": "temperature"})
    mock_backend(aioclient_mock, push=push)
    entry = make_entry(hass)
    await setup_entry(hass, entry)                   # startup snapshot (19.0) hits the outage

    hass.states.async_set("climate.living", "heat", {"current_temperature": 19.5})
    await wait_for_flush(hass)                       # backend is back

    temps = [i["attributes"].get("current_temperature") for i in accepted if i["entity_id"] == "climate.living"]
    assert 19.5 in temps                              # sanity: the new reading arrived
    assert 19.0 in temps, f"the reading taken during the outage was lost; accepted: {temps}"
    await hass.config_entries.async_unload(entry.entry_id)
