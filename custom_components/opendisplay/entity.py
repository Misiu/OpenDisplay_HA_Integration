"""Base entity for OpenDisplay devices."""

from typing import Generic, TypeVar

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import EntityDescription

from .coordinator import OpenDisplayCoordinator
from .deep_sleep import deep_sleep_enabled, supports_deep_sleep

_DescriptionT = TypeVar("_DescriptionT", bound=EntityDescription)


class OpenDisplayEntity(
    PassiveBluetoothCoordinatorEntity[OpenDisplayCoordinator],
    Generic[_DescriptionT],
):
    """Base class for all OpenDisplay entities."""

    _attr_has_entity_name = True
    _attr_assumed_state = False
    entity_description: _DescriptionT

    def __init__(
        self,
        coordinator: OpenDisplayCoordinator,
        description: _DescriptionT,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}-{description.key}"

        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
        )

    @property
    def available(self) -> bool:
        """Return True when device is online or assumed online due to deep sleep."""
        if self.coordinator.available:
            self._attr_assumed_state = False
            return True

        device_config = self.coordinator.config_entry.runtime_data.device_config
        deep_sleep_active = supports_deep_sleep(device_config) and deep_sleep_enabled(
            device_config
        )
        self._attr_assumed_state = deep_sleep_active
        return deep_sleep_active
