"""Config Flow fuer DHL Sendungsverfolgung."""
from __future__ import annotations

import base64
import logging
import urllib.parse
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

from .const import (
    API_TIMEOUT,
    API_TYPE_PARCEL_DE,
    API_TYPE_UNIFIED,
    CONF_API_KEY,
    CONF_API_SECRET,
    CONF_API_TYPE,
    CONF_IMAP_ENABLED,
    CONF_IMAP_FOLDER,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_PORT,
    CONF_IMAP_PROVIDER,
    CONF_IMAP_SCAN_INTERVAL,
    CONF_IMAP_SERVER,
    CONF_IMAP_SSL,
    CONF_IMAP_USERNAME,
    CONF_LABELS,
    CONF_POSTAL_CODES,
    CONF_SANDBOX,
    CONF_TRACKING_NUMBERS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_IMAP_FOLDER,
    DEFAULT_IMAP_PORT,
    DEFAULT_IMAP_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    IMAP_PROVIDERS,
    IMAP_PROVIDER_LABELS,
    MIN_SCAN_INTERVAL,
    PARCEL_DE_SANDBOX_URL,
    SANDBOX_APPNAME,
    SANDBOX_PASSWORD,
    SANDBOX_TRACKING_NUMBERS,
    UNIFIED_API_SANDBOX_URL,
    UNIFIED_API_URL,
)

_LOGGER = logging.getLogger(__name__)

API_TYPE_OPTIONS = [
    {"value": API_TYPE_PARCEL_DE, "label": "Parcel DE Tracking - empfohlen fuer Deutschland"},
    {"value": API_TYPE_UNIFIED,   "label": "Shipment Tracking - Unified (API-Freischaltung erforderlich)"},
]

IMAP_PROVIDER_OPTIONS = [
    {"value": k, "label": v} for k, v in IMAP_PROVIDER_LABELS.items()
]


async def _validate_credentials(api_key: str, api_secret: str, api_type: str, sandbox: bool) -> str | None:
    """Validierung – nur wenn Sandbox oder Unified API aktiv."""
    # Parcel DE Produktion: Website-API, keine Credentials noetig
    if api_type == API_TYPE_PARCEL_DE and not sandbox:
        return None

    # Sandbox oder Unified API: API-Key ist Pflicht
    if not api_key.strip():
        return "api_key_required"

    try:
        async with aiohttp.ClientSession() as session:
            if api_type == API_TYPE_PARCEL_DE and sandbox:
                xml_body = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
                    f'<data appname="{SANDBOX_APPNAME}" language-code="de" '
                    f'password="{SANDBOX_PASSWORD}" piece-code="{SANDBOX_TRACKING_NUMBERS[0]}" '
                    'request="d-get-piece-detail"/>'
                )
                url  = f"{PARCEL_DE_SANDBOX_URL}?xml={urllib.parse.quote(xml_body)}"
                auth = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
                async with session.get(url,
                    headers={"DHL-API-Key": api_key, "Authorization": f"Basic {auth}",
                             "Accept": "application/xml,text/xml,*/*"},
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                ) as resp:
                    return "invalid_api_key" if resp.status == 401 else None
            else:
                url = f"{UNIFIED_API_SANDBOX_URL if sandbox else UNIFIED_API_URL}?trackingNumber=test"
                async with session.get(url,
                    headers={"DHL-API-Key": api_key, "Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                ) as resp:
                    return "invalid_api_key" if resp.status == 401 else None
    except aiohttp.ClientError:
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        return "unknown"


class DhlTrackingConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            api_type = user_input.get(CONF_API_TYPE, API_TYPE_PARCEL_DE)
            sandbox  = user_input.get(CONF_SANDBOX, False)
            error = await _validate_credentials(
                user_input[CONF_API_KEY],
                user_input.get(CONF_API_SECRET, ""),
                api_type, sandbox,
            )
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title="DHL Sendungsverfolgung",
                    data={
                        CONF_API_KEY:    user_input[CONF_API_KEY],
                        CONF_API_SECRET: user_input.get(CONF_API_SECRET, ""),
                        CONF_API_TYPE:   api_type,
                        CONF_SANDBOX:    sandbox,
                    },
                    options={
                        CONF_TRACKING_NUMBERS: [],
                        CONF_UPDATE_INTERVAL:  DEFAULT_SCAN_INTERVAL,
                        CONF_LABELS:           {},
                        CONF_POSTAL_CODES:     {},
                        CONF_IMAP_ENABLED:     False,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional(CONF_API_KEY, default=""): str,
                vol.Optional(CONF_API_SECRET, default=""): str,
                vol.Required(CONF_API_TYPE, default=API_TYPE_PARCEL_DE): SelectSelector(
                    SelectSelectorConfig(options=API_TYPE_OPTIONS, mode=SelectSelectorMode.LIST)
                ),
                vol.Optional(CONF_SANDBOX, default=False): bool,
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DhlTrackingOptionsFlow:
        return DhlTrackingOptionsFlow(config_entry)


class DhlTrackingOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        opts = config_entry.options
        self._tracking_numbers  = list(opts.get(CONF_TRACKING_NUMBERS, []))
        self._labels            = dict(opts.get(CONF_LABELS, {}))
        self._postal_codes      = dict(opts.get(CONF_POSTAL_CODES, {}))
        self._update_interval   = opts.get(CONF_UPDATE_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self._imap_enabled      = opts.get(CONF_IMAP_ENABLED, False)
        self._imap_provider     = opts.get(CONF_IMAP_PROVIDER, "gmx")
        self._imap_server       = opts.get(CONF_IMAP_SERVER, "")
        self._imap_port         = int(opts.get(CONF_IMAP_PORT, DEFAULT_IMAP_PORT))
        self._imap_ssl          = opts.get(CONF_IMAP_SSL, True)
        self._imap_username     = opts.get(CONF_IMAP_USERNAME, "")
        self._imap_password     = opts.get(CONF_IMAP_PASSWORD, "")
        self._imap_folder       = opts.get(CONF_IMAP_FOLDER, DEFAULT_IMAP_FOLDER)
        self._imap_interval     = int(opts.get(CONF_IMAP_SCAN_INTERVAL, DEFAULT_IMAP_SCAN_INTERVAL))

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        return await self.async_step_menu()

    async def async_step_menu(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="menu",
            menu_options=["add_tracking", "manage_trackings", "email_scanner", "settings"],
        )

    async def async_step_add_tracking(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            number = user_input["tracking_number"].strip().replace(" ", "").upper()
            label  = user_input.get("label", "").strip()
            plz    = user_input.get("postal_code", "").strip()
            if len(number) < 5:
                errors["tracking_number"] = "invalid_tracking_number"
            elif number in self._tracking_numbers:
                errors["tracking_number"] = "tracking_already_added"
            else:
                self._tracking_numbers.append(number)
                if label: self._labels[number] = label
                if plz:   self._postal_codes[number] = plz
                return self._save()
        return self.async_show_form(
            step_id="add_tracking",
            data_schema=vol.Schema({
                vol.Required("tracking_number"): str,
                vol.Optional("label", default=""): str,
                vol.Optional("postal_code", default=""): str,
            }),
            errors=errors,
        )

    async def async_step_manage_trackings(self, user_input=None) -> ConfigFlowResult:
        if not self._tracking_numbers:
            return self.async_show_form(
                step_id="manage_trackings", data_schema=vol.Schema({}),
                errors={"base": "no_trackings"},
            )
        if user_input is not None:
            for n in user_input.get("remove", []):
                self._tracking_numbers = [x for x in self._tracking_numbers if x != n]
                self._labels.pop(n, None)
                self._postal_codes.pop(n, None)
            return self._save()
        return self.async_show_form(
            step_id="manage_trackings",
            data_schema=vol.Schema({
                vol.Optional("remove", default=[]): cv.multi_select(
                    {n: self._labels.get(n, n) for n in self._tracking_numbers}
                ),
            }),
            errors={},
        )

    async def async_step_email_scanner(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            enabled  = user_input.get(CONF_IMAP_ENABLED, False)
            provider = user_input.get(CONF_IMAP_PROVIDER, "gmx")
            auto_server, auto_port = IMAP_PROVIDERS.get(provider, ("", DEFAULT_IMAP_PORT))
            server   = auto_server if (auto_server and provider != "custom") \
                       else user_input.get(CONF_IMAP_SERVER, "")
            port     = int(user_input.get(CONF_IMAP_PORT, auto_port or DEFAULT_IMAP_PORT))
            ssl      = user_input.get(CONF_IMAP_SSL, True)
            username = user_input.get(CONF_IMAP_USERNAME, "").strip()
            password = user_input.get(CONF_IMAP_PASSWORD, "")
            folder   = user_input.get(CONF_IMAP_FOLDER, DEFAULT_IMAP_FOLDER) or DEFAULT_IMAP_FOLDER
            interval = int(user_input.get(CONF_IMAP_SCAN_INTERVAL, DEFAULT_IMAP_SCAN_INTERVAL))

            if enabled and (not server or not username or not password):
                errors["base"] = "imap_missing_fields"
            else:
                self._imap_enabled  = enabled
                self._imap_provider = provider
                self._imap_server   = server
                self._imap_port     = port
                self._imap_ssl      = ssl
                self._imap_username = username
                self._imap_password = password
                self._imap_folder   = folder
                self._imap_interval = interval
                return self._save()

        return self.async_show_form(
            step_id="email_scanner",
            data_schema=vol.Schema({
                vol.Optional(CONF_IMAP_ENABLED,       default=self._imap_enabled):  bool,
                vol.Optional(CONF_IMAP_PROVIDER,      default=self._imap_provider): SelectSelector(
                    SelectSelectorConfig(options=IMAP_PROVIDER_OPTIONS, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_IMAP_SERVER,        default=self._imap_server):   str,
                vol.Optional(CONF_IMAP_PORT,          default=self._imap_port): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Optional(CONF_IMAP_SSL,           default=self._imap_ssl):      bool,
                vol.Optional(CONF_IMAP_USERNAME,      default=self._imap_username): str,
                vol.Optional(CONF_IMAP_PASSWORD,      default=self._imap_password): str,
                vol.Optional(CONF_IMAP_FOLDER,        default=self._imap_folder):   str,
                vol.Optional(CONF_IMAP_SCAN_INTERVAL, default=self._imap_interval): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
            }),
            errors=errors,
        )

    async def async_step_settings(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            interval = int(user_input[CONF_UPDATE_INTERVAL])
            if interval < MIN_SCAN_INTERVAL:
                errors[CONF_UPDATE_INTERVAL] = "interval_too_low"
            else:
                self._update_interval = interval
                return self._save()
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema({
                vol.Required(CONF_UPDATE_INTERVAL, default=self._update_interval): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)
                ),
            }),
            errors=errors,
        )

    def _save(self) -> ConfigFlowResult:
        return self.async_create_entry(title="", data={
            CONF_TRACKING_NUMBERS:  self._tracking_numbers,
            CONF_UPDATE_INTERVAL:   self._update_interval,
            CONF_LABELS:            self._labels,
            CONF_POSTAL_CODES:      self._postal_codes,
            CONF_IMAP_ENABLED:      self._imap_enabled,
            CONF_IMAP_PROVIDER:     self._imap_provider,
            CONF_IMAP_SERVER:       self._imap_server,
            CONF_IMAP_PORT:         self._imap_port,
            CONF_IMAP_SSL:          self._imap_ssl,
            CONF_IMAP_USERNAME:     self._imap_username,
            CONF_IMAP_PASSWORD:     self._imap_password,
            CONF_IMAP_FOLDER:       self._imap_folder,
            CONF_IMAP_SCAN_INTERVAL: self._imap_interval,
        })
