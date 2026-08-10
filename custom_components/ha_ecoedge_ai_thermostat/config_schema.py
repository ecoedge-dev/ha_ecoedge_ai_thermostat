import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_API_KEY,
    CONF_DEBOUNCE_SECONDS,
    CONF_EMAIL,
    CONF_ENDPOINT,
    CONF_EXCLUDE,
    CONF_HOME_ID,
    CONF_INCLUDE,
    CONF_OUTDOOR_SENSOR,
    CONF_TIMEOUT_SECONDS,
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
)

DOMAIN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENDPOINT): cv.url,
        vol.Optional(CONF_HOME_ID): cv.string,
        vol.Optional(CONF_API_KEY): cv.string,
        vol.Optional(CONF_INCLUDE, default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_EXCLUDE, default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_OUTDOOR_SENSOR): cv.string,
        vol.Optional(CONF_DEBOUNCE_SECONDS, default=DEFAULT_DEBOUNCE_SECONDS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=3600)
        ),
        vol.Optional(CONF_TIMEOUT_SECONDS, default=DEFAULT_TIMEOUT_SECONDS): vol.All(
            vol.Coerce(int), vol.Range(min=3, max=120)
        ),
        vol.Optional(CONF_EMAIL): cv.string,
    }
)
