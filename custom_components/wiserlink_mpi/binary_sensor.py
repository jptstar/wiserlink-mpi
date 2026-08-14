"""Connectivity status for WiserLink MPI."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WiserLinkCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the MPI connectivity entity."""
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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name="WiserLink MPI",
            manufacturer="Schneider Electric",
            model="EER31600",
            configuration_url=(
                f"http://{entry.options.get('host', entry.data['host'])}:"
                f"{entry.options.get('port', entry.data['port'])}"
            ),
        )

    @property
    def available(self) -> bool:
        """Keep the entity itself available so it can explicitly report offline."""
        return True

    @property
    def is_on(self) -> bool:
        """Return the result of the latest coordinated read."""
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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name="WiserLink MPI",
            manufacturer="Schneider Electric",
            model="WiserLink MPI",
        )

    @property
    def is_on(self) -> bool | None:
        """Return the reported communication state."""
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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name="WiserLink MPI",
            manufacturer="Schneider Electric",
            model="WiserLink MPI",
        )

    @property
    def is_on(self) -> bool | None:
        """Return connectivity, or an alert when the battery is low."""
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
