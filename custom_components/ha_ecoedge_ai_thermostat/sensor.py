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
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EcoEdgeCoordinator

_LOGGER = logging.getLogger(__name__)

# sensors read the coordinator's data; no per-entity requests to EcoEdge
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EcoEdgeCoordinator = entry.runtime_data.coordinator
    devices = entry.runtime_data.devices
    registered = devices.registered     # shrinks when a device is removed (audit M-06)

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
        if not coordinator.last_update_success or coordinator.data is None:
            return                          # a failed (or not yet finished) poll says nothing about absence
        devices.update(set(coordinator.data or {}))
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

    @property
    def available(self) -> bool:
        """v0.8.0 (audit M-06): unavailable while EcoEdge doesn't report this
        thermostat, instead of showing its last values."""
        return super().available and self._thermostat_entity_id in (self.coordinator.data or {})


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
    _attr_entity_category = EntityCategory.DIAGNOSTIC      # v0.8.0 (audit L-04)
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
    """Heat-loss coefficient k from the RC fit: dT/dt = −k · (T_indoor − T_outdoor).

    k is a rate per hour (1/h), the share of the indoor–outdoor difference the
    room loses per hour with the heating off; typical rooms 0.005–0.06. v0.8.1
    (issue #10): the unit was "°C/h", which read as degrees per hour.
    """

    _sensor_key = "k_per_hour"
    _attr_entity_category = EntityCategory.DIAGNOSTIC      # v0.8.0 (audit L-04)
    _attr_native_unit_of_measurement = "1/h"
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

    @property
    def extra_state_attributes(self) -> dict:
        k = (self._profile or {}).get("rcKPerHour")
        k = float(k) if k is not None else None
        return {
            "loss_pct_per_hour": round(k * 100, 2) if k is not None else None,
            "time_constant_h": round(1 / k, 1) if k else None,       # hours; 1/k
            "fitted_by": "rc",          # k always comes from the RC fit; the Model sensor may show kq
        }


class ConfidenceSensor(_EcoEdgeSensor):
    """Model confidence score (0–100 %)."""

    _sensor_key = "confidence"
    _attr_entity_category = EntityCategory.DIAGNOSTIC      # v0.8.0 (audit L-04)
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
