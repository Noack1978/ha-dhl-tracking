"""Config Flow für DHL Sendungsverfolgung."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import (
    API_BASE_URL,
    API_SANDBOX_URL,
    API_TIMEOUT,
    CONF_API_KEY,
    CONF_LABELS,
    CONF_SANDBOX,
    CONF_TRACKING_NUMBERS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


async def _validate_api_key(api_key: str, sandbox: bool) -> str | None:
    """Gibt None zurück wenn der Key gültig ist, sonst einen Fehler-String."""
    base_url = API_SANDBOX_URL if sandbox else API_BASE_URL
    url = f"{base_url}?trackingNumber=validationtest"
    headers = {"DHL-API-Key": api_key, "Accept": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)
            ) as resp:
                if resp.status == 401:
                    return "invalid_api_key"
                if resp.status in (200, 404):  # 404 = Key ok, Sendung unbekannt
                    return None
                _LOGGER.warning("DHL API Validierung: HTTP %s", resp.status)
                return "cannot_connect"
    except aiohttp.ClientError:
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        return "unknown"


class DhlTrackingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config Flow: API-Key einrichten."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            error = await _validate_api_key(
                user_input[CONF_API_KEY],
                user_input.get(CONF_SANDBOX, False),
            )
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title="DHL Sendungsverfolgung",
                    data={
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_SANDBOX: user_input.get(CONF_SANDBOX, False),
                    },
                    options={
                        CONF_TRACKING_NUMBERS: [],
                        CONF_UPDATE_INTERVAL: DEFAULT_SCAN_INTERVAL,
                        CONF_LABELS: {},
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): str,
                    vol.Optional(CONF_SANDBOX, default=False): bool,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DhlTrackingOptionsFlow:
        return DhlTrackingOptionsFlow(config_entry)


class DhlTrackingOptionsFlow(OptionsFlow):
    """Options Flow: Sendungsnummern & Einstellungen verwalten."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        self._tracking_numbers: list[str] = list(
            config_entry.options.get(CONF_TRACKING_NUMBERS, [])
        )
        self._labels: dict[str, str] = dict(
            config_entry.options.get(CONF_LABELS, {})
        )
        self._update_interval: int = config_entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_SCAN_INTERVAL
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_menu()

    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="menu",
            menu_options=["add_tracking", "manage_trackings", "settings"],
        )

    # ── Sendung hinzufügen ───────────────────────────────────────────────────

    async def async_step_add_tracking(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            number = user_input["tracking_number"].strip().replace(" ", "").upper()
            label = user_input.get("label", "").strip()

            if len(number) < 5:
                errors["tracking_number"] = "invalid_tracking_number"
            elif number in self._tracking_numbers:
                errors["tracking_number"] = "tracking_already_added"
            else:
                self._tracking_numbers.append(number)
                if label:
                    self._labels[number] = label
                return self._save()

        return self.async_show_form(
            step_id="add_tracking",
            data_schema=vol.Schema(
                {
                    vol.Required("tracking_number"): str,
                    vol.Optional("label", default=""): str,
                }
            ),
            errors=errors,
        )

    # ── Sendungen verwalten / entfernen ─────────────────────────────────────

    async def async_step_manage_trackings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if not self._tracking_numbers:
            return self.async_show_form(
                step_id="manage_trackings",
                data_schema=vol.Schema({}),
                errors={"base": "no_trackings"},
            )

        if user_input is not None:
            to_remove: list[str] = user_input.get("remove", [])
            self._tracking_numbers = [
                n for n in self._tracking_numbers if n not in to_remove
            ]
            for n in to_remove:
                self._labels.pop(n, None)
            return self._save()

        options = {n: self._labels.get(n, n) for n in self._tracking_numbers}
        return self.async_show_form(
            step_id="manage_trackings",
            data_schema=vol.Schema(
                {vol.Optional("remove", default=[]): cv.multi_select(options)}
            ),
            errors=errors,
        )

    # ── Einstellungen ────────────────────────────────────────────────────────

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
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_UPDATE_INTERVAL, default=self._update_interval
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL))
                }
            ),
            errors=errors,
        )

    # ── Speichern ────────────────────────────────────────────────────────────

    def _save(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title="",
            data={
                CONF_TRACKING_NUMBERS: self._tracking_numbers,
                CONF_UPDATE_INTERVAL: self._update_interval,
                CONF_LABELS: self._labels,
            },
        )
