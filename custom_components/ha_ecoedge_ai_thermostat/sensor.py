"""EcoEdge AI Thermostat — sensor platform.

Creates 5 sensor entities per tracked thermostat, populated from the
EcoEdge GraphQL API via EcoEdgeCoordinator (refreshed after each push cycle
plus a 30-minute fallback poll).
"""
from __future__ import annotations

import logging

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
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EcoEdgeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EcoEdgeCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    registered: set[str] = set()

    def _make_sensors(entity_id: str) -> list[SensorEntity]:
        return [
            AiSetpointSensor(coordinator, entry.entry_id, entity_id),
            ModelSensor(coordinator, entry.entry_id, entity_id),
            KPerHourSensor(coordinator, entry.entry_id, entity_id),
            ConfidenceSensor(coordinator, entry.entry_id, entity_id),
            SavingEst7dSensor(coordinator, entry.entry_id, entity_id),
        ]

    @callback
    def _register_new_thermostats() -> None:
        """Add sensor entities for any newly discovered thermostats."""
        new_entities = []
        for entity_id in coordinator.data or {}:
            if entity_id not in registered:
                registered.add(entity_id)
                new_entities.extend(_make_sensors(entity_id))
        if new_entities:
            _LOGGER.debug("EcoEdge sensors: registering %d new entity/entities", len(new_entities))
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_register_new_thermostats))
    _register_new_thermostats()


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class _EcoEdgeSensor(CoordinatorEntity[EcoEdgeCoordinator], SensorEntity):
    """Base for all EcoEdge profile sensors.

    Availability comes from the coordinator: sensors go unavailable as soon as
    a poll fails (auth, network, backend error) instead of showing stale data.
    """

    _attr_has_entity_name = True
    _sensor_key: str

    def __init__(
        self,
        coordinator: EcoEdgeCoordinator,
        entry_id: str,
        thermostat_entity_id: str,
    ) -> None:
        super().__init__(coordinator)
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
        self._attr_translation_key = self._sensor_key

    @property
    def _profile(self) -> dict | None:
        return (self.coordinator.data or {}).get(self._thermostat_entity_id)


# ---------------------------------------------------------------------------
# Concrete sensors
# ---------------------------------------------------------------------------

class AiSetpointSensor(_EcoEdgeSensor):
    """Current AI-computed target temperature setpoint."""

    _sensor_key = "ai_setpoint"
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
        sp = p.get("decisionSafeSetpoint")
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
    """Thermal model currently in use.

    v0.5.0 (audit M3): the state is the bare model id (rc / kq / ml) so
    automations can match on it; decoration lives in attributes.
    """

    _sensor_key = "model"
    _attr_icon = "mdi:brain"

    @property
    def native_value(self) -> str | None:
        p = self._profile
        if not p:
            return None
        if p.get("mlBlendActive"):
            return "ml"
        model = p.get("predictionModel")
        return str(model).lower() if model else None

    @property
    def extra_state_attributes(self) -> dict:
        p = self._profile or {}
        model = p.get("predictionModel")
        return {
            "base_model": str(model).lower() if model else None,
            "ml_blend_active": bool(p.get("mlBlendActive")),
        }


class KPerHourSensor(_EcoEdgeSensor):
    """Heat loss coefficient k (°C/h) from the fitted thermal model."""

    _sensor_key = "k_per_hour"
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
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-bell-curve-cumulative"
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        p = self._profile
        if not p:
            return None
        c = p.get("rcConfidence")
        if c is None:
            return None
        return round(float(c) * 100, 1)


class SavingEst7dSensor(_EcoEdgeSensor):
    """7-day rolling average energy saving estimate (%)."""

    _sensor_key = "saving_est_7d"
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
