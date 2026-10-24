"""Isolated Home Assistant instance. No test reaches the internet."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.website_health.checker import (
    OUTCOME_FAILING,
    OUTCOME_INTERNET_DOWN,
    OUTCOME_OK,
    BuildInfo,
    CheckResult,
    ControlResult,
    SweepResult,
)


@pytest.fixture
async def hass(tmp_path):
    source = Path(__file__).parents[1] / "custom_components" / "website_health"
    shutil.copytree(
        source,
        tmp_path / "custom_components" / "website_health",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    instance = HomeAssistant(str(tmp_path))
    instance.config_entries = ConfigEntries(instance, {})
    loader.async_setup(instance)
    dr.async_setup(instance)
    await dr.async_load(instance, load_empty=True)
    await er.async_load(instance, load_empty=True)
    yield instance
    await instance.async_stop(force=True)


class FakeSweeps:
    """Stands in for run_sweep: every check gets the result set here."""

    def __init__(self) -> None:
        self.outcome = OUTCOME_OK
        self.build_info: BuildInfo | None = None
        self.detail = "HTTP 503"
        self.calls = 0
        # Set to an asyncio.Event to hold sweeps until the test releases them.
        self.gate = None

    async def __call__(self, session, config, control) -> SweepResult:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        ok = self.outcome == OUTCOME_OK
        checks = {
            spec.key: CheckResult(
                key=spec.key,
                ok=ok,
                http_status=200 if ok else 503,
                failure=None if ok else "status",
                detail=None if ok else self.detail,
                bytes=1234 if ok else None,
                response_ms=87.5,
            )
            for spec in config.checks
        }
        control_result = None
        if self.outcome in (OUTCOME_FAILING, OUTCOME_INTERNET_DOWN):
            reachable = int(self.outcome == OUTCOME_FAILING)
            control_result = ControlResult(
                ok=bool(reachable), reachable=reachable, total=2
            )
        return SweepResult(
            outcome=self.outcome,
            checks=checks,
            control=control_result,
            build_info=self.build_info,
        )


@pytest.fixture
async def sweeps():
    fake = FakeSweeps()
    # HA's shared session needs zeroconf and the network integration, which
    # this isolated instance does not load. A plain session talks only to the
    # local fixtures some tests start.
    session = aiohttp.ClientSession()
    with (
        patch("custom_components.website_health.coordinator.run_sweep", fake),
        patch(
            "custom_components.website_health.coordinator.async_get_clientsession",
            return_value=session,
        ),
        patch(
            "custom_components.website_health.config_flow.async_probe",
            return_value=True,
        ),
    ):
        yield fake
    await session.close()
