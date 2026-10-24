"""Base entity for Website Health."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_BASE_URL, DOMAIN
from .coordinator import WebsiteHealthCoordinator


class WebsiteHealthEntity(CoordinatorEntity[WebsiteHealthCoordinator]):
    """Base for entities backed by one website's coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WebsiteHealthCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        identifier = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{identifier}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Website Health",
            model="Website",
            name=entry.title,
            configuration_url=entry.data[CONF_BASE_URL],
        )

    @property
    def available(self) -> bool:
        """Unavailable until the first sweep, which runs after setup, finishes."""
        return super().available and self.coordinator.data is not None
