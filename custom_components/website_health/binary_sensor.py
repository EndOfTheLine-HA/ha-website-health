"""Binary sensor platform for Website Health."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WebsiteHealthConfigEntry
from .checker import CheckSpec
from .const import STATUS_DOWN, STATUS_INTERNET_DOWN
from .coordinator import WebsiteHealthCoordinator
from .entity import WebsiteHealthEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WebsiteHealthConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the site problem sensor and one sensor per check."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [ProblemBinarySensor(coordinator)]
    entities.extend(CheckBinarySensor(coordinator, spec) for spec in coordinator.checks)
    async_add_entities(entities)


class _HiddenWhenInternetDown(WebsiteHealthEntity, BinarySensorEntity):
    """Unavailable while the internet is down: nothing about the site is known."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def available(self) -> bool:
        """Never claim on or off for the site during a home outage."""
        return (
            super().available and self.coordinator.data.status != STATUS_INTERNET_DOWN
        )


class ProblemBinarySensor(_HiddenWhenInternetDown):
    """On when the website is down. The entity automations should use."""

    _attr_translation_key = "problem"

    def __init__(self, coordinator: WebsiteHealthCoordinator) -> None:
        super().__init__(coordinator, "problem")

    @property
    def is_on(self) -> bool:
        """Return whether the site is down after the failed-sweeps threshold."""
        return self.coordinator.data.status == STATUS_DOWN


class CheckBinarySensor(_HiddenWhenInternetDown):
    """On when one check failed in the latest sweep."""

    # Used for icons only; the name comes from the user's label below.
    _attr_translation_key = "check"

    def __init__(self, coordinator: WebsiteHealthCoordinator, spec: CheckSpec) -> None:
        super().__init__(coordinator, f"check_{spec.key}")
        self._spec = spec
        self._attr_name = spec.label

    def _result(self):
        return self.coordinator.data.sweep.checks.get(self._spec.key)

    @property
    def available(self) -> bool:
        """Also unavailable if this check was not part of the sweep."""
        return super().available and self._result() is not None

    @property
    def is_on(self) -> bool:
        """Return whether this check failed."""
        return not self._result().ok

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Describe the last attempt. Response bodies are never included."""
        result = self._result()
        if result is None:
            return {"path": self._spec.path}
        return {
            "path": self._spec.path,
            "http_status": result.http_status,
            "failure": result.failure,
            "detail": result.detail,
            "bytes": result.bytes,
            "response_time_ms": result.response_ms,
            "retried": result.retried,
        }
