DOMAIN = "ha_ecoedge_ai_thermostat"

CONF_ENDPOINT = "endpoint"
CONF_API_KEY = "api_key"
CONF_INCLUDE = "include_entities"
CONF_EXCLUDE = "exclude_entities"
CONF_DEBOUNCE_SECONDS = "debounce_seconds"
CONF_TIMEOUT_SECONDS = "timeout_seconds"
CONF_OUTDOOR_SENSOR = "outdoor_sensor"

CONF_HOME_ID = "home_id"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_ROTATE_TOKEN = "rotate_token"
CONF_CLIENT_ID = "client_id"
CONF_REFRESH_TOKEN = "refresh_token"
# Fingerprint of the last successfully synced entity lists (audit P1.6) —
# bookkeeping, never shown in a form.
CONF_SYNC_HASH = "sync_entities_hash"

AUTH_LOGIN_URL = "https://ha.ecoedge.io/api/auth/login"
AUTH_REFRESH_URL = "https://ha.ecoedge.io/api/auth/refresh"
GRAPHQL_URL = "https://ha.ecoedge.io/graphql/"
SYNC_ENTITIES_URL = "https://ha.ecoedge.io/api/device/sync-entities"

# Internal defaults — not exposed in the UI
# v0.6.0: the debounce is a batching window over state SNAPSHOTS (no reading
# is lost inside it), so it can be longer than the old 3 s: ~10-20x fewer
# requests (audit H-03). Entries on the old default are migrated (entry v5).
DEFAULT_DEBOUNCE_SECONDS = 20
LEGACY_DEBOUNCE_SECONDS = 3
# Hard flush bound (audit H1): a sustained event storm keeps resetting the
# trailing debounce, so a batch is cut at latest this long after its first
# snapshot.
DEFAULT_DEBOUNCE_MAX_WAIT_SECONDS = 60
DEFAULT_TIMEOUT_SECONDS = 10
# Outbox (audit H-02): readings buffered during outages, resent oldest first.
OUTBOX_MAX_ITEMS = 5000
OUTBOX_MAX_AGE_SECONDS = 24 * 3600
OUTBOX_BACKOFF_BASE = 2.0
OUTBOX_BACKOFF_MAX = 300.0
DEFAULT_REFRESH_TIMEOUT_SECONDS = 15
DEFAULT_FETCH_DELAY_SECONDS = 90
DEFAULT_FALLBACK_POLL_MINUTES = 30
