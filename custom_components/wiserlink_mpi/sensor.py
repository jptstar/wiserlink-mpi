"""Energy, power and volume sensors exposed by a WiserLink MPI."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import WiserLinkCoordinator
from .meter import (
    is_gas_meter,
    is_volume_meter,
    is_water_meter,
    meter_enabled,
    meter_name,
    normalized_energy_unit,
    normalized_power_unit,
)


@dataclass(frozen=True)
class Metric:
    """Description of one numeric field exposed by a UsageMeter entry."""

    field: str
    label: str
    unit: str
    device_class: SensorDeviceClass
    state_class: SensorStateClass
    validity_field: str


POWER_METRIC = Metric(
    "Power",
    "Puissance",
    UnitOfPower.WATT,
    SensorDeviceClass.POWER,
    SensorStateClass.MEASUREMENT,
    "PowerValidity",
)

ENERGY_METRIC = Metric(
    "EnergyConsumed",
    "Énergie",
    UnitOfEnergy.KILO_WATT_HOUR,
    SensorDeviceClass.ENERGY,
    SensorStateClass.TOTAL_INCREASING,
    "EnergyValidity",
)

VOLUME_METRIC = Metric(
    "EnergyConsumed",
    "Volume",
    UnitOfVolume.CUBIC_METERS,
    SensorDeviceClass.VOLUME,
    SensorStateClass.TOTAL_INCREASING,
    "EnergyValidity",
)


def _metrics_for_meter(meter: dict) -> tuple[Metric, ...]:
    """Return metrics from the API units instead of from a fixed list index."""
    metrics: list[Metric] = []

    if "Power" in meter and normalized_power_unit(meter) == "w":
        metrics.append(POWER_METRIC)

    if "EnergyConsumed" in meter:
        if is_volume_meter(meter):
            metrics.append(VOLUME_METRIC)
        else:
            unit = normalized_energy_unit(meter)
            if unit in {"", "kwh"}:
                metrics.append(ENERGY_METRIC)
            elif unit == "wh":
                metrics.append(
                    Metric(
                        "EnergyConsumed",
                        "Énergie",
                        UnitOfEnergy.WATT_HOUR,
                        SensorDeviceClass.ENERGY,
                        SensorStateClass.TOTAL_INCREASING,
                        "EnergyValidity",
                    )
                )
            else:
                metrics.append(
                    Metric(
                        "EnergyConsumed",
                        "Énergie",
                        meter.get("Unit_Energy") or UnitOfEnergy.KILO_WATT_HOUR,
                        SensorDeviceClass.ENERGY,
                        SensorStateClass.TOTAL_INCREASING,
                        "EnergyValidity",
                    )
                )

    return tuple(metrics)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create entities for the enabled meters returned by the MPI."""
    coordinator: WiserLinkCoordinator = entry.runtime_data
    meters = [
        meter
        for meter in coordinator.data.get("UsageMeterList", [])
        if isinstance(meter, dict)
    ]
    settings = {**entry.data, **entry.options}

    _remove_obsolete_fluid_power_entities(hass, entry, meters)

    async_add_entities(
        WiserLinkSensor(coordinator, entry, index, meter, metric)
        for index, meter in enumerate(meters)
        if meter_enabled(settings, index, meter, meters)
        for metric in _metrics_for_meter(meter)
    )

    async_add_entities(
        [
            WiserLinkDiagnosticSensor(
                coordinator,
                entry,
                "mip_serial",
                "MIP Numéro de série",
                lambda data: data.get("_mip_identification", {}).get(
                    "Serial_Number"
                ),
            ),
            WiserLinkDiagnosticSensor(
                coordinator,
                entry,
                "mip_firmware",
                "MIP Version du logiciel",
                lambda data: data.get("_mip_identification", {}).get(
                    "Firmware_Version"
                ),
            ),
            WiserLinkDiagnosticSensor(
                coordinator,
                entry,
                "mip_webpage",
                "MIP Version de la page web",
                lambda data: data.get("_mip_identification", {}).get(
                    "Webpage_Version"
                ),
            ),
            WiserLinkDiagnosticSensor(
                coordinator,
                entry,
                "em5_status",
                "EM5 Statut",
                lambda data: {"Nominal": "Normal"}.get(
                    data.get("_sem_identification", {}).get("Status"),
                    data.get("_sem_identification", {}).get("Status"),
                ),
            ),
            WiserLinkDiagnosticSensor(
                coordinator,
                entry,
                "em5_serial",
                "EM5 Numéro de série",
                lambda data: data.get("_sem_identification", {}).get(
                    "SerialNumber"
                ),
            ),
            WiserLinkDiagnosticSensor(
                coordinator,
                entry,
                "em5_monitoring_firmware",
                "EM5 Version du logiciel",
                lambda data: data.get("_sem_identification", {}).get(
                    "SWVersionMonitoring"
                ),
            ),
            WiserLinkDiagnosticSensor(
                coordinator,
                entry,
                "em5_metering_firmware",
                "EM5 Version logiciel mesure",
                lambda data: data.get("_sem_identification", {}).get(
                    "SWVersionMetering"
                ),
            ),
            WiserLinkDiagnosticSensor(
                coordinator,
                entry,
                "mpr_serial",
                "MPR Numéro de série",
                lambda data: _mpr_extension_value(data, "SerialNumber"),
            ),
            WiserLinkDiagnosticSensor(
                coordinator,
                entry,
                "mpr_firmware",
                "MPR Version du logiciel",
                lambda data: _mpr_extension_value(data, "SWVersionMain"),
            ),
        ]
    )
    async_add_entities(
        WiserLinkMprConfigurationSensor(coordinator, entry, meter["Id"])
        for meter in sorted(
            coordinator.data.get("_mpr_instances", []),
            key=lambda item: item.get("Id", 999),
        )
        if meter.get("Id") is not None
    )
    async_add_entities([WiserLinkEventsSensor(coordinator, entry)])


def _mpr_extension_value(data: dict, field: str) -> str | None:
    """Return a field from the installed MPR extension."""
    extensions = data.get("_sem_identification", {}).get("Extensions", [])
    return next(
        (item.get(field) for item in extensions if item.get("Type") == "MPR"),
        None,
    )


class WiserLinkDiagnosticSensor(
    CoordinatorEntity[WiserLinkCoordinator], SensorEntity
):
    """A textual diagnostic value reported by the MPI."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, suffix, name, value_fn) -> None:
        super().__init__(coordinator)
        self._value_fn = value_fn
        self._attr_name = name
        self._attr_unique_id = f"{entry.unique_id}_{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name="WiserLink MPI",
            manufacturer="Schneider Electric",
            model="WiserLink MPI",
        )

    @property
    def native_value(self) -> str | None:
        """Return the diagnostic state."""
        return self._value_fn(self.coordinator.data)


class WiserLinkEventsSensor(
    CoordinatorEntity[WiserLinkCoordinator], SensorEntity
):
    """Expose the latest event message and five most recent events."""

    _attr_has_entity_name = True
    _attr_name = "Événements"
    _attr_icon = "mdi:format-list-bulleted"
    _attr_entity_category = EntityCategory.DIAGNIC

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_events"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name="WiserLink MPI",
            manufacturer="Schneider Electric",
            model="WiserLink MPI",
        )

    @property
    def native_value(self) -> str | None:
        """Return the latest event description as the visible state."""
        events = self.coordinator.data.get("_events", {}).get("EventList", [])
        return _event_description(events[-1])[:255] if events else None

    @property
    def extra_state_attributes(self) -> dict:
        """Return the five latest events as a compact table-like attribute."""
        events = self.coordinator.data.get("_events", {}).get("EventList", [])
        recent = list(reversed(events[-5:]))
        return {
            "nombre_total": self.coordinator.data.get("_events", {}).get(
                "TotalNB"
            ),
            "evenements": [
                {
                    "Id": event.get("Id", index),
                    "Date&Heure": _format_event_time(event.get("Timestamp")),
                    "Description": _event_description(event),
                }
                for index, event in enumerate(recent, 1)
            ],
        }


class WiserLinkMprConfigurationSensor(
    CoordinatorEntity[WiserLinkCoordinator], SensorEntity
):
    """Expose the configuration and radio quality of one MPR meter."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator, entry, meter_id) -> None:
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._attr_name = f"MPR {meter_id} Configuration"
        self._attr_unique_id = f"{entry.unique_id}_mpr_{meter_id}_configuration"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name="WiserLink MPI",
            manufacturer="Schneider Electric",
            model="WiserLink MPI",
        )

    def _meter(self) -> dict | None:
        return next(
            (
                meter
                for meter in self.coordinator.data.get("_mpr_instances", [])
                if meter.get("Id") == self._meter_id
            ),
            None,
        )

    @property
    def native_value(self) -> str | None:
        """Use the meter type as the compact state."""
        meter = self._meter()
        return meter.get("Type") if meter else None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the configurable MPR fields and signal quality."""
        meter = self._meter() or {}
        return {
            "usage_rt2012": meter.get("Usage"),
            "poids_impulsion": meter.get("PulseWeight"),
            "unite_impulsion": meter.get("PulseWeightUnit"),
            "adresse_radio": meter.get("RfAddress"),
            "qualite_signal": meter.get("SignalQuality"),
        }


def _format_event_time(timestamp: str | None) -> str:
    """Format the MPI UTC timestamp in the Home Assistant local timezone."""
    if not timestamp:
        return "Inconnue"
    parsed = dt_util.parse_datetime(f"{timestamp}+00:00")
    if parsed is None:
        return timestamp
    return dt_util.as_local(parsed).strftime("%d/%m/%Y, %H:%M:%S")


def _event_description(event: dict) -> str:
    """Translate common events and preserve raw information for unknown ones."""
    source = event.get("Source")
    event_class = event.get("Class")
    code = event.get("Code")
    state = event.get("State")
    additional = event.get("AdditionalData") or ""
    if (source, event_class, code) == (15, 101, 3):
        return (
            "Une session web locale a été démarrée"
            if state == 1
            else "La session web locale a été arrêtée"
        )
    if (source, event_class, code) == (15, 101, 2):
        return f"Le Wiser MIP utilise maintenant l’adresse IP : {additional}".strip()
    if source == 0 and code == 9:
        return (
            "Compteur principal non détecté"
            if state == 1
            else "Compteur principal détecté de nouveau"
        )
    event_type = event.get("Type")
    if event_type:
        return f"{event_type} {additional}".strip()
    return f"Événement source {source}, code {code}, état {state}"


def _remove_obsolete_fluid_power_entities(
    hass: HomeAssistant, entry: ConfigEntry, meters: list[dict]
) -> None:
    """Remove legacy power entities for meters that are actually volumes."""
    registry = er.async_get(hass)
    entry_prefix = entry.unique_id or entry.entry_id

    for index, meter in enumerate(meters):
        if not is_volume_meter(meter):
            continue
        power_unique_id = f"{entry_prefix}_{index}_power"
        power_entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, power_unique_id
        )
        if power_entity_id is not None:
            registry.async_remove(power_entity_id)


class WiserLinkSensor(CoordinatorEntity[WiserLinkCoordinator], SensorEntity):
    """One value from a UsageMeter entry."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WiserLinkCoordinator,
        entry: ConfigEntry,
        index: int,
        meter: dict,
        metric: Metric,
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._metric = metric
        settings = {**entry.data, **entry.options}
        detected_name = meter_name(settings, meter, index)
        entry_prefix = entry.unique_id or entry.entry_id

        self._attr_name = f"{detected_name} {metric.label}"
        self._attr_unique_id = f"{entry_prefix}_{index}_{metric.field.lower()}"
        self._attr_native_unit_of_measurement = metric.unit
        self._attr_device_class = metric.device_class
        self._attr_state_class = metric.state_class

        if is_gas_meter(meter):
            self._attr_icon = "mdi:meter-gas"
        elif is_water_meter(meter):
            self._attr_icon = "mdi:water"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_prefix)},
            name="WiserLink MPI",
            manufacturer="Schneider Electric",
            model="WiserLink MPI",
            configuration_url=(
                f"http://{entry.options.get('host', entry.data['host'])}:"
                f"{entry.options.get('port', entry.data['port'])}"
            ),
        )

    def _meter(self) -> dict | None:
        """Return the current API entry at the stable list index."""
        meters = self.coordinator.data.get("UsageMeterList", [])
        if self._index >= len(meters) or not isinstance(meters[self._index], dict):
            return None
        return meters[self._index]

    @property
    def available(self) -> bool:
        """Respect coordinator state and the validity flag reported by the MPI."""
        if not super().available:
            return False
        meter = self._meter()
        if meter is None:
            return False
        validity = meter.get(self._metric.validity_field)
        if validity in (None, ""):
            return True
        return str(validity).strip().lower() not in {"0", "false"}

    @property
    def native_value(self) -> Decimal | None:
        """Return a numeric value without filtering legitimate zeroes."""
        meter = self._meter()
        if meter is None:
            return None
        value = meter.get(self._metric.field)
        try:
            return Decimal(str(value)) if value is not None else None
        except (InvalidOperation, TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the API identity so physical mapping can be verified easily."""
        meter = self._meter() or {}
        return {
            "api_index": self._index,
            "api_type": meter.get("Type"),
            "api_name": meter.get("Name"),
            "api_power_unit": meter.get("Unit_Power"),
            "api_energy_unit": meter.get("Unit_Energy"),
        }
