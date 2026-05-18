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
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    API_TIMEOUT,
    API_TYPE_PARCEL_DE,
    API_TYPE_UNIFIED,
    CONF_API_KEY,
    CONF_API_SECRET,
    CONF_API_TYPE,
    CONF_GKP_PASSWORD,
    CONF_GKP_USER,
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
    PARCEL_DE_URL,
    SANDBOX_APPNAME,
    SANDBOX_PASSWORD,
    SANDBOX_TRACKING_NUMBERS,
    UNIFIED_API_SANDBOX_URL,
    UNIFIED_API_URL,
)

_LOGGER = logging.getLogger(__name__)

API_TYPE_OPTIONS = [
    {"value": API_TYPE_PARCEL_DE, "label": "Parcel DE Tracking (Post & Parcel Germany) - empfohlen"},
    {"value": API_TYPE_UNIFIED,   "label": "Shipment Tracking - Unified"},
]

IMAP_PROVIDER_OPTIONS = [
    {"value": k, "label": v} for k, v in IMAP_PROVIDER_LABELS.items()
]


async def _validate_api(api_key: str, api_secret: str, api_type: str, sandbox: bool) -> str | None:
    try:
        async with aiohttp.ClientSession() as session:
            if api_type == API_TYPE_PARCEL_DE:
                base_url    = PARCEL_DE_SANDBOX_URL if sandbox else PARCEL_DE_URL
                appname     = SANDBOX_APPNAME  if sandbox else "validation"
                password    = SANDBOX_PASSWORD if sandbox else "validation"
                test_number = SANDBOX_TRACKING_NUMBERS[0] if sandbox else "0000000000"
                xml_body = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
                    f'<data appname="{appname}" language-code="de" '
                    f'password="{password}" piece-code="{test_number}" '
                    'request="d-get-piece-detail"/>'
                )
                url  = f"{base_url}?xml={urllib.parse.quote(xml_body)}"
                auth = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
                async with session.get(url,
                    headers={"DHL-API-Key": api_key, "Authorization": f"Basic {auth}",
                             "Accept": "application/xml,text/xml,*/*"},
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                ) as resp:
                    return "invalid_api_key" if resp.status == 401 else None
            else:
                url = f"{UNIFIED_API_SANDBOX_URL if sandbox else UNIFIED_API_URL}?trackingNumber=validationtest"
                async with session.get(url,
                    headers={"DHL-API-Key": api_key, "Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                ) as resp:
                    return "invalid_api_key" if resp.status == 401 else None
    except aiohttp.ClientError:
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        return "unknown"


    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _test)


class DhlTrackingConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            api_type = user_input.get(CONF_API_TYPE, API_TYPE_PARCEL_DE)
            sandbox  = user_input.get(CONF_SANDBOX, False)
            error = await _validate_api(
                user_input[CONF_API_KEY], user_input.get(CONF_API_SECRET, ""),
                api_type, sandbox,
            )
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title="DHL Sendungsverfolgung",
                    data={
                        CONF_API_KEY:      user_input[CONF_API_KEY],
                        CONF_API_SECRET:   user_input.get(CONF_API_SECRET, ""),
                        CONF_API_TYPE:     api_type,
                        CONF_GKP_USER:     user_input.get(CONF_GKP_USER, ""),
                        CONF_GKP_PASSWORD: user_input.get(CONF_GKP_PASSWORD, ""),
                        CONF_SANDBOX:      sandbox,
                    },
                    options={
                        CONF_TRACKING_NUMBERS:  [],
                        CONF_UPDATE_INTERVAL:   DEFAULT_SCAN_INTERVAL,
                        CONF_LABELS:            {},
                        CONF_POSTAL_CODES:      {},
                        CONF_IMAP_ENABLED:      False,
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_API_SECRET): str,
                vol.Required(CONF_API_TYPE, default=API_TYPE_PARCEL_DE): SelectSelector(
                    SelectSelectorConfig(options=API_TYPE_OPTIONS, mode=SelectSelectorMode.LIST)
                ),
                vol.Optional(CONF_GKP_USER, default=""): str,
                vol.Optional(CONF_GKP_PASSWORD, default=""): str,
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
        self._tracking_numbers  = list(config_entry.options.get(CONF_TRACKING_NUMBERS, []))
        self._labels            = dict(config_entry.options.get(CONF_LABELS, {}))
        self._postal_codes      = dict(config_entry.options.get(CONF_POSTAL_CODES, {}))
        self._update_interval   = config_entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self._imap_enabled = config_entry.options.get(CONF_IMAP_ENABLED, False)
        opts = config_entry.options
        self._imap_opts = {
            CONF_IMAP_PROVIDER:      opts.get(CONF_IMAP_PROVIDER, "gmx"),
            CONF_IMAP_SERVER:        opts.get(CONF_IMAP_SERVER, ""),
            CONF_IMAP_PORT:          int(opts.get(CONF_IMAP_PORT, DEFAULT_IMAP_PORT)),
            CONF_IMAP_SSL:           opts.get(CONF_IMAP_SSL, True),
            CONF_IMAP_USERNAME:      opts.get(CONF_IMAP_USERNAME, ""),
            CONF_IMAP_PASSWORD:      opts.get(CONF_IMAP_PASSWORD, ""),
            CONF_IMAP_FOLDER:        opts.get(CONF_IMAP_FOLDER, DEFAULT_IMAP_FOLDER),
            CONF_IMAP_SCAN_INTERVAL: int(opts.get(CONF_IMAP_SCAN_INTERVAL, DEFAULT_IMAP_SCAN_INTERVAL)),
        }

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        return await self.async_step_menu()

    async def async_step_menu(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="menu",
            menu_options=["add_tracking", "manage_trackings", "email_scanner", "settings"],
        )

    # ── Sendung hinzufuegen ──────────────────────────────────────────────────

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

    # ── Sendungen verwalten ──────────────────────────────────────────────────

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

    # ── E-Mail-Scanner ───────────────────────────────────────────────────────

    async def async_step_email_scanner(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            enabled  = user_input.get(CONF_IMAP_ENABLED, False)
            provider = user_input.get(CONF_IMAP_PROVIDER, "gmx")

            # Server automatisch aus Provider ermitteln; bei custom manuell
            auto_server, auto_port = IMAP_PROVIDERS.get(provider, ("", DEFAULT_IMAP_PORT))
            server = auto_server if (auto_server and provider != "custom") \
                     else user_input.get(CONF_IMAP_SERVER, "")

            # Port: aus Provider-Preset oder manuell (Coerce gibt int)
            raw_port = user_input.get(CONF_IMAP_PORT, auto_port or DEFAULT_IMAP_PORT)
            port = int(float(raw_port)) if raw_port else (auto_port or DEFAULT_IMAP_PORT)

            ssl      = user_input.get(CONF_IMAP_SSL, True)
            username = user_input.get(CONF_IMAP_USERNAME, "").strip()
            password = user_input.get(CONF_IMAP_PASSWORD, "")
            folder   = user_input.get(CONF_IMAP_FOLDER, DEFAULT_IMAP_FOLDER) or DEFAULT_IMAP_FOLDER
            raw_int  = user_input.get(CONF_IMAP_SCAN_INTERVAL, DEFAULT_IMAP_SCAN_INTERVAL)
            interval = int(float(raw_int)) if raw_int else DEFAULT_IMAP_SCAN_INTERVAL

            # Pflichtfelder pruefen – KEINE Verbindungsvalidierung
            # (schlaegt bei 2FA immer fehl; Fehler werden beim ersten Scan geloggt)
            if enabled and (not server or not username or not password):
                errors["base"] = "imap_missing_fields"

            if not errors:
                self._imap_enabled = enabled
                self._imap_opts = {
                    CONF_IMAP_PROVIDER:      provider,
                    CONF_IMAP_SERVER:        server,
                    CONF_IMAP_PORT:          port,
                    CONF_IMAP_SSL:           ssl,
                    CONF_IMAP_USERNAME:      username,
                    CONF_IMAP_PASSWORD:      password,
                    CONF_IMAP_FOLDER:        folder,
                    CONF_IMAP_SCAN_INTERVAL: interval,
                }
                return self._save()

        cur_provider = self._imap_opts[CONF_IMAP_PROVIDER]
        cur_port     = self._imap_opts[CONF_IMAP_PORT]

        return self.async_show_form(
            step_id="email_scanner",
            data_schema=vol.Schema({
                vol.Optional(CONF_IMAP_ENABLED,
                    default=self._imap_enabled): bool,
                vol.Optional(CONF_IMAP_PROVIDER,
                    default=cur_provider): SelectSelector(
                    SelectSelectorConfig(
                        options=IMAP_PROVIDER_OPTIONS,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_IMAP_SERVER,
                    default=self._imap_opts[CONF_IMAP_SERVER]): str,
                vol.Optional(CONF_IMAP_PORT,
                    default=cur_port): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Optional(CONF_IMAP_SSL,
                    default=self._imap_opts[CONF_IMAP_SSL]): bool,
                vol.Optional(CONF_IMAP_USERNAME,
                    default=self._imap_opts[CONF_IMAP_USERNAME]): str,
                vol.Optional(CONF_IMAP_PASSWORD,
                    default=self._imap_opts[CONF_IMAP_PASSWORD]): str,
                vol.Optional(CONF_IMAP_FOLDER,
                    default=self._imap_opts[CONF_IMAP_FOLDER]): str,
                vol.Optional(CONF_IMAP_SCAN_INTERVAL,
                    default=self._imap_opts[CONF_IMAP_SCAN_INTERVAL]): vol.All(
                    vol.Coerce(int), vol.Range(min=60, max=3600)
                ),
            }),
            errors=errors,
        )

    # ── Einstellungen ────────────────────────────────────────────────────────

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

    # ── Speichern ────────────────────────────────────────────────────────────

    def _save(self) -> ConfigFlowResult:
        data = {
            CONF_TRACKING_NUMBERS: self._tracking_numbers,
            CONF_UPDATE_INTERVAL:  self._update_interval,
            CONF_LABELS:           self._labels,
            CONF_POSTAL_CODES:     self._postal_codes,
            CONF_IMAP_ENABLED:     self._imap_enabled,
            **self._imap_opts,
        }
        return self.async_create_entry(title="", data=data)
