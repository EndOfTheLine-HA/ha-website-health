"""Data coordinator for Website Health."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from time import monotonic

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .checker import (
    OUTCOME_OK,
    CheckSpec,
    ControlResult,
    SweepConfig,
    SweepResult,
    check_internet,
    next_status,
    run_sweep,
)
from .const import (
    CONF_BASE_URL,
    CONF_BUILD_INFO_KEYS,
    CONF_BUILD_INFO_PATH,
    CONF_CHECK_KEY,
    CONF_CHECK_LABEL,
    CONF_CHECK_PATH,
    CONF_CHECK_TYPE,
    CONF_CHECKS,
    CONF_CONTROL_URLS,
    CONF_FAILURES_BEFORE_DOWN,
    CONF_HEARTBEAT_URL,
    CONF_MUST_CONTAIN,
    CONF_MUST_HAVE_KEYS,
    CONF_RETRIES,
    CONF_RETRY_DELAY,
    CONF_SCAN_INTERVAL,
    CONF_TIMEOUT,
    CONF_USER_AGENT,
    CONF_VERIFY_SSL,
    CONTROL_CACHE_SECONDS,
    DEFAULT_OPTIONS,
    DOMAIN,
    STATUS_DOWN,
)

_LOGGER = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True, kw_only=True)
class WebsiteHealthData:
    """Latest sweep plus the status after the failed-sweeps threshold."""

    status: str
    sweep: SweepResult
    failed_sweeps: int
    checked_at: datetime


@dataclass
class _ControlCache:
    """Internet-check results shared by every entry, keyed by control URLs."""

    results: dict[tuple[str, ...], ControlResult] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def entry_options(entry: ConfigEntry) -> dict:
    """Options with defaults filled in for keys older entries may lack."""
    return {**DEFAULT_OPTIONS, **entry.options}


def checks_from_options(options: dict) -> tuple[CheckSpec, ...]:
    """Build check specs from stored option dictionaries."""
    return tuple(
        CheckSpec(
            key=check[CONF_CHECK_KEY],
            label=check[CONF_CHECK_LABEL],
            path=check[CONF_CHECK_PATH],
            type=check[CONF_CHECK_TYPE],
            must_contain=tuple(check.get(CONF_MUST_CONTAIN, ())),
            must_have_keys=tuple(check.get(CONF_MUST_HAVE_KEYS, ())),
        )
        for check in options.get(CONF_CHECKS, [])
    )


class WebsiteHealthCoordinator(DataUpdateCoordinator[WebsiteHealthData]):
    """Run the sweep for one website on its own schedule."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        options = entry_options(config_entry)
        super().__init__(
            hass,
            logger=_LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=int(options[CONF_SCAN_INTERVAL])),
        )
        self.checks = checks_from_options(options)
        self.sweep_config = SweepConfig(
            base_url=config_entry.data[CONF_BASE_URL],
            checks=self.checks,
            timeout=float(options[CONF_TIMEOUT]),
            retries=int(options[CONF_RETRIES]),
            retry_delay=float(options[CONF_RETRY_DELAY]),
            user_agent=options[CONF_USER_AGENT],
            build_info_path=options[CONF_BUILD_INFO_PATH] or None,
            build_info_keys=tuple(options[CONF_BUILD_INFO_KEYS]),
        )
        self._control_urls = tuple(options[CONF_CONTROL_URLS])
        self._threshold = int(options[CONF_FAILURES_BEFORE_DOWN])
        self._heartbeat_url: str = options[CONF_HEARTBEAT_URL] or ""
        self._session = async_get_clientsession(
            hass, verify_ssl=bool(options[CONF_VERIFY_SSL])
        )
        self._failed_sweeps = 0

    async def _async_update_data(self) -> WebsiteHealthData:
        # run_sweep reports network trouble as results and never raises for it.
        # Anything that does raise is a code error, and the base class then
        # marks every entity unavailable rather than reporting the site down.
        sweep = await run_sweep(self._session, self.sweep_config, self._internet)
        status, self._failed_sweeps = next_status(
            sweep.outcome, self._failed_sweeps, self._threshold
        )
        await self._heartbeat(status, sweep.outcome)
        return WebsiteHealthData(
            status=status,
            sweep=sweep,
            failed_sweeps=self._failed_sweeps,
            checked_at=dt_util.utcnow(),
        )

    async def _internet(self) -> ControlResult:
        """Internet check, cached briefly and shared across entries."""
        cache: _ControlCache = self.hass.data.setdefault(DOMAIN, _ControlCache())
        async with cache.lock:
            cached = cache.results.get(self._control_urls)
            if cached and monotonic() - cached.checked_at < CONTROL_CACHE_SECONDS:
                return cached
            result = await check_internet(
                self._session,
                self._control_urls,
                timeout_s=self.sweep_config.timeout,
                user_agent=self.sweep_config.user_agent,
            )
            cache.results[self._control_urls] = result
            return result

    async def _heartbeat(self, status: str, outcome: str) -> None:
        """Ping the optional dead-man's switch.

        Success after a clean sweep, /fail once the site is down, and nothing
        for an internet outage, which could not deliver the ping anyway. The
        URL is a secret: it is never logged.
        """
        if not self._heartbeat_url:
            return
        timeout = aiohttp.ClientTimeout(total=HEARTBEAT_TIMEOUT_SECONDS)
        try:
            if outcome == OUTCOME_OK:
                async with self._session.get(self._heartbeat_url, timeout=timeout):
                    pass
            elif status == STATUS_DOWN:
                fail_url = f"{self._heartbeat_url.rstrip('/')}/fail"
                async with self._session.post(fail_url, timeout=timeout):
                    pass
        except (TimeoutError, aiohttp.ClientError, OSError) as err:
            _LOGGER.debug("Heartbeat ping failed: %s", type(err).__name__)
