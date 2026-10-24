# Changelog

All notable changes to the EcoEdge AI Thermostat integration. Audit IDs refer to
`docs/ha_integration_audit_2026-08.md` / `docs/ha_integration_audit_2026-10.md`
in the EcoEdge backend repository.

## 0.7.0 — 2026-10-24

**Least privilege and less data.** This needs the EcoEdge backend from ecoedge-dev/ha_ai_push#89; older backends still work through the fallbacks below.

- **Profiles are read for this installation only (H-06, M-10).** The sensors now use `deviceProfiles`, which EcoEdge scopes to this installation's own device token. Before, the query picked a home by its name, so one installation's token could read the account's other homes, and renaming a home emptied its sensors. A backend without the new query gets the old one.
- **One API address (H-07).** Sign-in, token refresh, sensors, entity sync and device updates all use the API address that EcoEdge sends at sign-in and with every token refresh. Readings still go to the receiver.
  - **Fixes the entity sync and device update.** Up to v0.6.1 both went to the receiver host and failed (HTTP 404), so your configured outdoor sensor was never registered with EcoEdge.
  - **Advanced mode** has a field for another API address (staging or self-hosted).
- **Less data leaves Home Assistant (M-01).**
  - Only the attributes the thermal model uses are sent: temperatures, set-points, HVAC action and modes, limits, units and the display name. Vendor extras such as serial numbers, IP addresses or battery levels are no longer sent.
  - Latitude, longitude, location name and timezone are no longer part of every upload. They go to EcoEdge only when you sign in.
- **YAML is deprecated (M-02).** A `configuration.yaml` block raises a Repair issue, because it stores your password in plain text. It is no longer re-imported at every start when the integration is already set up in the UI; before, that meant a sign-in at every restart.
- **Outdated-version Repair issue (M-08).** When EcoEdge reports a newer minimum version than the one installed, Home Assistant shows a Repair issue with a link to the releases.
- **Tests:** 43 pass. New: attribute allowlist and no location in batches, the `deviceProfiles` fallback, API base routing with a separate receiver, the contract stored from login and refresh without a reload, the advanced API base, the YAML issue without re-import, and the outdated issue.

## 0.6.1 — 2026-10-24

**No needless password prompts.**

- **Token refresh failures are classified (H-04).**
  - A rejected refresh token (HTTP 400/401/403) starts re-authentication, as before.
  - When EcoEdge is unreachable (5xx, timeout, network), the readings stay in the outbox and the refresh is retried with backoff. No password prompt.
- **The profile poll renews an expired access token (H-05).** It uses the same refresh as the uploads, instead of asking for re-authentication straight away. The new token is saved in the entry. If the refresh is only unavailable, the sensors report unavailable without a prompt.
- **Passwords are sent exactly as typed (M-04).** Leading and trailing spaces were stripped before, which locked out such passwords.
- **A refused platform unload no longer tears down the runtime half-way (M-05).**
- **Tests:** H-04 and H-05 are normal tests now, so no expected failures remain. 33 tests pass, and each has a 60 s timeout.

## 0.6.0 — 2026-10-24

**Reliable delivery.** This needs the EcoEdge backend with idempotent ingest, ecoedge-dev/ha_ai_push#87.

- **No reading is lost during an outage (H-02).** Batches go into a persistent outbox (Home Assistant storage), each with a stable `batch_id`, and are delivered oldest first. A batch is kept until EcoEdge accepts it, and it survives Home Assistant restarts. Bounded to 24 h or 5,000 readings; anything dropped is counted.
- **An upload in progress is never cancelled (H-01).** Collecting readings and delivering them are now separate.
- **Every reading is kept (H-03).** Each state change is queued as a snapshot taken at the event, so the batching window can be longer. The option is renamed **Batching window**, the default is now 20 s (was 3 s), and a batch is sent at the latest 60 s after its first change. Existing installs on the old default move to 20 s (config entry v5); a value you chose yourself is kept. The result is about 10–20× fewer requests with no readings lost.
- **Smarter retries (M-03).**
  - `429` / `503` with `Retry-After` are honoured.
  - Other transient errors back off exponentially with jitter, up to 5 min.
  - Permanent 4xx errors drop the batch without retrying.
  - A 401 still refreshes the token. When the refresh is rejected, the outbox pauses, keeps its batches and resends them after re-authentication.
- The outbox is deleted when the integration is removed.

## 0.5.2 — 2026-10-24

No behaviour change.

- **Test suite:** `pytest-homeassistant-custom-component` (Home Assistant 2026.10.0), run with `make test` in Docker and in CI. It covers the config, options and reauth flows (including the 0.4.0 options-flow regression), the entry migration, the entity filter, debounced batching, token refresh, and the sensors and their availability.
- **Known issues, documented as expected-failure tests:** each of these becomes a normal test when it's fixed.
  - **H-01:** a new state change cancels an upload in progress.
  - **H-02:** readings from a backend outage are dropped after three attempts.
  - **H-04:** a transient token-refresh error starts re-authentication.
  - **H-05:** an expired access token on the profile poll starts re-authentication, although the refresh token is valid.
- **CI:** every action is pinned to a commit SHA, with a read-only token, and the tests run on every push and pull request. Ruff now also checks `tests/`.
- **README:** a development section, and the manual-install file list is corrected.

## 0.5.1 — 2026-08-10

- Reload the runtime when a flow rotates credentials (the running coordinator kept polling with the revoked token).

## 0.5.0 — 2026-08-10

- `DataUpdateCoordinator` replaces the hand-rolled profile fetcher; sensors are unavailable when the poll fails.
- Debounce max-wait of 30 s, so an event storm can no longer postpone the push forever.
- Entity lists are synced only when they change; `unique_id` is now the backend `client_id` (entry migration to version 4).
- The "Rotate access token" option really rotates (`force`).
- Integration version reported with each push.

## 0.4.0 — 2026-08-10

- Saving options without a password no longer drops `refresh_token` / `home_id`.
- Re-authentication flow when the refresh token is rejected.
- Default data collection narrowed to climate entities plus the configured outdoor sensor.
- Concurrent token refreshes are serialized.
- CI: hassfest, HACS validation, ruff.

## 0.3.1 — 2026-04-08

- Clean up server-side data on entity removal, fix the sync URL, clearer logging.

## 0.3.0 — 2026-04-08

- Entity sync on reconfigure and on startup.
