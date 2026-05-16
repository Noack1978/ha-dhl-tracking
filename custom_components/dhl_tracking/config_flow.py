"""Config Flow für DHL Sendungsverfolgung."""
from __future__ import annotations

import logging
import urllib.parse
from datetime import timedelta
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
    CONF_LABELS,
    CONF_POSTAL_CODES,
    CONF_SANDBOX,
    CONF_TRACKING_NUMBERS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    PARCEL_DE_AUTH_SANDBOX_URL,
    PARCEL_DE_AUTH_URL,
    UNIFIED_API_SANDBOX_URL,
    UNIFIED_API_URL,
)

_LOGGER = logging.getLogger(__name__)

API_TYPE_OPTIONS = [
    {"value": API_TYPE_PARCEL_DE, "label": "Parcel DE Tracking (Post & Parcel Germany) – empfohlen"},
    {"value": API_TYPE_UNIFIED,   "label": "Shipment Tracking – Unified"},
]


async def _validate_credentials(
    api_key: str, api_secret: str, api_type: str, sandbox: bool
) -> str | None:
    """Validiert Zugangsdaten. Gibt None zurück wenn OK, sonst Fehler-String."""
    try:
        async with aiohttp.ClientSession() as session:

            if api_type == API_TYPE_PARCEL_DE:
                # OAuth2 Token anfordern – das ist die echte Validierung
                auth_url = PARCEL_DE_AUTH_SANDBOX_URL if sandbox else PARCEL_DE_AUTH_URL
                payload = {
                    "grant_type":    "client_credentials",
                    "client_id":     api_key,
                    "client_secret": api_secret,
                }
                async with session.post(
                    auth_url,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                ) as resp:
                    _LOGGER.debug("OAuth2 Validierung: HTTP %s", resp.status)
                    if resp.status == 401:
                        return "invalid_api_key"
                    if resp.status == 200:
                        result = await resp.json(content_type=None)
                        if result.get("access_token"):
                            return None  # Erfolgreich!
                        return "invalid_api_key"
                    # Andere Fehler → trotzdem akzeptieren, echter Test beim ersten Abruf
                    return None

            else:
                # Unified API – nur DHL-API-Key Header nötig
                url = f"{UNIFIED_API_SANDBOX_URL if sandbox else UNIFIED_API_URL}?trackingNumber=validationtest"
                async with session.get(
                    url,
                    headers={"DHL-API-Key": api_key, "Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                ) as resp:
                    if resp.status == 401:
                        return "invalid_api_key"
                    return None

    except aiohttp.ClientError:
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        return "unknown"


class DhlTrackingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config Flow: Zugangsdaten einrichten."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            api_type = user_input.get(CONF_API_TYPE, API_TYPE_PARCEL_DE)
            error = await _validate_credentials(
                user_input[CONF_API_KEY],
                user_input.get(CONF_API_SECRET, ""),
                api_type,
                user_input.get(CONF_SANDBOX, False),
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
                        CONF_SANDBOX:    user_input.get(CONF_SANDBOX, False),
                    },
                    options={
                        CONF_TRACKING_NUMBERS: [],
                        CONF_UPDATE_INTERVAL:  DEFAULT_SCAN_INTERVAL,
                        CONF_LABELS:           {},
                        CONF_POSTAL_CODES:     {},
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
                vol.Optional(CONF_SANDBOX, default=False): bool,
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DhlTrackingOptionsFlow:
        return DhlTrackingOptionsFlow(config_entry)


class DhlTrackingOptionsFlow(OptionsFlow):
    """Options Flow: Sendungsnummern & Einstellungen."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry            = config_entry
        self._tracking_numbers = list(config_entry.options.get(CONF_TRACKING_NUMBERS, []))
        self._labels           = dict(config_entry.options.get(CONF_LABELS, {}))
        self._postal_codes     = dict(config_entry.options.get(CONF_POSTAL_CODES, {}))
        self._update_interval  = config_entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_SCAN_INTERVAL)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_menu()

    async def async_step_menu(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="menu",
            menu_options=["add_tracking", "manage_trackings", "settings"],
        )

    async def async_step_add_tracking(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
                if label:
                    self._labels[number] = label
                if plz:
                    self._postal_codes[number] = plz
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

    async def async_step_manage_trackings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._tracking_numbers:
            return self.async_show_form(
                step_id="manage_trackings",
                data_schema=vol.Schema({}),
                errors={"base": "no_trackings"},
            )

        if user_input is not None:
            for n in user_input.get("remove", []):
                self._tracking_numbers = [x for x in self._tracking_numbers if x != n]
                self._labels.pop(n, None)
                self._postal_codes.pop(n, None)
            return self._save()

        options = {n: self._labels.get(n, n) for n in self._tracking_numbers}
        return self.async_show_form(
            step_id="manage_trackings",
            data_schema=vol.Schema(
                {vol.Optional("remove", default=[]): cv.multi_select(options)}
            ),
            errors={},
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
                )
            }),
            errors=errors,
        )

    def _save(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title="",
            data={
                CONF_TRACKING_NUMBERS: self._tracking_numbers,
                CONF_UPDATE_INTERVAL:  self._update_interval,
                CONF_LABELS:           self._labels,
                CONF_POSTAL_CODES:     self._postal_codes,
            },
        )
