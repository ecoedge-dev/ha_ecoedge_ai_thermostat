import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .config_schema import DOMAIN_SCHEMA
from .const import (
    ATTRIBUTE_ALLOWLIST,
    CONF_API_BASE,
    CONF_API_KEY,
    CONF_CLIENT_ID,
    CONF_DEBOUNCE_SECONDS,
    CONF_ENDPOINT,
    CONF_EXCLUDE,
    CONF_HOME_ID,
    CONF_INCLUDE,
    CONF_MIN_VERSION,
    CONF_OUTDOOR_SENSOR,
    CONF_REFRESH_TOKEN,
    CONF_SYNC_HASH,
    CONF_TIMEOUT_SECONDS,
    DEFAULT_DEBOUNCE_MAX_WAIT_SECONDS,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_REFRESH_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    DOMAIN,
    LEGACY_DEBOUNCE_SECONDS,
    OUTBOX_BACKOFF_BASE,
    OUTBOX_BACKOFF_MAX,
    OUTBOX_MAX_AGE_SECONDS,
    OUTBOX_MAX_ITEMS,
    PATH_REFRESH,
    PATH_SYNC_ENTITIES,
    api_url,
)
from .coordinator import EcoEdgeCoordinator
from .delivery import (
    STORAGE_VERSION,
    BatchWindow,
    Outbox,
    RefreshOutcome,
    SendResult,
    SendStatus,
)
from .issues import async_check_version, async_raise_yaml_deprecated
from .util import contract_updates, sync_fingerprint

_LOGGER = logging.getLogger(__name__)

# Home Assistant validates configuration.yaml via this
CONFIG_SCHEMA = vol.Schema({DOMAIN: DOMAIN_SCHEMA}, extra=vol.ALLOW_EXTRA)


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_climate_entity(entity_id: str) -> bool:
    return entity_id.startswith("climate.")


def _filter_entities(
    hass: HomeAssistant,
    entity_id: str,
    include: list[str],
    exclude: list[str],
    extra_sensors: list[str] | None = None,
) -> bool:
    if exclude and entity_id in exclude:
        return False
    if extra_sensors and entity_id in extra_sensors:
        return True
    if include:
        return entity_id in include
    # v0.4.0 (audit C2): default is climate entities ONLY. The old fallback also
    # pushed every temperature sensor in the home (bedroom sensors, fridge
    # probes) — data over-collection and the source of backend binding noise.
    # The configured outdoor sensor is covered by extra_sensors above.
    return _is_climate_entity(entity_id)


def _snapshot(st: State) -> dict[str, Any]:
    """One reading as it was at event time (v0.6.0: snapshots, not entity ids).

    v0.7.0 (audit M-01): allowlisted attributes only — vendor extras and
    device identifiers never leave Home Assistant.
    """
    return {
        "entity_id": st.entity_id,
        "state": st.state,
        "attributes": {k: v for k, v in st.attributes.items() if k in ATTRIBUTE_ALLOWLIST},
        "last_changed": st.last_changed.isoformat() if st.last_changed else None,
        "last_updated": st.last_updated.isoformat() if st.last_updated else None,
    }


def _retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None                       # HTTP-date form: fall back to own backoff


def _classify(status: int | None, retry_after: float | None, detail: str) -> SendResult:
    """Audit M-03: only transient failures are retried; permanent 4xx drop the batch."""
    if status is not None and 200 <= status < 300:
        return SendResult(SendStatus.DELIVERED, detail=detail)
    if status is None or status in (408, 425, 429) or status >= 500:
        return SendResult(SendStatus.RETRY, retry_after=retry_after, detail=detail)
    return SendResult(SendStatus.REJECTED, detail=detail)


def _outbox_key(entry_id: str) -> str:
    return f"{DOMAIN}.outbox.{entry_id}"


class PushClient:
    def __init__(
        self,
        hass: HomeAssistant,
        endpoint: str,
        api_key: str | None,
        refresh_token: str | None,
        client_id: str | None,
        timeout_seconds: int,
        api_base: str | None = None,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.api_base = api_base           # login/refresh/GraphQL/sync (audit H-07)
        self.contract: dict[str, str] = {}  # api_base / min_supported_version from the last refresh
        self.api_key = api_key
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.session = aiohttp_client.async_get_clientsession(hass)
        # v0.4.0 (audit H4): concurrent flushes hitting 401 must not both rotate
        self._refresh_lock = asyncio.Lock()

    async def post(self, payload: dict[str, Any]) -> tuple[int | None, float | None, str]:
        """POST one batch. Returns (status or None on a network error, Retry-After, detail)."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with self.session.post(
                f"{self.endpoint}/api/ha", json=payload, headers=headers, timeout=self.timeout
            ) as resp:
                txt = await resp.text()
                return resp.status, _retry_after(resp.headers.get("Retry-After")), f"HTTP {resp.status}: {txt[:200]}"
        except (TimeoutError, aiohttp.ClientError) as err:
            return None, None, f"{type(err).__name__}: {err}"

    async def async_refresh(self, failed_token: str | None = None) -> RefreshOutcome:
        """Exchange refresh_token for a new access token (now in `self.api_key`).

        Serialized: if another caller (push or poll) already rotated the token
        while we waited for the lock, reuse it instead of rotating again (audit H4).
        """
        if not self.refresh_token:
            return RefreshOutcome.REJECTED
        async with self._refresh_lock:
            if failed_token is not None and self.api_key and self.api_key != failed_token:
                return RefreshOutcome.NEW_TOKEN
            return await self._do_refresh()

    async def _do_refresh(self) -> RefreshOutcome:
        try:
            async with self.session.post(
                api_url(self.api_base, PATH_REFRESH),
                json={"refresh_token": self.refresh_token, "client_id": self.client_id or ""},
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REFRESH_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status in (400, 401, 403):
                    _LOGGER.warning("Token refresh rejected (HTTP %s)", resp.status)
                    return RefreshOutcome.REJECTED
                if resp.status != 200:
                    _LOGGER.info("Token refresh unavailable (HTTP %s) — will retry", resp.status)
                    return RefreshOutcome.UNAVAILABLE
                body = await resp.json()
                new_token = body.get("token")
                if not new_token:
                    return RefreshOutcome.UNAVAILABLE
                self.api_key = new_token
                self.contract = contract_updates(body)
                return RefreshOutcome.NEW_TOKEN
        except Exception as exc:
            _LOGGER.info("Token refresh request failed: %s — will retry", exc)
            return RefreshOutcome.UNAVAILABLE


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Handle YAML import and init the domain storage."""

    hass.data.setdefault(DOMAIN, {})

    if DOMAIN in config:
        # v0.7.0 (audit M-02): YAML is deprecated — it holds a plain-text
        # password, and importing at every start logged in at every start (a
        # wrong password in a restart loop locks the account's sign-in).
        async_raise_yaml_deprecated(hass)
        if hass.config_entries.async_entries(DOMAIN):
            _LOGGER.warning(
                "EcoEdge is configured in the UI — the %s: block in configuration.yaml is ignored; "
                "please remove it (it contains your password)", DOMAIN,
            )
        else:
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": config_entries.SOURCE_IMPORT},
                    data=config[DOMAIN],
                )
            )

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v4 (audit H6): unique_id becomes client_id.

    home_id was the old unique_id, so two homes both named "Home" could not
    coexist on one HA instance. client_id is backend-issued and unique per
    device. Entries that never logged in keep their old unique_id.

    v5 (v0.6.0, audit H-03): the old 3 s debounce default becomes the 20 s
    batching window; a value the user chose explicitly is kept.
    """
    if entry.version > 5:
        return False  # downgrade from a future version — not supported
    if entry.version < 4:
        conf = {**entry.data, **entry.options}
        client_id = conf.get(CONF_CLIENT_ID)
        hass.config_entries.async_update_entry(
            entry,
            unique_id=str(client_id) if client_id else entry.unique_id,
            version=4,
        )
    if entry.version < 5:
        data = dict(entry.data)
        if data.get(CONF_DEBOUNCE_SECONDS, LEGACY_DEBOUNCE_SECONDS) == LEGACY_DEBOUNCE_SECONDS:
            data[CONF_DEBOUNCE_SECONDS] = DEFAULT_DEBOUNCE_SECONDS
        hass.config_entries.async_update_entry(entry, data=data, version=5)
    return True


def _config_fingerprint(entry: ConfigEntry) -> str:
    """User-visible config only — volatile bookkeeping must not force reloads."""
    conf = {**entry.data, **entry.options}
    # api_base / min_supported_version arrive with token refreshes and are
    # applied in place (v0.7.0)
    for volatile in (CONF_API_KEY, CONF_REFRESH_TOKEN, CONF_SYNC_HASH, CONF_API_BASE, CONF_MIN_VERSION):
        conf.pop(volatile, None)
    return json.dumps(conf, sort_keys=True, default=str)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = HaAiPushRuntime(hass, entry)
    await runtime.async_setup()
    hass.data[DOMAIN][entry.entry_id] = {
        "runtime": runtime,
        "coordinator": runtime.coordinator,
    }
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # audit M-05: keep the runtime when the sensor platform refuses to unload —
    # tearing it down anyway left a half-unloaded entry (and doubled pushes on reload)
    if not await hass.config_entries.async_unload_platforms(entry, ["sensor"]):
        return False
    entry_data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if entry_data:
        runtime: HaAiPushRuntime = entry_data["runtime"]
        await runtime.async_unload()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Integration deleted: drop its buffered readings too."""
    await Store(hass, STORAGE_VERSION, _outbox_key(entry.entry_id)).async_remove()


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload only when the runtime doesn't already reflect the entry.

    Token refreshes and sync-hash bookkeeping also update the entry; reloading
    for those tore the whole runtime down mid-flush (and re-pushed the full
    snapshot) every ~30 days for no reason.

    v0.5.1: skipping is only safe when the RUNTIME wrote the update (it already
    holds the new token in memory). A token rotated by the options flow must
    reload, or the running coordinator keeps polling with the revoked token
    and drops the user into reauth.
    """
    entry_data = hass.data[DOMAIN].get(entry.entry_id)
    runtime: HaAiPushRuntime | None = entry_data["runtime"] if entry_data else None
    if runtime is None:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    conf = {**entry.data, **entry.options}
    creds_current = (
        conf.get(CONF_API_KEY) == runtime.current_api_key
        and conf.get(CONF_REFRESH_TOKEN) == runtime.current_refresh_token
    )
    if creds_current and runtime.config_fingerprint == _config_fingerprint(entry):
        _LOGGER.debug("Entry update is bookkeeping-only — skipping reload")
        return
    await hass.config_entries.async_reload(entry.entry_id)


class HaAiPushRuntime:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._unsub: Callable[[], None] | None = None
        self._window: BatchWindow | None = None
        self.outbox: Outbox | None = None
        self._client: PushClient | None = None
        self.coordinator: EcoEdgeCoordinator | None = None
        self.config_fingerprint: str = ""
        self.integration_version: str = ""

    @property
    def current_api_key(self) -> str | None:
        """Live access token — client.api_key mutates on runtime refresh."""
        return self._client.api_key if self._client else None

    @property
    def current_refresh_token(self) -> str | None:
        return self._client.refresh_token if self._client else None

    async def async_renew_token(self, failed_token: str | None) -> RefreshOutcome:
        """Shared by the push path and the profile poll (audit H-05).

        NEW_TOKEN is persisted to the entry (a bookkeeping update — no reload)
        and handed to the coordinator; REJECTED starts the reauth flow.
        """
        client = self._client
        if client is None:
            return RefreshOutcome.UNAVAILABLE
        outcome = await client.async_refresh(failed_token=failed_token)
        if outcome is RefreshOutcome.NEW_TOKEN and client.api_key:
            # v0.7.0: the refresh also carries the client contract (H-07, M-08)
            updates = {CONF_API_KEY: client.api_key, **client.contract}
            if any(self.entry.data.get(k) != v for k, v in updates.items()):
                self.hass.config_entries.async_update_entry(self.entry, data={**self.entry.data, **updates})
            if CONF_API_BASE in client.contract:
                client.api_base = client.contract[CONF_API_BASE]
            if CONF_MIN_VERSION in client.contract:
                async_check_version(self.hass, self.entry.entry_id, self.integration_version,
                                    client.contract[CONF_MIN_VERSION])
            if self.coordinator:
                self.coordinator.update_token(client.api_key)
        elif outcome is RefreshOutcome.REJECTED:
            _LOGGER.warning("EcoEdge refresh token rejected for %s — starting re-auth flow", client.endpoint)
            self.entry.async_start_reauth(self.hass)
        return outcome

    async def async_setup(self) -> None:
        conf = {**self.entry.data, **self.entry.options}
        self.config_fingerprint = _config_fingerprint(self.entry)

        endpoint = conf[CONF_ENDPOINT]
        api_key = conf.get(CONF_API_KEY)
        refresh_token = conf.get(CONF_REFRESH_TOKEN)
        client_id = conf.get(CONF_CLIENT_ID)
        include = conf.get(CONF_INCLUDE, [])
        exclude = conf.get(CONF_EXCLUDE, [])
        outdoor_sensor = conf.get(CONF_OUTDOOR_SENSOR)
        extra_sensors = [outdoor_sensor] if outdoor_sensor else []
        debounce_seconds = conf.get(CONF_DEBOUNCE_SECONDS, DEFAULT_DEBOUNCE_SECONDS)
        timeout_seconds = conf.get(CONF_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS)
        home_id = conf.get(CONF_HOME_ID)
        api_base = conf.get(CONF_API_BASE)
        if not home_id:
            # audit M-10: the backend keys this home by its name in the pushes
            _LOGGER.warning("EcoEdge entry has no home_id — sending HA's location name %r instead",
                            self.hass.config.location_name)

        integration = await async_get_integration(self.hass, DOMAIN)
        integration_version = str(integration.version)
        self.integration_version = integration_version
        async_check_version(self.hass, self.entry.entry_id, integration_version, conf.get(CONF_MIN_VERSION))

        client = PushClient(self.hass, endpoint, api_key, refresh_token, client_id, timeout_seconds, api_base)
        self._client = client

        coordinator = EcoEdgeCoordinator(
            hass=self.hass,
            entry=self.entry,
            session=aiohttp_client.async_get_clientsession(self.hass),
            api_key=api_key or "",
            home_id=home_id,
            renew=self.async_renew_token,
            api_base=lambda: client.api_base,
        )
        self.coordinator = coordinator
        # First fetch in the background — never block HA bootstrap on the cloud.
        self.hass.async_create_background_task(
            coordinator.async_refresh(), "ecoedge_initial_profile_fetch"
        )

        # Sync entity lists to backend on startup (background, non-blocking).
        self.hass.async_create_background_task(
            self._sync_entities_to_backend(api_key, include, outdoor_sensor),
            "ecoedge_entity_sync",
        )

        def payload_for(batch: dict[str, Any]) -> dict[str, Any]:
            return {
                "source": "homeassistant",
                "batch_id": batch["batch_id"],      # stable across resends → idempotent ingest
                "home_id": home_id or self.hass.config.location_name,
                # v0.7.0 (audit M-01): no location in batches — it goes to
                # EcoEdge only at sign-in and on a device update
                "ts_utc": _utc_iso(),
                "event": "state_change_batch",
                "count": len(batch["items"]),
                "items": batch["items"],
                # v0.5.0: fleet adoption tracking
                "integration_version": integration_version,
            }

        async def send(batch: dict[str, Any]) -> SendResult:
            payload = payload_for(batch)
            sent_with = client.api_key
            status, retry_after, detail = await client.post(payload)
            if status == 401:
                outcome = await self.async_renew_token(sent_with)
                if outcome is RefreshOutcome.UNAVAILABLE:
                    # audit H-04: EcoEdge is down, the credentials are fine — keep
                    # the batch and retry later, never ask the user for a password
                    return SendResult(SendStatus.RETRY, detail="token refresh unavailable")
                if outcome is RefreshOutcome.REJECTED:
                    # v0.4.0 (audit C3): HA's standard re-authenticate badge. The
                    # batch stays buffered and is resent after the reauth reload.
                    return SendResult(SendStatus.AUTH_FAILED, detail="refresh rejected")
                status, retry_after, detail = await client.post(payload)
                if status == 401:
                    self.entry.async_start_reauth(self.hass)
                    return SendResult(SendStatus.AUTH_FAILED, detail="401 after refresh")
            result = _classify(status, retry_after, detail)
            if result.status is SendStatus.DELIVERED:
                _LOGGER.debug("Pushed %s readings to %s", len(batch["items"]), client.endpoint)
            return result

        outbox = Outbox(
            self.hass, _outbox_key(self.entry.entry_id), send,
            max_items=OUTBOX_MAX_ITEMS, max_age_seconds=OUTBOX_MAX_AGE_SECONDS,
            backoff_base=OUTBOX_BACKOFF_BASE, backoff_max=OUTBOX_BACKOFF_MAX,
            on_delivered=coordinator.schedule_refresh_after_push,
        )
        await outbox.async_load()
        self.outbox = outbox

        async def to_outbox(items: list[dict[str, Any]]) -> None:
            outbox.enqueue(items)

        window = BatchWindow(debounce_seconds, DEFAULT_DEBOUNCE_MAX_WAIT_SECONDS, to_outbox)
        self._window = window

        @callback
        def _handle_state_change(event: Event) -> None:
            new_state = event.data.get("new_state")
            if new_state is None:               # removals
                return
            if not _filter_entities(self.hass, new_state.entity_id, include, exclude, extra_sensors):
                return
            window.add(_snapshot(new_state))

        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, _handle_state_change)

        # startup snapshot of every selected entity, as one batch
        snapshot = [
            _snapshot(st)
            for st in self.hass.states.async_all()
            if _filter_entities(self.hass, st.entity_id, include, exclude, extra_sensors)
        ]
        if snapshot:
            outbox.enqueue(snapshot)
        outbox.start()                          # also resends what was buffered before a restart

        _LOGGER.info(
            "HA AI Push loaded. Endpoint=%s, include=%s, exclude=%s",
            endpoint,
            include,
            exclude,
        )

    async def _sync_entities_to_backend(
        self, api_key: str, thermostats: list, outdoor_sensor: str | None
    ) -> None:
        """Sync entity lists to backend. Non-fatal on failure."""
        if not api_key:
            return
        outdoor_sensors = [outdoor_sensor] if outdoor_sensor else []
        # v0.5.0 (audit P1.6): skip when the lists haven't changed since the
        # last successful sync — a plain HA restart must never give the backend
        # an opportunity to archive profiles.
        fingerprint = sync_fingerprint([str(t) for t in (thermostats or [])], outdoor_sensors)
        if self.entry.data.get(CONF_SYNC_HASH) == fingerprint:
            _LOGGER.debug("Entity lists unchanged — skipping startup sync")
            return
        # v0.7.0 (H-07): the API base, not the receiver — up to v0.6.x this went
        # to the receiver host, which 404'd, so the outdoor sensor was never bound
        sync_url = api_url(self._client.api_base if self._client else None, PATH_SYNC_ENTITIES)
        session = aiohttp_client.async_get_clientsession(self.hass)
        try:
            async with session.post(
                sync_url,
                json={"thermostats": thermostats, "outdoor_sensors": outdoor_sensors},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status >= 400:
                    _LOGGER.warning("Startup entity sync: HTTP %s from %s", resp.status, sync_url)
                else:
                    _LOGGER.debug("Startup entity sync OK")
                    self.hass.config_entries.async_update_entry(
                        self.entry,
                        data={**self.entry.data, CONF_SYNC_HASH: fingerprint},
                    )
        except Exception as err:
            _LOGGER.debug("Startup entity sync failed (non-fatal): %s", err)

    async def async_unload(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._window:
            await self._window.flush_now()       # pending snapshots go into the persisted outbox
            self._window = None
        if self.outbox:
            await self.outbox.async_stop()       # saves; resent on the next start
            self.outbox = None
        if self.coordinator:
            await self.coordinator.async_shutdown()
            self.coordinator = None


