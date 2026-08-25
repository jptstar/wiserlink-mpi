"""Energy, power and volume sensors exposed by a WiserLink MPI."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from homeassistant.components.energy.data import async_get_manager
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, METER_UNIT_KWH, METER_UNIT_M3, METER_UNIT_WH
from .coordinator import WiserLinkCoordinator
from .meter import (
    is_gas_meter,
    is_water_meter,
    meter_effective_unit,
    meter_enabled,
    meter_name,
    normalized_energy_unit,
    normalized_power_unit,
)


@dataclass(frozen=True)
class Metric:
    """Description of one numeric UsageMeter field."""

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
ENERGY_WH_METRIC = Metric(
    "EnergyConsumed",
    "Énergie",
    UnitOfEnergy.WATT_HOUR,
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

_LEGACY_GAS_ENERGY_ENTITY_ID = "sensor.wiser_energy_gaz_conso"

_MPR_TYPE_NAMES = {
    "Gas Meter": "Compteur gaz",
    "Cold Water Meter": "Compteur eau froide",
    "Hot Water Meter": "Compteur eau chaude",
    "Water Meter": "Compteur eau",
    "Calorimeter": "Calorimètre",
}

_MPR_USAGE_NAMES = {
    "No usage": "Aucun usage",
    "Heating": "Chauffage",
    "Hot water": "Eau chaude",
    "Cooling": "Climatisation",
    "Sockets": "Prises",
    "Others": "Autres",
}

# Official mappings extracted from the Schneider WiserLink Web UI bundled with
# eSetup for Electrician. Each pair is (state 0, state 1).
_EVENT_SOURCE0 = {
    0: ("Fin erreur TIC", "Erreur TIC"),
    1: ("Fin erreur EM5 - mesure", "Erreur EM5 - mesure"),
    2: (
        "Fin erreur extension EM5 - court-circuit",
        "Erreur extension EM5 - court-circuit",
    ),
    3: ("Fin erreur EM5 - mémoire Flash", "Erreur EM5 - mémoire Flash"),
    4: (
        "Toutes les extensions détectées sont maintenant supportées",
        "Extension non supportée",
    ),
    5: ("Fin erreur bus extensions EM5", "Erreur bus extensions EM5"),
    7: ("Fin erreur appareil EM5", "Erreur appareil EM5"),
    9: ("Compteur principal détecté de nouveau", "Compteur principal non détecté"),
}

_EVENT_EXTENSION = {
    0: ("Fin erreur intégrité", "Erreur intégrité"),
    1: ("Fin erreur mémoire Flash", "Erreur mémoire Flash"),
    4: ("Fin erreur matérielle", "Erreur matérielle"),
    8: ("Fin défaut extension OUREA", "Défaut extension OUREA"),
}

_EVENT_MPR = {
    0: ("Versions MPE/MPR compatibles", "Incompatibilité de versions MPE/MPR"),
    1: ("Batterie MPE correcte", "Batterie MPE faible"),
    2: ("Communication MPE/MPR rétablie", "Erreur de communication MPE/MPR"),
    3: ("Qualité MPE/MPR bonne", "Qualité MPE/MPR moyenne"),
    4: ("Qualité MPE/MPR moyenne", "Qualité MPE/MPR mauvaise"),
}

_EVENT_SOURCE15_CLASS100 = {
    0: ("Communication EM5 rétablie", "Communication EM5 perdue"),
    1: ("EM5 détecté de nouveau", "EM5 non détecté"),
    2: ("Connexion cloud rétablie", "Connexion cloud perdue"),
}

_EVENT_SOURCE15_CLASS101 = {
    0: ("Wiser MIP redémarré", "Wiser MIP redémarré"),
    1: ("Le Wiser MIP utilise maintenant l’IP de secours", "Le Wiser MIP utilise maintenant l’IP de secours"),
    2: ("Le Wiser MIP utilise maintenant l’adresse IP", "Le Wiser MIP utilise maintenant l’adresse IP"),
    3: ("Session web locale terminée", "Session web locale démarrée"),
    4: ("Bouton du Wiser MIP relâché", "Bouton du Wiser MIP appuyé"),
    5: ("Historique réinitialisé après corruption mémoire", "Historique réinitialisé après corruption mémoire"),
    6: ("Débordement de l’historique", "Débordement de l’historique"),
    7: ("Journal des événements réinitialisé après corruption mémoire", "Journal des événements réinitialisé après corruption mémoire"),
    8: ("Débordement du journal des événements", "Débordement du journal des événements"),
    9: ("Système à jour", "Incohérence système détectée - mise à niveau requise"),
    10: ("Mise à niveau système terminée", "Mise à niveau système terminée"),
}

_EVENT_SOURCE15_CLASS102 = {
    0: "Réinitialisation historique de mesure - Chauffage",
    1: "Réinitialisation historique de mesure - Eau chaude",
    2: "Réinitialisation historique de mesure - Climatisation",
    3: "Réinitialisation historique de mesure - LoadSem",
    5: "Réinitialisation énergie",
    6: "Mise à jour de la référence d’index énergie pour l’usage",
}


def _entry_prefix(entry: ConfigEntry) -> str:
    return entry.unique_id or entry.entry_id


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, _entry_prefix(entry))},
        name="WiserLink MPI",
        manufacturer="Schneider Electric",
        model="WiserLink MPI",
        configuration_url=(
            f"http://{entry.options.get('host', entry.data['host'])}:"
            f"{entry.options.get('port', entry.data['port'])}"
        ),
    )


def _metrics_for_meter(
    settings: dict[str, Any], index: int, meter: dict[str, Any]
) -> tuple[Metric, ...]:
    """Build metrics from the selected unit, falling back to the Wiser API."""
    metrics: list[Metric] = []
    unit = meter_effective_unit(settings, index, meter)

    if unit != METER_UNIT_M3 and "Power" in meter and normalized_power_unit(meter) == "w":
        metrics.append(POWER_METRIC)

    if "EnergyConsumed" not in meter:
        return tuple(metrics)

    if unit == METER_UNIT_M3:
        metrics.append(VOLUME_METRIC)
    elif unit == METER_UNIT_WH:
        metrics.append(ENERGY_WH_METRIC)
    else:
        metrics.append(ENERGY_METRIC)
    return tuple(metrics)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create entities for enabled meters and diagnostics."""
    coordinator: WiserLinkCoordinator = entry.runtime_data
    meters = [
        meter
        for meter in coordinator.data.get("UsageMeterList", [])
        if isinstance(meter, dict)
    ]
    settings = {**entry.data, **entry.options}
    _remove_obsolete_volume_power_entities(hass, entry, settings, meters)
    await _migrate_energy_dashboard_gas_source(hass, entry, settings, meters)

    async_add_entities(
        WiserLinkSensor(coordinator, entry, index, meter, metric)
        for index, meter in enumerate(meters)
        if meter_enabled(settings, index, meter, meters)
        for metric in _metrics_for_meter(settings, index, meter)
    )

    diagnostics: list[tuple[str, str, Callable[[dict], Any]]] = [
        (
            "mip_serial",
            "MIP Numéro de série",
            lambda data: data.get("_mip_identification", {}).get("Serial_Number"),
        ),
        (
            "mip_firmware",
            "MIP Version du logiciel",
            lambda data: data.get("_mip_identification", {}).get("Firmware_Version"),
        ),
        (
            "mip_webpage",
            "MIP Version de la page web",
            lambda data: data.get("_mip_identification", {}).get("Webpage_Version"),
        ),
        (
            "em5_status",
            "EM5 Statut",
            lambda data: {"Nominal": "Normal"}.get(
                data.get("_sem_identification", {}).get("Status"),
                data.get("_sem_identification", {}).get("Status"),
            ),
        ),
        (
            "em5_serial",
            "EM5 Numéro de série",
            lambda data: data.get("_sem_identification", {}).get("SerialNumber"),
        ),
        (
            "em5_monitoring_firmware",
            "EM5 Version du logiciel",
            lambda data: data.get("_sem_identification", {}).get("SWVersionMonitoring"),
        ),
        (
            "em5_metering_firmware",
            "EM5 Version logiciel mesure",
            lambda data: data.get("_sem_identification", {}).get("SWVersionMetering"),
        ),
        (
            "mpr_serial",
            "MPR Numéro de série",
            lambda data: _mpr_extension_value(data, "SerialNumber"),
        ),
        (
            "mpr_firmware",
            "MPR Version du logiciel",
            lambda data: _mpr_extension_value(data, "SWVersionMain"),
        ),
    ]
    async_add_entities(
        WiserLinkDiagnosticSensor(coordinator, entry, suffix, name, value_fn)
        for suffix, name, value_fn in diagnostics
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


async def _migrate_energy_dashboard_gas_source(
    hass: HomeAssistant,
    entry: ConfigEntry,
    settings: dict[str, Any],
    meters: list[dict],
) -> None:
    """Replace the legacy gas source in Energy preferences with the WiserLink meter."""
    registry = er.async_get(hass)
    prefix = _entry_prefix(entry)
    gas_entity_ids: list[str] = []

    for index, meter in enumerate(meters):
        if (
            not meter_enabled(settings, index, meter, meters)
            or meter_effective_unit(settings, index, meter) != METER_UNIT_M3
            or not is_gas_meter(meter)
        ):
            continue
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{prefix}_{index}_energyconsumed"
        )
        if entity_id is not None:
            gas_entity_ids.append(entity_id)

    if len(gas_entity_ids) != 1:
        return

    manager = await async_get_manager(hass)
    if manager.data is None:
        return

    replacement = gas_entity_ids[0]
    changed = False
    sources = []
    for source in manager.data["energy_sources"]:
        updated_source = source.copy()
        if (
            updated_source.get("type") == "gas"
            and updated_source.get("stat_energy_from") == _LEGACY_GAS_ENERGY_ENTITY_ID
        ):
            updated_source["stat_energy_from"] = replacement
            changed = True
        sources.append(updated_source)

    if changed:
        await manager.async_update({"energy_sources": sources})


def _mpr_extension_value(data: dict, field: str) -> str | None:
    extensions = data.get("_sem_identification", {}).get("Extensions", [])
    return next(
        (item.get(field) for item in extensions if item.get("Type") == "MPR"),
        None,
    )


def _mpr_type_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return _MPR_TYPE_NAMES.get(text, text)


def _mpr_usage_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return _MPR_USAGE_NAMES.get(text, text)


class WiserLinkDiagnosticSensor(CoordinatorEntity[WiserLinkCoordinator], SensorEntity):
    """A textual diagnostic value reported by the MPI."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, suffix, name, value_fn) -> None:
        super().__init__(coordinator)
        self._value_fn = value_fn
        self._attr_name = name
        self._attr_unique_id = f"{_entry_prefix(entry)}_{suffix}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> str | None:
        return self._value_fn(self.coordinator.data)


class WiserLinkEventsSensor(CoordinatorEntity[WiserLinkCoordinator], SensorEntity):
    """Expose the latest event message and five most recent events."""

    _attr_has_entity_name = True
    _attr_name = "Événements"
    _attr_icon = "mdi:format-list-bulleted"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{_entry_prefix(entry)}_events"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> str | None:
        events = self.coordinator.data.get("_events", {}).get("EventList", [])
        return _event_description(events[-1])[:255] if events else None

    @property
    def extra_state_attributes(self) -> dict:
        events = self.coordinator.data.get("_events", {}).get("EventList", [])
        recent = list(reversed(events[-5:]))
        return {
            "nombre_total": self.coordinator.data.get("_events", {}).get("TotalNB"),
            "evenements": [
                {
                    "Id": event.get("Id", index),
                    "Date&Heure": _format_event_time(event.get("Timestamp")),
                    "Description": _event_description(event),
                    "Source": event.get("Source"),
                    "Code": event.get("Code"),
                    "Classe": event.get("Class"),
                    "Etat": event.get("State"),
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
        self._attr_name = f"Configuration MPR {meter_id}"
        self._attr_unique_id = f"{_entry_prefix(entry)}_mpr_{meter_id}_configuration"
        self._attr_device_info = _device_info(entry)

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
        meter = self._meter()
        return _mpr_type_name(meter.get("Type")) if meter else None

    @property
    def extra_state_attributes(self) -> dict:
        meter = self._meter() or {}
        return {
            "usage_rt2012": _mpr_usage_name(meter.get("Usage")),
            "poids_impulsion": meter.get("PulseWeight"),
            "unite_impulsion": meter.get("PulseWeightUnit"),
            "adresse_radio": meter.get("RfAddress"),
            "qualite_signal": meter.get("SignalQuality"),
        }


def _format_event_time(timestamp: str | None) -> str:
    if not timestamp:
        return "Inconnue"
    parsed = dt_util.parse_datetime(f"{timestamp}+00:00")
    if parsed is None:
        return timestamp
    return dt_util.as_local(parsed).strftime("%d/%m/%Y, %H:%M:%S")


def _event_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _event_pair(pair: tuple[str, str], state: int | None) -> str:
    return pair[1] if state == 1 else pair[0]


def _with_suffix(text: str, suffix: Any) -> str:
    extra = str(suffix).strip() if suffix not in (None, "") else ""
    return f"{text} : {extra}" if extra else text


def _event_description(event: dict) -> str:
    source = _event_int(event.get("Source"))
    event_class = _event_int(event.get("Class"))
    code = _event_int(event.get("Code"))
    state = _event_int(event.get("State"))
    additional = event.get("AdditionalData")
    event_type = event.get("Type")

    if source == 0 and code in _EVENT_SOURCE0:
        return _event_pair(_EVENT_SOURCE0[code], state)

    if source is not None and 1 <= source <= 4 and code in _EVENT_EXTENSION:
        return f"Extension EM5 #{source} - {_event_pair(_EVENT_EXTENSION[code], state)}"

    if source is not None and 5 <= source <= 8 and code in _EVENT_MPR:
        return _with_suffix(_event_pair(_EVENT_MPR[code], state), event_type or additional)

    if source is not None and 9 <= source <= 14 and code == 0:
        offset = source - 9
        m2i = offset // 2 + 1
        input_no = offset % 2 + 1
        if state == 1:
            return _with_suffix(
                f"Alarme détectée sur M2I #{m2i}, entrée {input_no}",
                additional or event_type,
            )
        return f"Alarme terminée sur M2I #{m2i}, entrée {input_no}"

    if source == 15:
        if event_class == 100 and code in _EVENT_SOURCE15_CLASS100:
            return _event_pair(_EVENT_SOURCE15_CLASS100[code], state)

        if event_class == 101 and code in _EVENT_SOURCE15_CLASS101:
            description = _event_pair(_EVENT_SOURCE15_CLASS101[code], state)
            if code in {1, 2, 10}:
                return _with_suffix(description, additional)
            return description

        if event_class == 102 and code in _EVENT_SOURCE15_CLASS102:
            description = _EVENT_SOURCE15_CLASS102[code]
            if code in {3, 5, 6}:
                return _with_suffix(description, additional)
            return description

    if event_type:
        return _with_suffix(str(event_type), additional)

    return (
        f"Événement source {source}, classe {event_class}, "
        f"code {code}, état {state}"
    )


def _remove_obsolete_volume_power_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    settings: dict[str, Any],
    meters: list[dict],
) -> None:
    """Remove legacy power entities for entries explicitly treated as volumes."""
    registry = er.async_get(hass)
    prefix = _entry_prefix(entry)
    for index, meter in enumerate(meters):
        if meter_effective_unit(settings, index, meter) != METER_UNIT_M3:
            continue
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{prefix}_{index}_power"
        )
        if entity_id is not None:
            registry.async_remove(entity_id)


class WiserLinkSensor(CoordinatorEntity[WiserLinkCoordinator], SensorEntity):
    """One value from one UsageMeter entry."""

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
        self._attr_name = f"{meter_name(settings, meter, index)} {metric.label}"
        self._attr_unique_id = f"{_entry_prefix(entry)}_{index}_{metric.field.lower()}"
        self._attr_native_unit_of_measurement = metric.unit
        if metric is VOLUME_METRIC and is_gas_meter(meter):
            self._attr_device_class = SensorDeviceClass.GAS
        elif metric is VOLUME_METRIC and is_water_meter(meter):
            self._attr_device_class = SensorDeviceClass.WATER
        else:
            self._attr_device_class = metric.device_class
        self._attr_state_class = metric.state_class
        if is_gas_meter(meter):
            self._attr_icon = "mdi:meter-gas"
        elif is_water_meter(meter):
            self._attr_icon = "mdi:water"
        self._attr_device_info = _device_info(entry)

    def _meter(self) -> dict | None:
        meters = self.coordinator.data.get("UsageMeterList", [])
        if self._index >= len(meters) or not isinstance(meters[self._index], dict):
            return None
        return meters[self._index]

    @property
    def available(self) -> bool:
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
        meter = self._meter()
        if meter is None:
            return None
        value = meter.get(self._metric.field)
        try:
            numeric = Decimal(str(value)) if value is not None else None
        except (InvalidOperation, TypeError, ValueError):
            return None
        if numeric is None or self._metric.field != "EnergyConsumed":
            return numeric

        source_unit = normalized_energy_unit(meter)
        if self._metric.unit == UnitOfEnergy.KILO_WATT_HOUR and source_unit == "wh":
            return numeric / Decimal("1000")
        if self._metric.unit == UnitOfEnergy.WATT_HOUR and source_unit == "kwh":
            return numeric * Decimal("1000")
        return numeric

    @property
    def extra_state_attributes(self) -> dict:
        meter = self._meter() or {}
        return {
            "api_index": self._index,
            "api_type": meter.get("Type"),
            "api_name": meter.get("Name"),
            "api_power_unit": meter.get("Unit_Power"),
            "api_energy_unit": meter.get("Unit_Energy"),
            "unite_selectionnee": self._metric.unit,
        }
