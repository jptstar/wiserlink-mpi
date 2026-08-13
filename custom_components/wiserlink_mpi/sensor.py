"""Energy and power sensors exposed by a WiserLink MPI."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ENABLE_GAS,
    CONF_ENABLE_WATER,
    CONF_LOAD_NAME_PREFIX,
    DEFAULT_METER_NAMES,
    DOMAIN,
)
from .coordinator import WiserLinkCoordinator


@dataclass(frozen=True)
class Metric:
    field: str
    label: str
    unit: str
    device_class: SensorDeviceClass
    state_class: SensorStateClass


METRICS = (
    Metric("Power", "Puissance", UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT),
    Metric("EnergyConsumed", "Énergie", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING),
)

FLUID_METRICS = (
    Metric(
        "EnergyConsumed",
        "Volume",
        UnitOfVolume.CUBIC_METERS,
        SensorDeviceClass.VOLUME,
        SensorStateClass.TOTAL_INCREASING,
    ),
)


def _metrics_for_meter(index: int) -> tuple[Metric, ...]:
    """Return metrics matching the physical type of a meter."""
    if index in (10, 11):
        return FLUID_METRICS
    return METRICS


def _optional_meter_enabled(entry: ConfigEntry, index: int) -> bool:
    """Return whether an optional fluid module should expose entities.

    Missing gas/water modules are a normal installation variant. They never
    affect coordinator availability or the MPI Online entity.
    """
    if index == 10:
        return entry.options.get(
            CONF_ENABLE_GAS, entry.data.get(CONF_ENABLE_GAS, False)
        )
    if index == 11:
        return entry.options.get(
            CONF_ENABLE_WATER, entry.data.get(CONF_ENABLE_WATER, False)
        )
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create two entities for every meter returned by the MPI."""
    coordinator: WiserLinkCoordinator = entry.runtime_data
    meters = coordinator.data.get("UsageMeterList", [])
    async_add_entities(
        WiserLinkSensor(coordinator, entry, index, metric)
        for index, meter in enumerate(meters)
        if _optional_meter_enabled(entry, index)
        for metric in _metrics_for_meter(index)
        if isinstance(meter, dict) and metric.field in meter
    )


class WiserLinkSensor(CoordinatorEntity[WiserLinkCoordinator], SensorEntity):
    """One value from a UsageMeter entry."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WiserLinkCoordinator,
        entry: ConfigEntry,
        index: int,
        metric: Metric,
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._metric = metric
        custom_name = entry.options.get(f"{CONF_LOAD_NAME_PREFIX}{index}")
        meter_name = custom_name or DEFAULT_METER_NAMES.get(
            index, f"Voie {index + 1}"
        )
        self._attr_name = f"{meter_name} {metric.label}"
        self._attr_unique_id = f"{entry.unique_id}_{index}_{metric.field.lower()}"
        self._attr_native_unit_of_measurement = metric.unit
        self._attr_device_class = metric.device_class
        self._attr_state_class = metric.state_class
        if index == 10:
            self._attr_icon = "mdi:meter-gas"
        elif index == 11:
            self._attr_icon = "mdi:water"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name="WiserLink MPI",
            manufacturer="Schneider Electric",
            model="WiserLink MPI",
            configuration_url=(
                f"http://{entry.options.get('host', entry.data['host'])}:"
                f"{entry.options.get('port', entry.data['port'])}"
            ),
        )

    @property
    def native_value(self) -> Decimal | None:
        """Return a numeric value without filtering legitimate zeroes."""
        try:
            value = self.coordinator.data["UsageMeterList"][self._index].get(self._metric.field)
            return Decimal(str(value)) if value is not None else None
        except (IndexError, KeyError, InvalidOperation, TypeError):
            return None
