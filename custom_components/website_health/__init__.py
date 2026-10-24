"""Website Health integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import PLATFORMS
from .coordinator import WebsiteHealthCoordinator

type WebsiteHealthConfigEntry = ConfigEntry[WebsiteHealthCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: WebsiteHealthConfigEntry
) -> bool:
    """Set up one monitored website from a config entry."""
    coordinator = WebsiteHealthCoordinator(hass, entry)
    entry.runtime_data = coordinator
    _remove_deleted_check_entities(hass, entry, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # The first sweep runs in the background, not in setup. A sweep against a
    # slow or unreachable site can take a minute of timeouts and retries, and
    # Home Assistant's startup would wait for all of it. Entities stay
    # unavailable until it finishes.
    entry.async_create_background_task(
        hass,
        coordinator.async_refresh(),
        name=f"website_health first sweep {entry.title}",
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: WebsiteHealthConfigEntry
) -> bool:
    """Unload a Website Health config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(
    hass: HomeAssistant, entry: WebsiteHealthConfigEntry
) -> None:
    """Reload after options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_deleted_check_entities(
    hass: HomeAssistant,
    entry: WebsiteHealthConfigEntry,
    coordinator: WebsiteHealthCoordinator,
) -> None:
    """Drop registry entries for checks the user removed in options."""
    registry = er.async_get(hass)
    identifier = entry.unique_id or entry.entry_id
    current = {f"{identifier}_check_{spec.key}" for spec in coordinator.checks}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.unique_id.startswith(f"{identifier}_check_")
            and entity.unique_id not in current
        ):
            registry.async_remove(entity.entity_id)
