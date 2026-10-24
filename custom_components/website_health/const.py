"""Constants for the Website Health integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "website_health"
VERSION: Final = "0.1.0"
REPO_URL: Final = "https://github.com/EndOfTheLine-HA/ha-website-health"

CONF_BASE_URL: Final = "base_url"
CONF_ADD_ANYWAY: Final = "add_anyway"

CONF_CHECKS: Final = "checks"
CONF_CHECK_KEY: Final = "key"
CONF_CHECK_LABEL: Final = "label"
CONF_CHECK_PATH: Final = "path"
CONF_CHECK_TYPE: Final = "type"
CONF_MUST_CONTAIN: Final = "must_contain"
CONF_MUST_HAVE_KEYS: Final = "must_have_keys"
CONF_SELECTED_CHECK: Final = "check"
CONF_REMOVE_CHECKS: Final = "remove"

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_TIMEOUT: Final = "timeout"
CONF_RETRIES: Final = "retries"
CONF_RETRY_DELAY: Final = "retry_delay"
CONF_FAILURES_BEFORE_DOWN: Final = "failures_before_down"
CONF_BUILD_INFO_PATH: Final = "build_info_path"
CONF_BUILD_INFO_KEYS: Final = "build_info_keys"
CONF_CONTROL_URLS: Final = "control_urls"
CONF_USER_AGENT: Final = "user_agent"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_HEARTBEAT_URL: Final = "heartbeat_url"

CHECK_TYPE_PAGE: Final = "page"
CHECK_TYPE_JSON: Final = "json"
CHECK_TYPES: Final = [CHECK_TYPE_PAGE, CHECK_TYPE_JSON]

STATUS_OK: Final = "ok"
STATUS_DOWN: Final = "down"
STATUS_INTERNET_DOWN: Final = "internet_down"
STATUSES: Final = [STATUS_OK, STATUS_DOWN, STATUS_INTERNET_DOWN]

DEFAULT_SCAN_INTERVAL: Final = 10  # minutes; matches the sentinel this came from
MIN_SCAN_INTERVAL: Final = 1
MAX_SCAN_INTERVAL: Final = 1440
DEFAULT_TIMEOUT: Final = 10
MIN_TIMEOUT: Final = 2
MAX_TIMEOUT: Final = 60
DEFAULT_RETRIES: Final = 1
MAX_RETRIES: Final = 3
DEFAULT_RETRY_DELAY: Final = 5
MAX_RETRY_DELAY: Final = 60
DEFAULT_FAILURES_BEFORE_DOWN: Final = 1
MAX_FAILURES_BEFORE_DOWN: Final = 5
DEFAULT_USER_AGENT: Final = f"HomeAssistant-WebsiteHealth/{VERSION} (+{REPO_URL})"

# Deliberately two unrelated operators. Neither may share infrastructure with
# the monitored site, or a real outage can look like a home-network fault.
# Not a DNS-over-HTTPS resolver such as dns.google: home DNS blockers and
# firewalls often block those, which would read every failure as an outage.
DEFAULT_CONTROL_URLS: Final = [
    "https://example.com/",
    "https://connectivitycheck.gstatic.com/generate_204",
]

DEFAULT_OPTIONS: Final = {
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
    CONF_TIMEOUT: DEFAULT_TIMEOUT,
    CONF_RETRIES: DEFAULT_RETRIES,
    CONF_RETRY_DELAY: DEFAULT_RETRY_DELAY,
    CONF_FAILURES_BEFORE_DOWN: DEFAULT_FAILURES_BEFORE_DOWN,
    CONF_BUILD_INFO_PATH: "",
    CONF_BUILD_INFO_KEYS: [],
    CONF_CONTROL_URLS: DEFAULT_CONTROL_URLS,
    CONF_USER_AGENT: DEFAULT_USER_AGENT,
    CONF_VERIFY_SSL: True,
    CONF_HEARTBEAT_URL: "",
}

# Several sites failing in the same minute check the control hosts once.
CONTROL_CACHE_SECONDS: Final = 60

# Bodies are only searched, never stored; this bounds memory on huge pages.
MAX_BODY_BYTES: Final = 5 * 1024 * 1024

PLATFORMS: Final = ["sensor", "binary_sensor"]
