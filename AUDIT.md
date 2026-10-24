# Source, privacy, and safety audit

## Origin and privacy boundary

Website Health is a generic port of a private, read-only uptime sentinel that ran on
a separate single-board computer. Only the sweep logic was ported. This repository
intentionally excludes the original site's address, routes, required text, user agent,
credentials, ping URLs, network addresses, and any household or deployment details.
A structure test fails if known private identifiers appear in the repository.

## Confirmed implementation behavior

- All site checks, control checks and the build-info read are HTTP GET requests through
  Home Assistant's shared aiohttp session, with redirects followed, a per-request
  timeout, and a 5 MiB cap on the body read.
- The only non-GET request is the optional heartbeat: GET after a clean sweep, POST to
  `<url>/fail` when the status is Down, nothing during an internet outage.
- A page check passes only when the status is 2xx and every required string is present
  in the decoded body. A JSON check passes only when the status is 2xx, the body parses,
  and every required top-level key exists.
- The internet check runs only after a failure and passes only when at least one control
  URL answers with 2xx over HTTP. DNS resolution alone is never accepted.
- When every control fails, the site's homepage is fetched once; if it answers, the
  failures are attributed to the site.
- An internet outage neither counts as a failed sweep nor clears earlier ones.
- The build-info file never changes the status.
- Response bodies are not stored, logged, or exposed in attributes or diagnostics.

## Deliberate exclusions

- No services, actions, buttons, switches or entity services.
- No requests other than those listed above.
- No JavaScript rendering, authentication, custom headers or POST checks.
- No automatic remediation of any kind.
- No YAML configuration.

## Validation boundary

Automated tests cover the check engine against local HTTP fixtures (success, 404,
timeouts, retries, missing text, JSON failures, redirects, internet-down
classification, the homepage override, build info, and the failed-sweeps threshold),
the real config and options flows, entity states for every status, heartbeat delivery
against a local server, diagnostics redaction, translation parity, and the read-only
contract. CI also runs Ruff, HACS validation and Hassfest. No test contacts the
internet.

Not verified: long-running behavior on every installation type, sites behind unusual
CDNs or bot protection that may block or challenge automated requests, very large
pages near the body cap, and non-UTF-8 pages without a declared charset.
