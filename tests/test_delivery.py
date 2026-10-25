"""Delivery under a slow or failing backend (v0.6.0: audit H-01, H-02, H-03, M-03).

BatchWindow and Outbox are tested directly with sub-second timings; the
end-to-end cases run the full runtime against a mocked backend.
"""
from __future__ import annotations

import asyncio
import time

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.ha_ecoedge_ai_thermostat.delivery import (
    BatchWindow,
    Outbox,
    SendResult,
    SendStatus,
)

from .conftest import (
    DOMAIN,
    PUSH_URL,
    calls_to,
    make_entry,
    mock_backend,
    setup_entry,
    wait_for_flush,
)


def _item(eid, temp, ts="2026-10-24T12:00:00+00:00"):
    return {"entity_id": eid, "state": "heat", "attributes": {"current_temperature": temp},
            "last_changed": ts, "last_updated": ts}


# ── BatchWindow ──────────────────────────────────────────────────────────────
async def test_every_snapshot_in_a_window_is_kept_in_one_batch():
    """H-03: three changes of one entity inside the window → one batch, all three readings."""
    batches: list[list[dict]] = []

    async def on_batch(items):
        batches.append(items)

    window = BatchWindow(window_seconds=0.05, max_wait_seconds=1, on_batch=on_batch)
    for temp in (20.0, 20.1, 20.2):
        window.add(_item("climate.a", temp))
    await asyncio.sleep(0.15)
    assert [[i["attributes"]["current_temperature"] for i in b] for b in batches] == [[20.0, 20.1, 20.2]]


async def test_max_wait_cuts_a_batch_during_a_continuous_storm():
    batches: list[list[dict]] = []

    async def on_batch(items):
        batches.append(items)

    window = BatchWindow(window_seconds=0.05, max_wait_seconds=0.2, on_batch=on_batch)
    for n in range(12):                              # a change every 30 ms for ~0.36 s
        window.add(_item("climate.a", 20 + n / 10))
        await asyncio.sleep(0.03)
    assert batches, "no batch although max_wait elapsed during the storm"
    await window.flush_now()
    assert sum(len(b) for b in batches) == 12        # nothing lost


# ── Outbox (unit) ────────────────────────────────────────────────────────────
async def test_outbox_caps_size_and_counts_what_it_drops(hass):
    async def never(batch):
        return SendResult(SendStatus.RETRY)

    outbox = Outbox(hass, f"{DOMAIN}.test_cap", never, max_items=5, max_age_seconds=3600)
    outbox._paused = True                            # keep the sender out of this test
    for n in range(4):
        outbox.enqueue([_item("climate.a", n), _item("climate.b", n)])
    assert outbox.pending_items <= 5
    assert outbox.dropped_overflow == 8 - outbox.pending_items
    assert [i["attributes"]["current_temperature"] for i in outbox.batches[-1]["items"]] == [3, 3]   # newest kept


async def test_outbox_drops_expired_batches(hass):
    async def never(batch):
        return SendResult(SendStatus.RETRY)

    outbox = Outbox(hass, f"{DOMAIN}.test_age", never, max_items=100, max_age_seconds=60)
    outbox._paused = True
    outbox.batches = [{"batch_id": "old", "created_at": time.time() - 120, "items": [_item("climate.a", 1)]}]
    outbox.enqueue([_item("climate.a", 2)])
    assert [b["batch_id"] for b in outbox.batches] != ["old"] and outbox.dropped_overflow == 1


# ── end to end ───────────────────────────────────────────────────────────────
async def test_state_changes_during_a_slow_upload_cancel_nothing(hass, aioclient_mock):
    """H-01 (was xfail): a slow backend (0.3 s per request) with changes arriving meanwhile."""
    accepted: list[dict] = []

    async def slow(method, url, data):
        await asyncio.sleep(0.3)
        accepted.extend(data["items"])
        return AiohttpClientMockResponse(method, URL(url), json={"ok": True})

    hass.states.async_set("climate.living", "heat", {"current_temperature": 20.0})
    mock_backend(aioclient_mock, push=slow)
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    for n in range(3):                               # each arrives while an upload is in flight
        hass.states.async_set("climate.living", "heat", {"current_temperature": 20.1 + n / 10})
        await wait_for_flush(hass, 1.2)

    temps = [round(i["attributes"]["current_temperature"], 1) for i in accepted if i["entity_id"] == "climate.living"]
    assert temps == [20.0, 20.1, 20.2, 20.3]


async def test_readings_from_an_outage_are_delivered_after_recovery(hass, aioclient_mock):
    """H-02 (was xfail): down for 3 attempts, then healthy — nothing is lost, same batch_id resent."""
    accepted: list[dict] = []
    attempts: list[str] = []

    async def push(method, url, data):
        attempts.append(data["batch_id"])
        if len(attempts) <= 3:
            return AiohttpClientMockResponse(method, URL(url), status=500)
        accepted.extend(data["items"])
        return AiohttpClientMockResponse(method, URL(url), json={"ok": True})

    hass.states.async_set("climate.living", "heat", {"current_temperature": 19.0})
    hass.states.async_set("sensor.outdoor", "4.0", {"device_class": "temperature"})
    mock_backend(aioclient_mock, push=push)
    entry = make_entry(hass)
    await setup_entry(hass, entry)                   # the startup snapshot (19.0) hits the outage

    hass.states.async_set("climate.living", "heat", {"current_temperature": 19.5})
    await wait_for_flush(hass)

    temps = [i["attributes"]["current_temperature"] for i in accepted if i["entity_id"] == "climate.living"]
    assert temps == [19.0, 19.5]                     # in order, nothing lost
    assert len(set(attempts[:4])) == 1               # the retried batch kept its batch_id


async def test_permanent_rejection_is_not_retried(hass, aioclient_mock):
    async def bad_request(method, url, data):
        return AiohttpClientMockResponse(method, URL(url), status=422)

    hass.states.async_set("climate.living", "heat", {"current_temperature": 20.0})
    mock_backend(aioclient_mock, push=bad_request)
    entry = make_entry(hass)
    await setup_entry(hass, entry)

    runtime = entry.runtime_data
    assert len(calls_to(aioclient_mock, PUSH_URL)) == 1
    assert runtime.outbox.batches == [] and runtime.outbox.dropped_rejected == 1     # the one reading


async def test_retry_after_is_honoured(hass, aioclient_mock):
    times: list[float] = []

    async def busy_then_ok(method, url, data):
        times.append(time.monotonic())
        if len(times) == 1:
            return AiohttpClientMockResponse(method, URL(url), status=503, headers={"Retry-After": "0.4"})
        return AiohttpClientMockResponse(method, URL(url), json={"ok": True})

    hass.states.async_set("climate.living", "heat", {"current_temperature": 20.0})
    mock_backend(aioclient_mock, push=busy_then_ok)
    await setup_entry(hass, make_entry(hass))

    assert len(times) == 2 and times[1] - times[0] >= 0.4


async def test_buffered_batches_survive_a_restart(hass, aioclient_mock, hass_storage):
    async def down(method, url, data):
        return AiohttpClientMockResponse(method, URL(url), status=502)

    hass.states.async_set("climate.living", "heat", {"current_temperature": 18.5})
    mock_backend(aioclient_mock, push=down)
    entry = make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await asyncio.sleep(0.2)                         # a few failed attempts
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    stored = hass_storage[f"{DOMAIN}.outbox.{entry.entry_id}"]["data"]["batches"]
    assert stored and stored[0]["items"][0]["attributes"]["current_temperature"] == 18.5
    batch_id = stored[0]["batch_id"]

    aioclient_mock.clear_requests()
    mock_backend(aioclient_mock)                     # backend is back
    await setup_entry(hass, entry)
    resent = [b for b in calls_to(aioclient_mock, PUSH_URL) if b["batch_id"] == batch_id]
    assert resent and resent[0]["items"][0]["attributes"]["current_temperature"] == 18.5


async def test_removing_the_integration_deletes_its_outbox(hass, aioclient_mock, hass_storage):
    hass.states.async_set("climate.living", "heat", {"current_temperature": 20.0})
    mock_backend(aioclient_mock)
    entry = make_entry(hass)
    await setup_entry(hass, entry)
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.outbox.{entry.entry_id}" in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"{DOMAIN}.outbox.{entry.entry_id}" not in hass_storage


async def test_an_event_storm_needs_few_uploads_and_loses_no_reading(hass, aioclient_mock):
    """H-03 exit criterion: 5 thermostats changing every 50 ms → a handful of uploads, every reading delivered."""
    thermostats = [f"climate.room_{n}" for n in range(5)]
    for eid in thermostats:
        hass.states.async_set(eid, "heat", {"current_temperature": 20.0})
    mock_backend(aioclient_mock)
    entry = make_entry(hass, include_entities=thermostats)
    await setup_entry(hass, entry)
    aioclient_mock.mock_calls.clear()

    changes = 0
    for step in range(20):                           # 1 s storm, 100 state changes
        for eid in thermostats:
            hass.states.async_set(eid, "heat", {"current_temperature": round(20.0 + (step + 1) / 100, 2)})
            changes += 1
        await asyncio.sleep(0.05)
    await wait_for_flush(hass, 1.5)

    bodies = calls_to(aioclient_mock, PUSH_URL)
    delivered = sum(len(b["items"]) for b in bodies)
    assert delivered == changes == 100
    assert len(bodies) <= 3, f"{len(bodies)} uploads for {changes} readings"       # v0.5.x: one per change
