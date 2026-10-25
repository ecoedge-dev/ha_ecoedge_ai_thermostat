"""Collect state snapshots into batches and deliver them reliably (v0.6.0).

Two independent parts, so a new state change can never cancel a running upload
(audit H-01):

- **BatchWindow** collects state *snapshots* (not entity ids, audit H-03) and
  cuts a batch after a quiet period (the debounce window), at the latest
  `max_wait` after the first pending snapshot. Cutting a batch only hands it to
  the outbox; it never touches the network.
- **Outbox** stores every batch with a stable `batch_id` in Home Assistant's
  storage and delivers them oldest first through one sender task. A batch is
  removed only when the backend accepted it (or reported it as a duplicate),
  or rejected it permanently. Transient failures keep it and retry with
  backoff, honouring `Retry-After` (audit H-02, M-03). The backend deduplicates
  on `batch_id`, so a resend after a lost response is safe.

Bounded: at most `max_items` readings and `max_age` seconds are kept; older
batches are dropped first and counted (`dropped_overflow`), as are batches the
backend rejects permanently (`dropped_rejected`).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


class RefreshOutcome(Enum):
    """v0.6.1 (audit H-04): only a REJECTED refresh needs the user (reauth)."""

    NEW_TOKEN = "new_token"
    REJECTED = "rejected"          # the refresh token itself is invalid or expired
    UNAVAILABLE = "unavailable"    # EcoEdge unreachable / 5xx / timeout — try again later


class SendStatus(Enum):
    DELIVERED = "delivered"        # stored (or already stored: duplicate)
    REJECTED = "rejected"          # permanent 4xx: drop the batch
    RETRY = "retry"                # transient: keep, try again later
    AUTH_FAILED = "auth_failed"    # credentials need the user (reauth): keep, stop sending


@dataclass
class SendResult:
    status: SendStatus
    retry_after: float | None = None
    detail: str = ""


class BatchWindow:
    """Debounced collection of state snapshots into batches."""

    def __init__(
        self,
        window_seconds: float,
        max_wait_seconds: float,
        on_batch: Callable[[list[dict[str, Any]]], Awaitable[None]],
    ) -> None:
        self.window_seconds = window_seconds
        self.max_wait_seconds = max(max_wait_seconds, window_seconds)
        self._on_batch = on_batch
        self._items: list[dict[str, Any]] = []
        self._first_at: float | None = None
        self._timer: asyncio.Task | None = None

    def add(self, item: dict[str, Any]) -> None:
        """Queue one snapshot (call from the event loop)."""
        now = asyncio.get_running_loop().time()
        self._items.append(item)
        if self._first_at is None:
            self._first_at = now
        deadline_in = self._first_at + self.max_wait_seconds - now
        delay = min(self.window_seconds, max(0.0, deadline_in))
        if self._timer and not self._timer.done():
            self._timer.cancel()          # only ever a sleeping timer — the batch hand-off is a separate task
        self._timer = asyncio.create_task(self._fire_later(delay))

    async def _fire_later(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._timer = None
        asyncio.create_task(self.flush_now())

    async def flush_now(self) -> None:
        """Hand everything collected so far to the outbox."""
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None
        items, self._items, self._first_at = self._items, [], None
        if items:
            await self._on_batch(items)

    @property
    def pending(self) -> int:
        return len(self._items)


class Outbox:
    """Persistent, bounded, ordered delivery of batches."""

    def __init__(
        self,
        hass: HomeAssistant,
        storage_key: str,
        send: Callable[[dict[str, Any]], Awaitable[SendResult]],
        *,
        max_items: int,
        max_age_seconds: float,
        backoff_base: float = 2.0,
        backoff_max: float = 300.0,
        on_delivered: Callable[[], None] | None = None,
    ) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, storage_key)
        self._send = send
        self.max_items = max_items
        self.max_age_seconds = max_age_seconds
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._on_delivered = on_delivered
        self.batches: list[dict[str, Any]] = []
        self.dropped_overflow = 0
        self.dropped_rejected = 0
        self.last_result: str | None = None
        # v0.8.0: wall-clock time of the last accepted batch (persisted), for
        # the "EcoEdge unreachable" repair issue and diagnostics
        self.last_delivered_at: float | None = None
        self._failures = 0
        self._paused = False          # AUTH_FAILED: wait for a reload with new credentials
        self._sender: asyncio.Task | None = None

    # ── persistence ───────────────────────────────────────────────────────────
    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.batches = [b for b in data.get("batches", []) if b.get("batch_id") and b.get("items")]
        self.dropped_overflow = int(data.get("dropped_overflow", 0))
        self.dropped_rejected = int(data.get("dropped_rejected", 0))
        self.last_delivered_at = data.get("last_delivered_at")
        self._trim()
        if self.batches:
            _LOGGER.info("EcoEdge outbox: %d buffered batch(es) from before the restart", len(self.batches))

    def _data(self) -> dict[str, Any]:
        return {"batches": self.batches, "dropped_overflow": self.dropped_overflow,
                "dropped_rejected": self.dropped_rejected, "last_delivered_at": self.last_delivered_at}

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data, 2)

    async def async_save(self) -> None:
        await self._store.async_save(self._data())

    # ── queueing ──────────────────────────────────────────────────────────────
    def enqueue(self, items: list[dict[str, Any]]) -> str:
        batch = {"batch_id": str(uuid.uuid4()), "created_at": time.time(), "items": items}
        self.batches.append(batch)
        self._trim()
        self._schedule_save()
        self.start()
        return batch["batch_id"]

    def _trim(self) -> None:
        """Drop the oldest batches beyond the age or size cap (counted)."""
        cutoff = time.time() - self.max_age_seconds
        while self.batches and (
            self.batches[0].get("created_at", 0) < cutoff
            or sum(len(b["items"]) for b in self.batches) > self.max_items
        ):
            if len(self.batches) == 1 and self.batches[0].get("created_at", 0) >= cutoff:
                break                       # never drop the only fresh batch
            dropped = self.batches.pop(0)
            self.dropped_overflow += len(dropped["items"])
            _LOGGER.warning("EcoEdge outbox full/expired — dropped %d buffered reading(s)", len(dropped["items"]))

    @property
    def pending_items(self) -> int:
        return sum(len(b["items"]) for b in self.batches)

    @property
    def oldest_age_seconds(self) -> float | None:
        if not self.batches:
            return None
        return max(0.0, time.time() - self.batches[0].get("created_at", time.time()))

    @property
    def paused(self) -> bool:
        return self._paused

    # ── sending ───────────────────────────────────────────────────────────────
    def start(self) -> None:
        """Run the sender if there is work. It exits when the outbox is empty
        (or paused) and is restarted by the next enqueue — no idle task."""
        if self.batches and not self._paused and (self._sender is None or self._sender.done()):
            self._sender = self.hass.async_create_background_task(self._run(), "ecoedge_outbox_sender")

    def _backoff(self, retry_after: float | None) -> float:
        if retry_after is not None:
            return max(0.0, retry_after)
        delay = min(self.backoff_max, self.backoff_base * (2 ** min(self._failures - 1, 10)))
        return delay * random.uniform(0.8, 1.2)

    async def _run(self) -> None:
        while self.batches and not self._paused:
            batch = self.batches[0]
            try:
                result = await self._send(batch)
            except Exception as err:                       # never let the sender die
                result = SendResult(SendStatus.RETRY, detail=f"{type(err).__name__}: {err}")
            self.last_result = f"{result.status.value} {result.detail}".strip()

            if result.status in (SendStatus.DELIVERED, SendStatus.REJECTED):
                if self.batches and self.batches[0] is batch:
                    self.batches.pop(0)
                if result.status is SendStatus.REJECTED:
                    self.dropped_rejected += len(batch["items"])
                    _LOGGER.warning("EcoEdge rejected a batch permanently (%s) — %d reading(s) dropped",
                                    result.detail, len(batch["items"]))
                else:
                    self._failures = 0
                    self.last_delivered_at = time.time()
                    if self._on_delivered:
                        self._on_delivered()
                self._schedule_save()
                continue

            if result.status is SendStatus.AUTH_FAILED:
                self._paused = True                        # kept; resent after reauth reloads the entry
                self._schedule_save()
                _LOGGER.warning("EcoEdge outbox paused until re-authentication (%d batch(es) kept)",
                                len(self.batches))
                return

            self._failures += 1
            delay = self._backoff(result.retry_after)
            _LOGGER.debug("EcoEdge push failed (%s) — retry in %.0f s", result.detail, delay)
            await asyncio.sleep(delay)      # new batches queue behind; order is preserved

    async def async_stop(self) -> None:
        if self._sender and not self._sender.done():
            self._sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sender
        self._sender = None
        await self.async_save()
