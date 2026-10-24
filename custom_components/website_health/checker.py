"""HTTP checks and judging for Website Health.

This module has no Home Assistant imports so every rule in it can be tested
against local HTTP fixtures. The coordinator supplies the aiohttp session and
decides what a run of failed sweeps means.

Ported from a read-only uptime sentinel. Rules it keeps, and why:

- GET only. The single exception is the optional heartbeat ping, which lives in
  the coordinator, not here.
- A 200 is not enough. A page check passes only if the served HTML contains
  every required string, because a page can load while the part that matters
  is missing.
- Before failures are blamed on the site, the internet check must prove HTTP
  works. DNS resolving says nothing about HTTP egress; accepting it once let a
  total local outage be reported as a site outage.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic

import aiohttp

from .const import (
    CHECK_TYPE_JSON,
    MAX_BODY_BYTES,
    STATUS_DOWN,
    STATUS_INTERNET_DOWN,
    STATUS_OK,
)

OUTCOME_OK = "ok"
OUTCOME_FAILING = "failing"
OUTCOME_INTERNET_DOWN = "internet_down"

FAILURE_STATUS = "status"
FAILURE_CONTENT = "content"
FAILURE_JSON = "json"


@dataclass(frozen=True, kw_only=True)
class CheckSpec:
    """One path to fetch and what its response must contain."""

    key: str
    label: str
    path: str
    type: str
    must_contain: tuple[str, ...] = ()
    must_have_keys: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class SweepConfig:
    """Everything one sweep needs, resolved from the config entry."""

    base_url: str
    checks: tuple[CheckSpec, ...]
    timeout: float
    retries: int
    retry_delay: float
    user_agent: str
    build_info_path: str | None = None
    build_info_keys: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class FetchResult:
    """One HTTP attempt. Never raises; a dead request is a result too."""

    ok: bool
    status: int | None
    body: str = ""
    size: int | None = None
    error: str | None = None
    response_ms: float | None = None
    truncated: bool = False


@dataclass(frozen=True, kw_only=True)
class CheckResult:
    """Judged outcome of one check. Holds no response body."""

    key: str
    ok: bool
    http_status: int | None
    failure: str | None = None
    detail: str | None = None
    bytes: int | None = None
    response_ms: float | None = None
    retried: bool = False


@dataclass(frozen=True, kw_only=True)
class ControlResult:
    """Whether this machine can reach the open internet over HTTP."""

    ok: bool
    reachable: int
    total: int
    reasons: tuple[str, ...] = ()
    checked_at: float = field(default_factory=monotonic)


@dataclass(frozen=True, kw_only=True)
class BuildInfo:
    """Values read from the optional build-info JSON file."""

    available: bool
    values: dict[str, object] = field(default_factory=dict)
    detail: str | None = None


@dataclass(frozen=True, kw_only=True)
class SweepResult:
    """One full sweep, before the failed-sweep threshold is applied."""

    outcome: str
    checks: dict[str, CheckResult]
    control: ControlResult | None = None
    probe_override: bool = False
    build_info: BuildInfo | None = None


ControlFn = Callable[[], Awaitable[ControlResult]]


def join_url(base_url: str, path: str) -> str:
    """Join a normalized base URL and a path that starts with a slash."""
    return f"{base_url.rstrip('/')}{path}"


async def fetch_once(
    session: aiohttp.ClientSession, url: str, *, timeout_s: float, user_agent: str
) -> FetchResult:
    """GET one URL with a hard timeout and a bounded body read."""
    started = monotonic()
    try:
        async with session.get(
            url,
            allow_redirects=True,
            headers={"User-Agent": user_agent, "Accept": "*/*"},
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as response:
            raw = await _read_body(response)
            elapsed = (monotonic() - started) * 1000
            truncated = len(raw) > MAX_BODY_BYTES
            raw = raw[:MAX_BODY_BYTES]
            body = raw.decode(response.charset or "utf-8", errors="replace")
            return FetchResult(
                ok=200 <= response.status < 300,
                status=response.status,
                body=body,
                size=len(raw),
                response_ms=round(elapsed, 1),
                truncated=truncated,
            )
    except TimeoutError:
        return FetchResult(ok=False, status=None, error=f"timed out after {timeout_s}s")
    except (aiohttp.ClientError, OSError, LookupError) as err:
        return FetchResult(ok=False, status=None, error=_describe(err))


async def _read_body(response: aiohttp.ClientResponse) -> bytes:
    """Read the whole body, stopping one byte past the cap.

    Not `content.read(n)`: that returns whatever has arrived so far, up to n
    bytes, so a page delivered in several network chunks was cut short and
    text near its end was reported missing.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            break
    return b"".join(chunks)


async def fetch_with_retry(
    session: aiohttp.ClientSession, url: str, config: SweepConfig
) -> tuple[FetchResult, bool]:
    """Retry a failed fetch a bounded number of times after a short delay.

    In-run retries catch transient CDN hiccups without waiting a whole interval.
    Noise that survives them is what the failed-sweeps threshold is for, not a
    longer timeout.
    """
    result = await fetch_once(
        session, url, timeout_s=config.timeout, user_agent=config.user_agent
    )
    retried = False
    for _ in range(config.retries):
        if result.ok:
            break
        retried = True
        await asyncio.sleep(config.retry_delay)
        result = await fetch_once(
            session, url, timeout_s=config.timeout, user_agent=config.user_agent
        )
    return result, retried


def judge(spec: CheckSpec, fetched: FetchResult, retried: bool) -> CheckResult:
    """Turn one fetch into a pass or a named kind of failure."""
    # Size is reported on failures too: a content failure on a page that came
    # back unusually small is the first clue to a cut-short or changed page.
    common = {
        "key": spec.key,
        "http_status": fetched.status,
        "bytes": fetched.size,
        "response_ms": fetched.response_ms,
        "retried": retried,
    }
    if not fetched.ok:
        detail = fetched.error or f"HTTP {fetched.status}"
        return CheckResult(ok=False, failure=FAILURE_STATUS, detail=detail, **common)

    if spec.type == CHECK_TYPE_JSON:
        try:
            parsed = json.loads(fetched.body)
        except ValueError:
            return CheckResult(
                ok=False, failure=FAILURE_JSON, detail="not valid JSON", **common
            )
        if spec.must_have_keys:
            if not isinstance(parsed, dict):
                return CheckResult(
                    ok=False, failure=FAILURE_JSON, detail="not a JSON object", **common
                )
            missing = [key for key in spec.must_have_keys if key not in parsed]
            if missing:
                return CheckResult(
                    ok=False,
                    failure=FAILURE_JSON,
                    detail=f"missing keys: {', '.join(missing)}",
                    **common,
                )
        return CheckResult(ok=True, **common)

    missing = [text for text in spec.must_contain if text not in fetched.body]
    if missing:
        detail = "missing text: " + " | ".join(missing)
        if fetched.truncated:
            detail += f" (only the first {MAX_BODY_BYTES} bytes were searched)"
        return CheckResult(ok=False, failure=FAILURE_CONTENT, detail=detail, **common)
    return CheckResult(ok=True, **common)


async def check_internet(
    session: aiohttp.ClientSession,
    control_urls: tuple[str, ...],
    *,
    timeout_s: float,
    user_agent: str,
) -> ControlResult:
    """Fetch every control URL concurrently. HTTP must work, not just DNS."""
    results = await asyncio.gather(
        *(
            fetch_once(session, url, timeout_s=timeout_s, user_agent=user_agent)
            for url in control_urls
        )
    )
    reasons = tuple(
        f"{url}: {result.error or f'HTTP {result.status}'}"
        for url, result in zip(control_urls, results, strict=True)
        if not result.ok
    )
    reachable = sum(1 for result in results if result.ok)
    return ControlResult(
        ok=reachable > 0, reachable=reachable, total=len(control_urls), reasons=reasons
    )


async def read_build_info(
    session: aiohttp.ClientSession, config: SweepConfig
) -> BuildInfo | None:
    """Read the optional provenance file. Diagnostic only: never changes status."""
    if not config.build_info_path:
        return None
    fetched = await fetch_once(
        session,
        join_url(config.base_url, config.build_info_path),
        timeout_s=config.timeout,
        user_agent=config.user_agent,
    )
    if not fetched.ok:
        return BuildInfo(
            available=False, detail=fetched.error or f"HTTP {fetched.status}"
        )
    try:
        parsed = json.loads(fetched.body)
    except ValueError:
        return BuildInfo(available=False, detail="not valid JSON")
    if not isinstance(parsed, dict):
        return BuildInfo(available=False, detail="not a JSON object")
    values = {key: parsed[key] for key in config.build_info_keys if key in parsed}
    if config.build_info_keys and config.build_info_keys[0] not in values:
        return BuildInfo(
            available=False,
            values=values,
            detail=f"missing key: {config.build_info_keys[0]}",
        )
    return BuildInfo(available=True, values=values)


async def run_sweep(
    session: aiohttp.ClientSession, config: SweepConfig, control: ControlFn
) -> SweepResult:
    """Run every check, then classify failures as the site's or the uplink's.

    The internet check runs only when something failed. The classification is
    the same as checking first, and a healthy site never sends traffic to the
    control hosts.
    """
    results: dict[str, CheckResult] = {}
    for spec in config.checks:
        fetched, retried = await fetch_with_retry(
            session, join_url(config.base_url, spec.path), config
        )
        results[spec.key] = judge(spec, fetched, retried)

    if all(result.ok for result in results.values()):
        return SweepResult(
            outcome=OUTCOME_OK,
            checks=results,
            build_info=await read_build_info(session, config),
        )

    internet = await control()
    probe_override = False
    if not internet.ok:
        # Before blaming the uplink, ask the site itself once. Two control
        # hosts having a bad minute together must not silence a real outage.
        probe = await fetch_once(
            session,
            join_url(config.base_url, "/"),
            timeout_s=config.timeout,
            user_agent=config.user_agent,
        )
        if not probe.ok:
            return SweepResult(
                outcome=OUTCOME_INTERNET_DOWN, checks=results, control=internet
            )
        probe_override = True

    return SweepResult(
        outcome=OUTCOME_FAILING,
        checks=results,
        control=internet,
        probe_override=probe_override,
        build_info=await read_build_info(session, config),
    )


def next_status(outcome: str, failed_sweeps: int, threshold: int) -> tuple[str, int]:
    """Apply the failed-sweeps threshold. Returns (status, new failed count).

    An internet outage says nothing about the site, so it neither counts as a
    failed sweep nor clears earlier ones.
    """
    if outcome == OUTCOME_OK:
        return STATUS_OK, 0
    if outcome == OUTCOME_INTERNET_DOWN:
        return STATUS_INTERNET_DOWN, failed_sweeps
    failed_sweeps += 1
    return (STATUS_DOWN if failed_sweeps >= threshold else STATUS_OK), failed_sweeps


def _describe(err: BaseException) -> str:
    text = str(err).strip()
    return f"{type(err).__name__}: {text}" if text else type(err).__name__
