"""Real HA flows, platforms and lifecycle, with the sweep faked."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.website_health.checker import (
    OUTCOME_FAILING,
    OUTCOME_INTERNET_DOWN,
    OUTCOME_OK,
    BuildInfo,
)
from custom_components.website_health.config_flow import normalize_base_url
from custom_components.website_health.const import (
    CONF_BUILD_INFO_KEYS,
    CONF_BUILD_INFO_PATH,
    CONF_CHECK_KEY,
    CONF_CHECKS,
    CONF_HEARTBEAT_URL,
    CONF_USER_AGENT,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from custom_components.website_health.diagnostics import (
    async_get_config_entry_diagnostics,
)


def fields(result):
    return sorted(str(key) for key in result["data_schema"].schema)


async def add_site(hass, url="https://Example.org/", name="Example", settle=True):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_url": url, "name": name}
    )
    assert result["step_id"] == "first_check"
    assert fields(result) == ["label", "path", "type"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"label": "Home", "path": "/", "type": "page"}
    )
    # Type first, then only the field that type uses.
    assert result["step_id"] == "check_page"
    assert fields(result) == ["must_contain"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"must_contain": ["Welcome", " ", "Welcome"]}
    )
    assert result["type"] == "create_entry"
    # settle=False leaves the background first sweep running.
    await hass.async_block_till_done(wait_background_tasks=settle)
    return result["result"]


def entity_ids(hass, entry):
    registry = er.async_get(hass)
    return sorted(
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    )


async def options_step(hass, entry, step, data):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )
    assert result["step_id"] == step
    return await hass.config_entries.options.async_configure(result["flow_id"], data)


def test_normalize_base_url():
    assert normalize_base_url(" HTTPS://Example.ORG/ ") == "https://example.org"
    assert normalize_base_url("http://example.org:8080") == "http://example.org:8080"


async def test_add_site_creates_entities_and_unloads(hass, sweeps):
    entry = await add_site(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.title == "Example"
    assert entry.unique_id == entry.data["base_url"] == "https://example.org"
    check = entry.options[CONF_CHECKS][0]
    assert check["must_contain"] == ["Welcome"]
    assert check["must_have_keys"] == []
    assert entity_ids(hass, entry) == [
        "binary_sensor.example_home",
        "binary_sensor.example_problem",
        "sensor.example_last_checked",
        "sensor.example_response_time",
        "sensor.example_status",
    ]
    assert hass.states.get("sensor.example_status").state == "ok"
    assert hass.states.get("binary_sensor.example_problem").state == "off"
    assert hass.states.get("binary_sensor.example_home").state == "off"
    assert hass.states.get("sensor.example_response_time").state == "87.5"

    await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_url_errors_duplicates_and_add_anyway(hass, sweeps):
    await add_site(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    for url, error in (
        ("ftp://example.net", "invalid_url"),
        ("https://example.net/blog/", "url_has_path"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"base_url": url}
        )
        assert result["errors"] == {"base_url": error}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_url": "https://example.org"}
    )
    assert result["type"] == "abort" and result["reason"] == "already_configured"

    with patch(
        "custom_components.website_health.config_flow.async_probe",
        return_value=False,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"base_url": "https://down.example.net"}
        )
        assert result["errors"] == {"base": "cannot_connect"}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"base_url": "https://down.example.net", "add_anyway": True},
        )
    assert result["step_id"] == "first_check"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"path": "no-slash"}
    )
    assert result["errors"] == {"path": "invalid_path"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"path": "/", "type": "json"}
    )
    assert result["step_id"] == "check_json"
    assert fields(result) == ["must_have_keys"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    assert result["title"] == "down.example.net"
    check = result["options"][CONF_CHECKS][0]
    assert (check["type"], check["must_have_keys"]) == ("json", [])


async def test_setup_does_not_wait_for_the_first_sweep(hass, sweeps):
    """Startup must not hang on a slow site: entities wait instead."""
    sweeps.gate = asyncio.Event()
    entry = await add_site(hass, settle=False)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.example_status").state == "unavailable"
    assert hass.states.get("binary_sensor.example_problem").state == "unavailable"

    sweeps.gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.example_status").state == "ok"
    assert hass.states.get("binary_sensor.example_home").state == "off"


async def test_down_and_internet_down_states(hass, sweeps):
    entry = await add_site(hass)
    coordinator = entry.runtime_data

    sweeps.outcome = OUTCOME_FAILING
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    status = hass.states.get("sensor.example_status")
    assert status.state == "down"
    assert status.attributes["failing_checks"] == [
        {"name": "Home", "path": "/", "failure": "status", "detail": "HTTP 503"}
    ]
    assert status.attributes["internet_check"]["ok"] is True
    assert hass.states.get("binary_sensor.example_problem").state == "on"
    assert hass.states.get("binary_sensor.example_home").state == "on"

    # A home outage must never read as the site being down.
    sweeps.outcome = OUTCOME_INTERNET_DOWN
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.example_status").state == "internet_down"
    assert hass.states.get("binary_sensor.example_problem").state == "unavailable"
    assert hass.states.get("binary_sensor.example_home").state == "unavailable"

    sweeps.outcome = OUTCOME_OK
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.example_status").state == "ok"
    assert hass.states.get("binary_sensor.example_problem").state == "off"


async def test_failed_sweeps_threshold_from_options(hass, sweeps):
    entry = await add_site(hass)
    result = await options_step(
        hass,
        entry,
        "timing",
        {
            "scan_interval": 5,
            "timeout": 10,
            "retries": 1,
            "retry_delay": 5,
            "failures_before_down": 2,
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done(wait_background_tasks=True)

    sweeps.outcome = OUTCOME_FAILING
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    status = hass.states.get("sensor.example_status")
    assert status.state == "ok"
    assert status.attributes["failed_sweeps"] == 1
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.example_status").state == "down"


async def test_options_add_edit_remove_checks(hass, sweeps):
    entry = await add_site(hass)
    result = await options_step(
        hass,
        entry,
        "add_check",
        {"label": "Agents", "path": "/agents.json", "type": "json"},
    )
    assert result["step_id"] == "check_json"
    assert fields(result) == ["must_have_keys"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"must_have_keys": ["version"]}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done(wait_background_tasks=True)
    checks = entry.options[CONF_CHECKS]
    assert [c["label"] for c in checks] == ["Home", "Agents"]
    assert checks[1]["must_contain"] == []
    assert "binary_sensor.example_agents" in entity_ids(hass, entry)

    agents_key = checks[1][CONF_CHECK_KEY]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "choose_check"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"check": agents_key}
    )
    assert result["step_id"] == "edit_check"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"label": "Agents file", "path": "/agents.json", "type": "json"},
    )
    # Same type: the current keys are offered for editing.
    assert result["step_id"] == "check_json"
    suggested = {
        str(key): key.description.get("suggested_value")
        for key in result["data_schema"].schema
    }
    assert suggested == {"must_have_keys": ["version"]}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"must_have_keys": ["version", "site"]}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done(wait_background_tasks=True)
    edited = entry.options[CONF_CHECKS][1]
    assert edited[CONF_CHECK_KEY] == agents_key
    assert edited["must_have_keys"] == ["version", "site"]

    home_key = entry.options[CONF_CHECKS][0][CONF_CHECK_KEY]
    result = await options_step(
        hass, entry, "remove_check", {"remove": [home_key, agents_key]}
    )
    assert result["errors"] == {"base": "no_checks_left"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"remove": [home_key]}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [c["label"] for c in entry.options[CONF_CHECKS]] == ["Agents file"]
    assert "binary_sensor.example_home" not in entity_ids(hass, entry)


async def test_build_info_sensor_and_warning(hass, sweeps):
    entry = await add_site(hass)
    result = await options_step(
        hass, entry, "build_info", {CONF_BUILD_INFO_PATH: "/build-info.json"}
    )
    assert result["errors"] == {CONF_BUILD_INFO_KEYS: "no_build_keys"}
    sweeps.build_info = BuildInfo(
        available=True, values={"commitShort": "abc1234", "builtAt": "2026-10-23"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_BUILD_INFO_PATH: "/build-info.json",
            CONF_BUILD_INFO_KEYS: ["commitShort", "builtAt"],
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done(wait_background_tasks=True)
    build = hass.states.get("sensor.example_published_build")
    assert build.state == "abc1234"
    assert build.attributes["builtAt"] == "2026-10-23"

    # A broken build-info file is a warning attribute, never a status change.
    sweeps.build_info = BuildInfo(available=False, detail="HTTP 404")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    status = hass.states.get("sensor.example_status")
    assert status.state == "ok"
    assert status.attributes["build_info_warning"] == "HTTP 404"
    assert hass.states.get("sensor.example_published_build").state == "unavailable"


async def test_internet_check_and_advanced_validation(hass, sweeps):
    entry = await add_site(hass)
    result = await options_step(
        hass, entry, "internet_check", {"control_urls": ["not a url"]}
    )
    assert result["errors"] == {"control_urls": "invalid_control_urls"}
    result = await options_step(
        hass,
        entry,
        "advanced",
        {
            CONF_USER_AGENT: "x",
            CONF_VERIFY_SSL: True,
            CONF_HEARTBEAT_URL: "hc-ping/abc",
        },
    )
    assert result["errors"] == {CONF_HEARTBEAT_URL: "invalid_heartbeat_url"}


async def test_heartbeat_get_on_ok_and_post_fail_on_down(hass, sweeps):
    seen = []

    async def record(request):
        seen.append((request.method, request.path))
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", record)
    server = TestServer(app)
    await server.start_server()
    try:
        entry = await add_site(hass)
        ping = str(server.make_url("/ping/uuid"))
        result = await options_step(
            hass,
            entry,
            "advanced",
            {CONF_USER_AGENT: "x", CONF_VERIFY_SSL: True, CONF_HEARTBEAT_URL: ping},
        )
        assert result["type"] == "create_entry"
        await hass.async_block_till_done(wait_background_tasks=True)
        assert seen[-1] == ("GET", "/ping/uuid")

        sweeps.outcome = OUTCOME_FAILING
        await entry.runtime_data.async_refresh()
        assert seen[-1] == ("POST", "/ping/uuid/fail")

        count = len(seen)
        sweeps.outcome = OUTCOME_INTERNET_DOWN
        await entry.runtime_data.async_refresh()
        assert len(seen) == count
    finally:
        await server.close()


async def test_diagnostics_redact_host_and_heartbeat(hass, sweeps):
    entry = await add_site(hass)
    await options_step(
        hass,
        entry,
        "advanced",
        {
            CONF_USER_AGENT: "x",
            CONF_VERIFY_SSL: True,
            CONF_HEARTBEAT_URL: "https://hc-ping.com/secret-uuid",
        },
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    sweeps.outcome = OUTCOME_FAILING
    sweeps.detail = "Cannot connect to host example.org:443"
    await entry.runtime_data.async_refresh()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diagnostics)
    assert "example.org" not in text
    assert "secret-uuid" not in text
    assert diagnostics["status"] == "down"
