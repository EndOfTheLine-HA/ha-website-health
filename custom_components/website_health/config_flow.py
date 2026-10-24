"""Config flow for Website Health."""

from __future__ import annotations

import logging
from typing import Any, override
from uuid import uuid4

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from yarl import URL

from .checker import fetch_once, join_url
from .const import (
    CHECK_TYPE_JSON,
    CHECK_TYPE_PAGE,
    CHECK_TYPES,
    CONF_ADD_ANYWAY,
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
    CONF_REMOVE_CHECKS,
    CONF_RETRIES,
    CONF_RETRY_DELAY,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_CHECK,
    CONF_TIMEOUT,
    CONF_USER_AGENT,
    CONF_VERIFY_SSL,
    DEFAULT_OPTIONS,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    DOMAIN,
    MAX_FAILURES_BEFORE_DOWN,
    MAX_RETRIES,
    MAX_RETRY_DELAY,
    MAX_SCAN_INTERVAL,
    MAX_TIMEOUT,
    MIN_SCAN_INTERVAL,
    MIN_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

LIST_TEXT = TextSelector(TextSelectorConfig(multiple=True))

USER_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NAME): TextSelector(),
        vol.Required(CONF_BASE_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Optional(CONF_ADD_ANYWAY, default=False): BooleanSelector(),
    }
)

# A check is asked for in two steps: name, path and type first, then only the
# field that type uses. One HA form cannot hide a field based on another, and
# showing both lists invited entries that were then silently dropped.
CHECK_BASICS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_CHECK_LABEL): TextSelector(),
        vol.Required(CONF_CHECK_PATH, default="/"): TextSelector(),
        vol.Required(CONF_CHECK_TYPE, default=CHECK_TYPE_PAGE): SelectSelector(
            SelectSelectorConfig(
                options=CHECK_TYPES,
                translation_key="check_type",
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
    }
)
CHECK_PAGE_SCHEMA = vol.Schema({vol.Optional(CONF_MUST_CONTAIN): LIST_TEXT})
CHECK_JSON_SCHEMA = vol.Schema({vol.Optional(CONF_MUST_HAVE_KEYS): LIST_TEXT})


class InvalidUrl(ValueError):
    """A URL the user typed cannot be used. `code` is the translation key."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_base_url(text: str) -> str:
    """Return scheme://host[:port] or raise InvalidUrl."""
    try:
        url = URL(text.strip())
    except ValueError as err:
        raise InvalidUrl("invalid_url") from err
    if url.scheme not in ("http", "https") or not url.host:
        raise InvalidUrl("invalid_url")
    if url.path not in ("", "/") or url.query_string or url.fragment:
        raise InvalidUrl("url_has_path")
    port = f":{url.explicit_port}" if url.explicit_port else ""
    return f"{url.scheme}://{url.host.lower()}{port}"


def is_http_url(text: str) -> bool:
    """Whether text is an absolute http(s) URL with a host."""
    try:
        url = URL(text.strip())
    except ValueError:
        return False
    return url.scheme in ("http", "https") and bool(url.host)


def is_valid_path(path: str) -> bool:
    """A check path starts with a slash and contains no whitespace."""
    return path.startswith("/") and not any(char.isspace() for char in path)


def clean_list(values: list[str] | None) -> list[str]:
    """Strip, drop empties and duplicates, keep order."""
    seen: dict[str, None] = {}
    for value in values or []:
        text = value.strip()
        if text:
            seen.setdefault(text)
    return list(seen)


def clean_check(
    user_input: dict[str, Any], key: str | None = None
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate one check form. Returns (stored check, errors)."""
    path = user_input.get(CONF_CHECK_PATH, "").strip()
    if not is_valid_path(path):
        return {}, {CONF_CHECK_PATH: "invalid_path"}
    check_type = user_input.get(CONF_CHECK_TYPE, CHECK_TYPE_PAGE)
    must_contain = clean_list(user_input.get(CONF_MUST_CONTAIN))
    must_have_keys = clean_list(user_input.get(CONF_MUST_HAVE_KEYS))
    return {
        CONF_CHECK_KEY: key or uuid4().hex[:12],
        CONF_CHECK_LABEL: (user_input.get(CONF_CHECK_LABEL) or "").strip() or path,
        CONF_CHECK_PATH: path,
        CONF_CHECK_TYPE: check_type,
        # Keep only the field the check type uses, so stale entries from a
        # changed type can never be silently ignored.
        CONF_MUST_CONTAIN: must_contain if check_type == CHECK_TYPE_PAGE else [],
        CONF_MUST_HAVE_KEYS: must_have_keys if check_type == CHECK_TYPE_JSON else [],
    }, {}


def basics_errors(user_input: dict[str, Any]) -> dict[str, str]:
    """Errors for the first step of a check form."""
    path = (user_input.get(CONF_CHECK_PATH) or "").strip()
    return {} if is_valid_path(path) else {CONF_CHECK_PATH: "invalid_path"}


class _CheckDetailSteps:
    """Second step of a check form, shared by the config and options flows.

    The host flow stores the first step's answers in `_basics`, may offer
    current values through `_current_details`, and saves the finished check in
    `_async_finish_check`.
    """

    _basics: dict[str, Any]

    def _current_details(self) -> dict[str, Any]:
        return {}

    async def _async_finish_check(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        raise NotImplementedError

    async def _async_step_details(self) -> ConfigFlowResult:
        if self._basics.get(CONF_CHECK_TYPE) == CHECK_TYPE_JSON:
            return await self.async_step_check_json()
        return await self.async_step_check_page()

    async def async_step_check_page(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Required text for a page check."""
        return await self._details_form("check_page", CHECK_PAGE_SCHEMA, user_input)

    async def async_step_check_json(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Required keys for a JSON check."""
        return await self._details_form("check_json", CHECK_JSON_SCHEMA, user_input)

    async def _details_form(
        self, step_id: str, schema: vol.Schema, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._async_finish_check(user_input)
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(  # type: ignore[attr-defined]
                schema, self._current_details()
            ),
            description_placeholders={
                "path": (self._basics.get(CONF_CHECK_PATH) or "").strip()
            },
        )


async def async_probe(hass: HomeAssistant, base_url: str) -> bool:
    """One GET of the homepage, used only while adding a site."""
    result = await fetch_once(
        async_get_clientsession(hass),
        join_url(base_url, "/"),
        timeout_s=DEFAULT_TIMEOUT,
        user_agent=DEFAULT_USER_AGENT,
    )
    return result.ok


class WebsiteHealthConfigFlow(_CheckDetailSteps, ConfigFlow, domain=DOMAIN):
    """Add a website to watch."""

    VERSION = 1

    def __init__(self) -> None:
        self._base_url = ""
        self._title = ""
        self._basics: dict[str, Any] = {}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                base_url = normalize_base_url(user_input[CONF_BASE_URL])
            except InvalidUrl as err:
                errors[CONF_BASE_URL] = err.code
            else:
                await self.async_set_unique_id(base_url)
                self._abort_if_unique_id_configured()
                # A site that is down today is still worth watching, so a
                # failed probe can be overridden instead of blocking setup.
                if user_input.get(CONF_ADD_ANYWAY) or await async_probe(
                    self.hass, base_url
                ):
                    self._base_url = base_url
                    self._title = (user_input.get(CONF_NAME) or "").strip() or str(
                        URL(base_url).host
                    )
                    return await self.async_step_first_check()
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_first_check(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the first check; more can be added in options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = basics_errors(user_input)
            if not errors:
                self._basics = user_input
                return await self._async_step_details()
        return self.async_show_form(
            step_id="first_check",
            data_schema=self.add_suggested_values_to_schema(
                CHECK_BASICS_SCHEMA, user_input
            ),
            errors=errors,
            description_placeholders={"base_url": self._base_url},
        )

    async def _async_finish_check(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        check, _ = clean_check({**self._basics, **user_input})
        return self.async_create_entry(
            title=self._title,
            data={CONF_BASE_URL: self._base_url},
            options={**DEFAULT_OPTIONS, CONF_CHECKS: [check]},
        )

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return WebsiteHealthOptionsFlow()


class WebsiteHealthOptionsFlow(_CheckDetailSteps, OptionsFlow):
    """Menu of checks and settings. Each item saves and closes on submit."""

    def __init__(self) -> None:
        self._edit_key: str | None = None
        self._basics: dict[str, Any] = {}

    def _editing(self) -> dict[str, Any] | None:
        return next(
            (c for c in self._checks if c[CONF_CHECK_KEY] == self._edit_key), None
        )

    def _current_details(self) -> dict[str, Any]:
        """When editing without changing the type, offer the current list."""
        current = self._editing()
        if current and current[CONF_CHECK_TYPE] == self._basics.get(CONF_CHECK_TYPE):
            return current
        return {}

    async def _async_finish_check(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        check, _ = clean_check({**self._basics, **user_input}, key=self._edit_key)
        if self._edit_key is None:
            return self._save(**{CONF_CHECKS: [*self._checks, check]})
        checks = [
            check if c[CONF_CHECK_KEY] == self._edit_key else c for c in self._checks
        ]
        return self._save(**{CONF_CHECKS: checks})

    @property
    def _options(self) -> dict[str, Any]:
        return {**DEFAULT_OPTIONS, **self.config_entry.options}

    @property
    def _checks(self) -> list[dict[str, Any]]:
        return list(self._options.get(CONF_CHECKS, []))

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self._options, **changes})

    def _check_choices(self) -> list[SelectOptionDict]:
        return [
            SelectOptionDict(
                value=check[CONF_CHECK_KEY],
                label=f"{check[CONF_CHECK_LABEL]} ({check[CONF_CHECK_PATH]})",
            )
            for check in self._checks
        ]

    @override
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        menu = ["add_check"]
        if self._checks:
            menu += ["choose_check", "remove_check"]
        menu += ["timing", "build_info", "internet_check", "advanced"]
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_add_check(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add one check: name, path and type first."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = basics_errors(user_input)
            if not errors:
                self._edit_key = None
                self._basics = user_input
                return await self._async_step_details()
        return self.async_show_form(
            step_id="add_check",
            data_schema=self.add_suggested_values_to_schema(
                CHECK_BASICS_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_choose_check(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the check to edit."""
        if user_input is not None:
            self._edit_key = user_input[CONF_SELECTED_CHECK]
            return await self.async_step_edit_check()
        return self.async_show_form(
            step_id="choose_check",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SELECTED_CHECK): SelectSelector(
                        SelectSelectorConfig(options=self._check_choices())
                    )
                }
            ),
        )

    async def async_step_edit_check(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the chosen check, keeping its key so its entity stays."""
        current = self._editing()
        if current is None:
            return self.async_abort(reason="check_not_found")
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = basics_errors(user_input)
            if not errors:
                self._basics = user_input
                return await self._async_step_details()
        return self.async_show_form(
            step_id="edit_check",
            data_schema=self.add_suggested_values_to_schema(
                CHECK_BASICS_SCHEMA, user_input or current
            ),
            errors=errors,
        )

    async def async_step_remove_check(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove checks; at least one must remain."""
        errors: dict[str, str] = {}
        if user_input is not None:
            remove = set(user_input.get(CONF_REMOVE_CHECKS, []))
            remaining = [c for c in self._checks if c[CONF_CHECK_KEY] not in remove]
            if not remaining:
                errors["base"] = "no_checks_left"
            else:
                return self._save(**{CONF_CHECKS: remaining})
        return self.async_show_form(
            step_id="remove_check",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REMOVE_CHECKS, default=[]): SelectSelector(
                        SelectSelectorConfig(
                            options=self._check_choices(),
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_timing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Interval, timeout, retries and the failed-sweeps threshold."""
        if user_input is not None:
            return self._save(**user_input)
        options = self._options

        def number(key: str, low: int, high: int) -> tuple:
            return (
                vol.Required(key, default=options[key]),
                vol.All(vol.Coerce(int), vol.Range(min=low, max=high)),
            )

        return self.async_show_form(
            step_id="timing",
            data_schema=vol.Schema(
                dict(
                    [
                        number(
                            CONF_SCAN_INTERVAL, MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL
                        ),
                        number(CONF_TIMEOUT, MIN_TIMEOUT, MAX_TIMEOUT),
                        number(CONF_RETRIES, 0, MAX_RETRIES),
                        number(CONF_RETRY_DELAY, 0, MAX_RETRY_DELAY),
                        number(CONF_FAILURES_BEFORE_DOWN, 1, MAX_FAILURES_BEFORE_DOWN),
                    ]
                )
            ),
        )

    async def async_step_build_info(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optional JSON file that names the live build."""
        errors: dict[str, str] = {}
        if user_input is not None:
            path = (user_input.get(CONF_BUILD_INFO_PATH) or "").strip()
            keys = clean_list(user_input.get(CONF_BUILD_INFO_KEYS))
            if path and not is_valid_path(path):
                errors[CONF_BUILD_INFO_PATH] = "invalid_path"
            elif path and not keys:
                errors[CONF_BUILD_INFO_KEYS] = "no_build_keys"
            else:
                return self._save(
                    **{
                        CONF_BUILD_INFO_PATH: path,
                        CONF_BUILD_INFO_KEYS: keys if path else [],
                    }
                )
        schema = vol.Schema(
            {
                vol.Optional(CONF_BUILD_INFO_PATH): TextSelector(),
                vol.Optional(CONF_BUILD_INFO_KEYS): LIST_TEXT,
            }
        )
        return self.async_show_form(
            step_id="build_info",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self._options
            ),
            errors=errors,
        )

    async def async_step_internet_check(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Control URLs used to tell a home outage from a site outage."""
        errors: dict[str, str] = {}
        if user_input is not None:
            urls = clean_list(user_input.get(CONF_CONTROL_URLS))
            if not urls or not all(is_http_url(url) for url in urls):
                errors[CONF_CONTROL_URLS] = "invalid_control_urls"
            else:
                return self._save(**{CONF_CONTROL_URLS: urls})
        schema = vol.Schema({vol.Required(CONF_CONTROL_URLS): LIST_TEXT})
        return self.async_show_form(
            step_id="internet_check",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self._options
            ),
            errors=errors,
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """User agent, TLS verification and the heartbeat ping."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_agent = (user_input.get(CONF_USER_AGENT) or "").strip()
            heartbeat = (user_input.get(CONF_HEARTBEAT_URL) or "").strip()
            if not user_agent:
                errors[CONF_USER_AGENT] = "empty_user_agent"
            elif heartbeat and not is_http_url(heartbeat):
                errors[CONF_HEARTBEAT_URL] = "invalid_heartbeat_url"
            else:
                return self._save(
                    **{
                        CONF_USER_AGENT: user_agent,
                        CONF_VERIFY_SSL: bool(user_input.get(CONF_VERIFY_SSL, True)),
                        CONF_HEARTBEAT_URL: heartbeat,
                    }
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_USER_AGENT): TextSelector(),
                vol.Required(CONF_VERIFY_SSL): BooleanSelector(),
                vol.Optional(CONF_HEARTBEAT_URL): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="advanced",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self._options
            ),
            errors=errors,
        )
