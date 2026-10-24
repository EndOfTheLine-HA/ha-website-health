"""Sensor platform for Website Health."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WebsiteHealthConfigEntry
from .const import STATUSES
from .coordinator import WebsiteHealthCoordinator
from .entity import WebsiteHealthEntity

MAX_STATE_LENGTH = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WebsiteHealthConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Website Health sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        StatusSensor(coordinator),
        ResponseTimeSensor(coordinator),
        LastCheckedSensor(coordinator),
    ]
    if coordinator.sweep_config.build_info_path:
        entities.append(PublishedBuildSensor(coordinator))
    async_add_entities(entities)


class StatusSensor(WebsiteHealthEntity, SensorEntity):
    """OK, Down, or My internet is down."""

    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES

    def __init__(self, coordinator: WebsiteHealthCoordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str:
        """Return the status after the failed-sweeps threshold."""
        return self.coordinator.data.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Explain the status without exposing any response body."""
        data = self.coordinator.data
        labels = {spec.key: spec for spec in self.coordinator.checks}
        attributes: dict[str, Any] = {
            "failed_sweeps": data.failed_sweeps,
            "failing_checks": [
                {
                    "name": labels[key].label,
                    "path": labels[key].path,
                    "failure": result.failure,
                    "detail": result.detail,
                }
                for key, result in data.sweep.checks.items()
                if not result.ok and key in labels
            ],
        }
        control = data.sweep.control
        if control is not None:
            attributes["internet_check"] = {
                "ok": control.ok,
                "reachable": control.reachable,
                "total": control.total,
            }
            attributes["site_answered_despite_internet_check"] = (
                data.sweep.probe_override
            )
        build = data.sweep.build_info
        if build is not None and not build.available:
            # A broken build-info file is worth knowing about, but it is not
            # the site being down, so it never changes the state.
            attributes["build_info_warning"] = build.detail
        return attributes


class ResponseTimeSensor(WebsiteHealthEntity, SensorEntity):
    """Response time of the first check, usually the homepage."""

    _attr_translation_key = "response_time"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MILLISECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: WebsiteHealthCoordinator) -> None:
        super().__init__(coordinator, "response_time")

    def _first(self):
        checks = self.coordinator.checks
        if not checks:
            return None
        return self.coordinator.data.sweep.checks.get(checks[0].key)

    @property
    def native_value(self) -> float | None:
        """Return milliseconds until the first check's body was read."""
        first = self._first()
        return first.response_ms if first else None

    @property
    def available(self) -> bool:
        """Unavailable when the first check got no response at all."""
        return super().available and self.native_value is not None


class PublishedBuildSensor(WebsiteHealthEntity, SensorEntity):
    """The first configured key of the build-info file."""

    _attr_translation_key = "published_build"

    def __init__(self, coordinator: WebsiteHealthCoordinator) -> None:
        super().__init__(coordinator, "published_build")

    @property
    def native_value(self) -> str | None:
        """Return the first build-info key as text."""
        build = self.coordinator.data.sweep.build_info
        keys = self.coordinator.sweep_config.build_info_keys
        if build is None or not build.available or not keys:
            return None
        return str(build.values.get(keys[0]))[:MAX_STATE_LENGTH]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the remaining build-info keys."""
        build = self.coordinator.data.sweep.build_info
        keys = self.coordinator.sweep_config.build_info_keys
        if build is None or not build.available:
            return {}
        return {key: build.values[key] for key in keys[1:] if key in build.values}

    @property
    def available(self) -> bool:
        """Unavailable when the file could not be read this sweep."""
        build = (
            self.coordinator.data.sweep.build_info if self.coordinator.data else None
        )
        return super().available and build is not None and build.available


class LastCheckedSensor(WebsiteHealthEntity, SensorEntity):
    """When the last sweep finished."""

    _attr_translation_key = "last_checked"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: WebsiteHealthCoordinator) -> None:
        super().__init__(coordinator, "last_checked")

    @property
    def native_value(self) -> datetime:
        """Return the time of the last sweep."""
        return self.coordinator.data.checked_at
