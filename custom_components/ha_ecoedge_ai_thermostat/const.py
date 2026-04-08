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

AUTH_LOGIN_URL = "https://ha.ecoedge.io/api/auth/login"
AUTH_REFRESH_URL = "https://ha.ecoedge.io/api/auth/refresh"
GRAPHQL_URL = "https://ha.ecoedge.io/graphql/"
SYNC_ENTITIES_URL = "https://ha.ecoedge.io/api/device/sync-entities"

# Internal defaults — not exposed in the UI
DEFAULT_DEBOUNCE_SECONDS = 3
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF = 1.5
DEFAULT_REFRESH_TIMEOUT_SECONDS = 15
DEFAULT_FETCH_DELAY_SECONDS = 90
DEFAULT_FALLBACK_POLL_MINUTES = 30
