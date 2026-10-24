"""Check engine against local HTTP fixtures. Nothing here touches the internet."""

from __future__ import annotations

import asyncio
import socket

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.website_health.checker import (
    FAILURE_CONTENT,
    FAILURE_JSON,
    FAILURE_STATUS,
    OUTCOME_FAILING,
    OUTCOME_INTERNET_DOWN,
    OUTCOME_OK,
    CheckSpec,
    ControlResult,
    SweepConfig,
    check_internet,
    next_status,
    run_sweep,
)
from custom_components.website_health.const import (
    STATUS_DOWN,
    STATUS_INTERNET_DOWN,
    STATUS_OK,
)


def _dead_url() -> str:
    """A localhost URL that refuses connections immediately."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture
async def site():
    hits = {"flaky": 0, "user_agents": []}

    async def page(request):
        hits["user_agents"].append(request.headers.get("User-Agent"))
        return web.Response(
            text="<h1>Welcome</h1><button>Get started</button>",
            content_type="text/html",
        )

    async def flaky(request):
        hits["flaky"] += 1
        if hits["flaky"] == 1:
            return web.Response(status=503)
        return web.Response(text="Welcome back", content_type="text/html")

    async def slow(request):
        await asyncio.sleep(2)
        return web.Response(text="late")

    async def moved(request):
        raise web.HTTPFound("/")

    async def big(request):
        # Sent in pieces with pauses, like a real page over the network, so a
        # reader that stops at the first chunk misses the text at the end.
        response = web.StreamResponse(headers={"Content-Type": "text/html"})
        await response.prepare(request)
        await response.write(b"<h1>Start</h1>")
        for _ in range(6):
            await response.write(b"x" * 50_000)
            await asyncio.sleep(0.02)
        await response.write(b"<button>Get recommendations</button>")
        await response.write_eof()
        return response

    def respond(make):
        async def handler(request):
            return make()

        return handler

    app = web.Application()
    app.router.add_get("/", page)
    app.router.add_get("/flaky/", flaky)
    app.router.add_get("/slow/", slow)
    app.router.add_get("/moved/", moved)
    app.router.add_get("/big/", big)
    app.router.add_get("/gone/", respond(lambda: web.Response(status=404)))
    app.router.add_get(
        "/agents.json",
        respond(
            lambda: web.json_response({"version": 1, "site": "x", "discovery": []})
        ),
    )
    app.router.add_get("/list.json", respond(lambda: web.json_response([1, 2])))
    app.router.add_get("/broken.json", respond(lambda: web.Response(text="{nope")))
    app.router.add_get(
        "/build-info.json",
        respond(
            lambda: web.json_response({"commitShort": "abc1234", "builtAt": "2026"})
        ),
    )
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("")).rstrip("/"), hits
    await server.close()


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as client:
        yield client


def _check(path, *, type_="page", must=(), keys=(), key=None):
    return CheckSpec(
        key=key or path,
        label=path,
        path=path,
        type=type_,
        must_contain=tuple(must),
        must_have_keys=tuple(keys),
    )


def _config(base, *checks, retries=0, timeout=5.0, build=None, build_keys=()):
    return SweepConfig(
        base_url=base,
        checks=tuple(checks),
        timeout=timeout,
        retries=retries,
        retry_delay=0,
        user_agent="WebsiteHealthTest/1",
        build_info_path=build,
        build_info_keys=tuple(build_keys),
    )


def _control(ok: bool):
    calls = []

    async def control():
        calls.append(True)
        return ControlResult(ok=ok, reachable=int(ok), total=2)

    return control, calls


async def test_all_pass_skips_internet_check(site, session):
    base, hits = site
    control, calls = _control(True)
    result = await run_sweep(
        session,
        _config(
            base,
            _check("/", must=["Welcome", "Get started"]),
            _check("/agents.json", type_="json", keys=["version", "discovery"]),
            _check("/moved/", must=["Welcome"]),
        ),
        control,
    )
    assert result.outcome == OUTCOME_OK
    assert all(check.ok for check in result.checks.values())
    assert result.checks["/"].http_status == 200
    assert result.checks["/"].bytes > 0
    assert calls == []
    assert hits["user_agents"][0] == "WebsiteHealthTest/1"


async def test_404_is_a_status_failure(site, session):
    base, _ = site
    control, calls = _control(True)
    result = await run_sweep(session, _config(base, _check("/gone/")), control)
    check = result.checks["/gone/"]
    assert result.outcome == OUTCOME_FAILING
    assert (check.failure, check.http_status, check.detail) == (
        FAILURE_STATUS,
        404,
        "HTTP 404",
    )
    assert calls == [True]


async def test_200_without_required_text_is_a_content_failure(site, session):
    base, _ = site
    control, _ = _control(True)
    result = await run_sweep(
        session, _config(base, _check("/", must=["Welcome", "Checkout"])), control
    )
    check = result.checks["/"]
    assert result.outcome == OUTCOME_FAILING
    assert check.failure == FAILURE_CONTENT
    assert check.detail == "missing text: Checkout"


async def test_text_at_the_end_of_a_chunked_page_is_found(site, session):
    """Regression: the body read used to stop after the first network chunk."""
    base, _ = site
    control, _ = _control(True)
    result = await run_sweep(
        session,
        _config(base, _check("/big/", must=["Start", "Get recommendations"])),
        control,
    )
    check = result.checks["/big/"]
    assert result.outcome == OUTCOME_OK, check.detail
    assert check.bytes == 14 + 6 * 50_000 + 36


async def test_content_failure_reports_the_size(site, session):
    base, _ = site
    control, _ = _control(True)
    result = await run_sweep(
        session, _config(base, _check("/", must=["Nope"])), control
    )
    assert result.checks["/"].bytes > 0


async def test_json_failures(site, session):
    base, _ = site
    control, _ = _control(True)
    result = await run_sweep(
        session,
        _config(
            base,
            _check("/broken.json", type_="json"),
            _check("/agents.json", type_="json", keys=["version", "owner"]),
            _check("/list.json", type_="json", keys=["version"]),
            _check("/list.json", type_="json", key="list-no-keys"),
        ),
        control,
    )
    assert result.checks["/broken.json"].detail == "not valid JSON"
    assert result.checks["/agents.json"].detail == "missing keys: owner"
    assert result.checks["/list.json"].detail == "not a JSON object"
    assert {r.failure for r in result.checks.values() if not r.ok} == {FAILURE_JSON}
    assert result.checks["list-no-keys"].ok


async def test_retry_recovers_a_transient_failure(site, session):
    base, hits = site
    control, calls = _control(True)
    result = await run_sweep(
        session, _config(base, _check("/flaky/"), retries=1), control
    )
    assert result.outcome == OUTCOME_OK
    assert result.checks["/flaky/"].retried is True
    assert hits["flaky"] == 2
    assert calls == []


async def test_timeout_is_reported(site, session):
    base, _ = site
    control, _ = _control(True)
    result = await run_sweep(
        session, _config(base, _check("/slow/"), timeout=0.3), control
    )
    check = result.checks["/slow/"]
    assert check.failure == FAILURE_STATUS
    assert check.http_status is None
    assert "timed out" in check.detail


async def test_internet_down_when_controls_and_site_fail(session):
    control, calls = _control(False)
    result = await run_sweep(session, _config(_dead_url(), _check("/")), control)
    assert result.outcome == OUTCOME_INTERNET_DOWN
    assert result.build_info is None
    assert calls == [True]


async def test_site_answering_overrides_failed_controls(site, session):
    base, _ = site
    control, _ = _control(False)
    result = await run_sweep(session, _config(base, _check("/gone/")), control)
    assert result.outcome == OUTCOME_FAILING
    assert result.probe_override is True


async def test_build_info_never_changes_the_outcome(site, session):
    base, _ = site
    control, _ = _control(True)
    good = await run_sweep(
        session,
        _config(
            base,
            _check("/"),
            build="/build-info.json",
            build_keys=["commitShort", "builtAt", "branch"],
        ),
        control,
    )
    assert good.outcome == OUTCOME_OK
    assert good.build_info.available
    assert good.build_info.values == {"commitShort": "abc1234", "builtAt": "2026"}

    missing = await run_sweep(
        session,
        _config(base, _check("/"), build="/nope.json", build_keys=["commitShort"]),
        control,
    )
    assert missing.outcome == OUTCOME_OK
    assert not missing.build_info.available
    assert missing.build_info.detail == "HTTP 404"


async def test_check_internet_needs_http_not_dns(site, session):
    base, _ = site
    dead = _dead_url()
    one_up = await check_internet(
        session, (f"{dead}/", f"{base}/"), timeout_s=2, user_agent="t"
    )
    assert (one_up.ok, one_up.reachable, one_up.total) == (True, 1, 2)
    assert len(one_up.reasons) == 1
    all_down = await check_internet(session, (f"{dead}/",), timeout_s=2, user_agent="t")
    assert not all_down.ok


@pytest.mark.parametrize(
    ("outcome", "failed", "threshold", "expected"),
    [
        (OUTCOME_OK, 3, 1, (STATUS_OK, 0)),
        (OUTCOME_FAILING, 0, 1, (STATUS_DOWN, 1)),
        (OUTCOME_FAILING, 0, 2, (STATUS_OK, 1)),
        (OUTCOME_FAILING, 1, 2, (STATUS_DOWN, 2)),
        (OUTCOME_INTERNET_DOWN, 1, 2, (STATUS_INTERNET_DOWN, 1)),
    ],
)
def test_failed_sweeps_threshold(outcome, failed, threshold, expected):
    assert next_status(outcome, failed, threshold) == expected
