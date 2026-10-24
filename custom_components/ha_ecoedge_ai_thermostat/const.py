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

# v0.7.0 (audit H-07): every API call derives from one base. The backend sends
# it at login and on every token refresh; entries without it use the default.
# Pushes keep going to `endpoint` (the receiver, from the login's ha_endpoint).
CONF_API_BASE = "api_base"
# The oldest integration version the backend supports (audit M-08)
CONF_MIN_VERSION = "min_supported_version"
DEFAULT_API_BASE = "https://ha.ecoedge.io"
PATH_LOGIN = "/api/auth/login"
PATH_REFRESH = "/api/auth/refresh"
PATH_DEVICE_UPDATE = "/api/auth/token"
PATH_GRAPHQL = "/graphql/"
PATH_SYNC_ENTITIES = "/api/device/sync-entities"


def api_url(api_base: str | None, path: str) -> str:
    return f"{(api_base or DEFAULT_API_BASE).rstrip('/')}{path}"


# v0.7.0 (audit M-01): the only state attributes sent to EcoEdge — what the
# thermal model reads, nothing vendor-specific or identifying.
ATTRIBUTE_ALLOWLIST = frozenset({
    "current_temperature",
    "temperature",
    "target_temp_low",
    "target_temp_high",
    "hvac_action",
    "hvac_modes",
    "min_temp",
    "max_temp",
    "target_temp_step",
    "unit_of_measurement",
    "device_class",
    "friendly_name",
})

RELEASES_URL = "https://github.com/ecoedge-dev/ha_ecoedge_ai_thermostat/releases"

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
