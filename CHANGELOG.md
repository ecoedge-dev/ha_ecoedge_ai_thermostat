# Changelog

All notable changes to the EcoEdge AI Thermostat integration. Audit IDs refer to
`docs/ha_integration_audit_2026-08.md` / `docs/ha_integration_audit_2026-10.md`
in the EcoEdge backend repository.

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
