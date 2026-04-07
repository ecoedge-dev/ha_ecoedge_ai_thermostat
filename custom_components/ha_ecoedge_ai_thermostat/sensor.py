"""EcoEdge AI Thermostat — sensor platform.

Creates 5 sensor entities per tracked thermostat, populated from the
EcoEdge GraphQL API via ProfileFetcher (updated after each push cycle).
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .profile_fetcher import ProfileFetcher

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    fetcher: ProfileFetcher = runtime["fetcher"]

    registered: set[str] = set()

    def _make_sensors(entity_id: str) -> list[SensorEntity]:
        return [
            AiSetpointSensor(fetcher, entry.entry_id, entity_id),
            ModelSensor(fetcher, entry.entry_id, entity_id),
            KPerHourSensor(fetcher, entry.entry_id, entity_id),
            ConfidenceSensor(fetcher, entry.entry_id, entity_id),
            SavingEst7dSensor(fetcher, entry.entry_id, entity_id),
        ]

    @callback
    def _on_data_update(data: Dict[str, Any]) -> None:
        """Add sensor entities for any newly discovered thermostats."""
        new_entities = []
        for entity_id in data:
            if entity_id not in registered:
                registered.add(entity_id)
                new_entities.extend(_make_sensors(entity_id))
        if new_entities:
            _LOGGER.debug("EcoEdge sensors: registering %d new entity/entities", len(new_entities))
            async_add_entities(new_entities)

    fetcher.add_listener(_on_data_update)

    # Seed from data already available at setup time.
    if fetcher.data:
        _on_data_update(fetcher.data)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class _EcoEdgeSensor(SensorEntity):
    """Base for all EcoEdge profile sensors."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, fetcher: ProfileFetcher, entry_id: str, thermostat_entity_id: str) -> None:
        self._fetcher = fetcher
        self._thermostat_entity_id = thermostat_entity_id
        display_name = (
            thermostat_entity_id.replace("climate.", "")
            .replace("_", " ")
            .title()
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{thermostat_entity_id}")},
            name=display_name,
            manufacturer="EcoEdge",
            model="EcoEdge AI Thermostat",
        )
        self._attr_unique_id = f"{entry_id}_{thermostat_entity_id}_{self._sensor_key}"

    @property
    def _sensor_key(self) -> str:
        raise NotImplementedError

    @property
    def _profile(self) -> dict | None:
        return self._fetcher.data.get(self._thermostat_entity_id)

    def _on_data_update(self, _data: Dict[str, Any]) -> None:
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        self._fetcher.add_listener(self._on_data_update)

    async def async_will_remove_from_hass(self) -> None:
        try:
            self._fetcher._listeners.remove(self._on_data_update)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Concrete sensors
# ---------------------------------------------------------------------------

class AiSetpointSensor(_EcoEdgeSensor):
    """Current AI-computed target temperature setpoint."""

    _sensor_key = "ai_setpoint"
    _attr_name = "AI Setpoint"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:thermometer-auto"

    @property
    def native_value(self) -> float | None:
        p = self._profile
        if not p:
            return None
        if p.get("mlBlendActive") and p.get("mlBlendedSetpoint") is not None:
            return round(float(p["mlBlendedSetpoint"]), 1)
        sp = p.get("aiSetpoint")
        return round(float(sp), 1) if sp is not None else None

    @property
    def extra_state_attributes(self) -> dict:
        p = self._profile or {}
        attrs: dict = {}
        if p.get("mlBlendActive"):
            attrs["ml_blend_active"] = True
            if p.get("mlBlendedSetpoint") is not None:
                attrs["ml_blended_setpoint"] = p["mlBlendedSetpoint"]
        return attrs


class ModelSensor(_EcoEdgeSensor):
    """Thermal model currently in use (RC / KQ / ✦ ML)."""

    _sensor_key = "model"
    _attr_name = "Model"
    _attr_icon = "mdi:brain"

    @property
    def native_value(self) -> str | None:
        p = self._profile
        if not p:
            return None
        model = p.get("modelUsed") or "—"
        if p.get("mlBlendActive"):
            return f"✦ ML ({model})"
        return model


class KPerHourSensor(_EcoEdgeSensor):
    """Heat loss coefficient k (°C/h) from the fitted thermal model."""

    _sensor_key = "k_per_hour"
    _attr_name = "Heat Loss k/h"
    _attr_native_unit_of_measurement = "°C/h"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:home-thermometer-outline"
    _attr_suggested_display_precision = 3

    @property
    def native_value(self) -> float | None:
        p = self._profile
        if not p:
            return None
        k = p.get("rcKPerHour")
        return round(float(k), 4) if k is not None else None


class ConfidenceSensor(_EcoEdgeSensor):
    """Model confidence score (0–100 %)."""

    _sensor_key = "confidence"
    _attr_name = "Confidence"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-bell-curve-cumulative"
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        p = self._profile
        if not p:
            return None
        c = p.get("confidence")
        if c is None:
            return None
        return round(float(c) * 100, 1)


class SavingEst7dSensor(_EcoEdgeSensor):
    """7-day rolling average energy saving estimate (%)."""

    _sensor_key = "saving_est_7d"
    _attr_name = "Saving Est. 7d"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:leaf"
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        p = self._profile
        if not p:
            return None
        s = p.get("savingsPct7d")
        return round(float(s), 1) if s is not None else None
