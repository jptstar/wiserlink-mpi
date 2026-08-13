"""Connectivity status for WiserLink MPI."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
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
    async_add_entities([WiserLinkOnlineSensor(entry.runtime_data, entry)])


class WiserLinkOnlineSensor(CoordinatorEntity[WiserLinkCoordinator], BinarySensorEntity):
    """Report whether the latest MPI bus read succeeded."""

    _attr_has_entity_name = True
    _attr_name = "MPI Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

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
