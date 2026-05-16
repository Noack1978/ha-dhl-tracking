"""DHL Sendungsverfolgung – Home Assistant Custom Integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    API_TYPE_PARCEL_DE,
    CONF_API_KEY,
    CONF_API_TYPE,
    CONF_LABELS,
    CONF_SANDBOX,
    CONF_TRACKING_NUMBERS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import DhlTrackingCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Integration aus einem Config-Entry einrichten."""
    hass.data.setdefault(DOMAIN, {})

    tracking_numbers: list[str] = entry.options.get(CONF_TRACKING_NUMBERS, [])
    scan_interval: int = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_SCAN_INTERVAL)
    api_type: str = entry.data.get(CONF_API_TYPE, API_TYPE_PARCEL_DE)

    coordinator = DhlTrackingCoordinator(
        hass=hass,
        api_key=entry.data[CONF_API_KEY],
        api_type=api_type,
        tracking_numbers=tracking_numbers,
        scan_interval=scan_interval,
        sandbox=entry.data.get(CONF_SANDBOX, False),
    )

    if tracking_numbers:
        await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    _async_register_services(hass)

    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "add_tracking"):
        return

    def _get_entry(entry_id: str | None) -> ConfigEntry | None:
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            return None
        if entry_id:
            return next((e for e in entries if e.entry_id == entry_id), None)
        return entries[0]

    async def handle_add_tracking(call: ServiceCall) -> None:
        entry = _get_entry(call.data.get("entry_id"))
        if not entry:
            _LOGGER.error("dhl_tracking.add_tracking: Kein Config-Entry gefunden.")
            return
        number = call.data["tracking_number"].strip().replace(" ", "").upper()
        label  = call.data.get("label", "").strip()
        numbers = list(entry.options.get(CONF_TRACKING_NUMBERS, []))
        labels  = dict(entry.options.get(CONF_LABELS, {}))
        if number in numbers:
            _LOGGER.info("Sendungsnummer %s wird bereits verfolgt.", number)
            return
        numbers.append(number)
        if label:
            labels[number] = label
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_TRACKING_NUMBERS: numbers, CONF_LABELS: labels}
        )
        await hass.config_entries.async_reload(entry.entry_id)

    async def handle_remove_tracking(call: ServiceCall) -> None:
        entry = _get_entry(call.data.get("entry_id"))
        if not entry:
            _LOGGER.error("dhl_tracking.remove_tracking: Kein Config-Entry gefunden.")
            return
        number  = call.data["tracking_number"].strip().replace(" ", "").upper()
        numbers = [n for n in entry.options.get(CONF_TRACKING_NUMBERS, []) if n != number]
        labels  = {k: v for k, v in entry.options.get(CONF_LABELS, {}).items() if k != number}
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_TRACKING_NUMBERS: numbers, CONF_LABELS: labels}
        )
        await hass.config_entries.async_reload(entry.entry_id)

    async def handle_refresh(call: ServiceCall) -> None:
        entry = _get_entry(call.data.get("entry_id"))
        if not entry:
            _LOGGER.error("dhl_tracking.refresh: Kein Config-Entry gefunden.")
            return
        coordinator: DhlTrackingCoordinator | None = hass.data[DOMAIN].get(entry.entry_id)
        if coordinator:
            await coordinator.async_refresh()

    hass.services.async_register(
        DOMAIN, "add_tracking", handle_add_tracking,
        schema=vol.Schema({
            vol.Required("tracking_number"): cv.string,
            vol.Optional("label", default=""): cv.string,
            vol.Optional("entry_id"): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, "remove_tracking", handle_remove_tracking,
        schema=vol.Schema({
            vol.Required("tracking_number"): cv.string,
            vol.Optional("entry_id"): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, "refresh", handle_refresh,
        schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
    )
