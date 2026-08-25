"""Connectivity and gas drift status for WiserLink MPI."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import WiserLinkCoordinator
from .meter import is_gas_meter


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        name="WiserLink MPI",
        manufacturer="Schneider Electric",
        model="EER31600",
        configuration_url=(
            f"http://{entry.options.get('host', entry.data['host'])}:"
            f"{entry.options.get('port', entry.data['port'])}"
        ),
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create connectivity, MPR and gas drift entities."""
    coordinator = entry.runtime_data
    entities = [
        WiserLinkOnlineSensor(entry.runtime_data, entry),
        WiserLinkCommunicationSensor(
            entry.runtime_data,
            entry,
            "em5_mip_communication",
            "Communication avec le Wiser MIP",
            "ComStatWithMIP",
        ),
        WiserLinkCommunicationSensor(
            entry.runtime_data,
            entry,
            "electricity_meter_communication",
            "Communication avec le compteur électrique",
            "ComStatWithTIC",
        ),
    ]
    for meter in sorted(
        coordinator.data.get("_mpr_instances", []),
        key=lambda item: item.get("Id", 999),
    ):
        meter_id = meter.get("Id")
        if meter_id is None:
            continue
        entities.extend(
            [
                WiserLinkMprBinarySensor(
                    coordinator,
                    entry,
                    meter_id,
                    "communication",
                    f"MPR {meter_id} Communication",
                    "ComStatus",
                    BinarySensorDeviceClass.CONNECTIVITY,
                ),
                WiserLinkMprBinarySensor(
                    coordinator,
                    entry,
                    meter_id,
                    "battery",
                    f"MPR {meter_id} Batterie",
                    "BatteryLevel",
                    BinarySensorDeviceClass.BATTERY,
                    invert=True,
                ),
            ]
        )

    if any(
        isinstance(meter, dict) and is_gas_meter(meter)
        for meter in coordinator.data.get("UsageMeterList", [])
    ):
        entities.append(WiserLinkGasDriftSensor(coordinator, entry))

    async_add_entities(entities)


class WiserLinkOnlineSensor(
    CoordinatorEntity[WiserLinkCoordinator], BinarySensorEntity
):
    """Report whether the latest MPI bus read succeeded."""

    _attr_has_entity_name = True
    _attr_name = "MPI Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: WiserLinkCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id}_online"
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success


class WiserLinkCommunicationSensor(
    CoordinatorEntity[WiserLinkCoordinator], BinarySensorEntity
):
    """Report a communication status exposed by the EM5."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, suffix, name, field) -> None:
        super().__init__(coordinator)
        self._field = field
        self._attr_name = name
        self._attr_unique_id = f"{entry.unique_id}_{suffix}"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get("_sem_identification", {}).get(
            self._field
        )
        return bool(value) if value is not None else None


class WiserLinkMprBinarySensor(
    CoordinatorEntity[WiserLinkCoordinator], BinarySensorEntity
):
    """Report communication or low-battery state for one MPR meter."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator,
        entry,
        meter_id,
        suffix,
        name,
        field,
        device_class,
        invert=False,
    ) -> None:
        super().__init__(coordinator)
        self._meter_id = meter_id
        self._field = field
        self._invert = invert
        self._attr_name = name
        self._attr_unique_id = f"{entry.unique_id}_mpr_{meter_id}_{suffix}"
        self._attr_device_class = device_class
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        meter = next(
            (
                item
                for item in self.coordinator.data.get("_mpr_instances", [])
                if item.get("Id") == self._meter_id
            ),
            None,
        )
        if meter is None or meter.get(self._field) is None:
            return None
        value = bool(meter[self._field])
        return not value if self._invert else value


class WiserLinkGasDriftSensor(
    CoordinatorEntity[WiserLinkCoordinator], BinarySensorEntity
):
    """Report whether the detected daily gas reading has drifted from target."""

    _attr_has_entity_name = True
    _attr_name = "Dérive relève gaz"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-alert-outline"

    def __init__(self, coordinator: WiserLinkCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_gas_reading_drift"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.gas_monitor_data.get("drift_exceeded")

    @staticmethod
    def _local_iso(value):
        if value is None:
            return None
        return dt_util.as_local(value).isoformat(timespec="seconds")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.gas_monitor_data
        return {
            "derniere_releve_detectee": self._local_iso(data.get("last_detected_at")),
            "prochaine_releve_estimee": self._local_iso(data.get("next_estimated_at")),
            "derive_minutes": data.get("drift_minutes"),
            "heure_cible": data.get("target_time"),
            "tolerance_minutes": data.get("tolerance_minutes"),
            "heure_controle": data.get("control_time"),
            "controle_automatique": data.get("automatic_control"),
            "attente_nouvelle_releve_apres_reboot": data.get(
                "waiting_for_new_reading_after_reboot"
            ),
            "index_gaz_observe_m3": data.get("raw_value"),
            "dernier_redemarrage": self._local_iso(data.get("last_reboot_at")),
            "raison_dernier_redemarrage": data.get("last_reboot_reason"),
        }
