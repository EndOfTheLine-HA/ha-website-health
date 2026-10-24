"""Diagnostics support for Website Health."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.core import HomeAssistant
from yarl import URL

from . import WebsiteHealthConfigEntry
from .const import CONF_BASE_URL, CONF_CHECK_PATH, CONF_CHECKS, CONF_HEARTBEAT_URL

TO_REDACT = {CONF_BASE_URL, CONF_HEARTBEAT_URL}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WebsiteHealthConfigEntry
) -> dict[str, Any]:
    """Return diagnostics safe to paste into a public issue.

    The site's host, the heartbeat URL and check query strings are removed,
    including from error text, which often names the host it failed to reach.
    """
    coordinator = entry.runtime_data
    host = URL(entry.data[CONF_BASE_URL]).host or ""

    def scrub(text: str | None) -> str | None:
        return text.replace(host, REDACTED) if text and host else text

    options = dict(entry.options)
    options[CONF_CHECKS] = [
        {**check, CONF_CHECK_PATH: check[CONF_CHECK_PATH].split("?", 1)[0]}
        for check in options.get(CONF_CHECKS, [])
    ]
    result: dict[str, Any] = {
        "config_entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(options, TO_REDACT),
        "last_update_success": coordinator.last_update_success,
    }
    data = coordinator.data
    if data is None:
        return result

    sweep = data.sweep
    result.update(
        {
            "status": data.status,
            "failed_sweeps": data.failed_sweeps,
            "outcome": sweep.outcome,
            "checks": {
                key: {**asdict(check), "detail": scrub(check.detail)}
                for key, check in sweep.checks.items()
            },
            "internet_check": (
                {
                    "ok": sweep.control.ok,
                    "reachable": sweep.control.reachable,
                    "total": sweep.control.total,
                    "reasons": list(sweep.control.reasons),
                }
                if sweep.control
                else None
            ),
            "site_answered_despite_internet_check": sweep.probe_override,
            "build_info": (
                {
                    "available": sweep.build_info.available,
                    "detail": scrub(sweep.build_info.detail),
                    "keys_found": sorted(sweep.build_info.values),
                }
                if sweep.build_info
                else None
            ),
        }
    )
    return result
