"""DHL Sendungsverfolgung - Home Assistant Custom Integration."""
from __future__ import annotations
import logging
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from .const import (
    API_TYPE_PARCEL_DE,
    CONF_API_KEY,
    CONF_API_SECRET,
    CONF_API_TYPE,
    CONF_IMAP_ENABLED,
    CONF_LABELS,
    CONF_POSTAL_CODES,
    CONF_SANDBOX,
    CONF_TRACKING_NUMBERS,
    CONF_UPDATE_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import DhlTrackingCoordinator
from .imap_scanner import DhlImapScanner

_LOGGER = logging.getLogger(__name__)
IMAP_SCANNER_KEY = f"{DOMAIN}_imap_scanner"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    coordinator = DhlTrackingCoordinator(
        hass=hass,
        api_key=entry.data[CONF_API_KEY],
        api_secret=entry.data.get(CONF_API_SECRET, ""),
        api_type=entry.data.get(CONF_API_TYPE, API_TYPE_PARCEL_DE),
        tracking_numbers=entry.options.get(CONF_TRACKING_NUMBERS, []),
        postal_codes=entry.options.get(CONF_POSTAL_CODES, {}),
        scan_interval=entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_SCAN_INTERVAL),
        sandbox=entry.data.get(CONF_SANDBOX, False),
    )
    if entry.options.get(CONF_TRACKING_NUMBERS):
        await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    _async_register_services(hass)
    await _async_start_imap_scanner(hass, entry)
    return True


async def _async_start_imap_scanner(hass: HomeAssistant, entry: ConfigEntry) -> None:
    if not entry.options.get(CONF_IMAP_ENABLED, False):
        return
    scanner = DhlImapScanner(hass, entry)
    hass.data.setdefault(IMAP_SCANNER_KEY, {})[entry.entry_id] = scanner
    await scanner.async_start()
    _LOGGER.info("DHL IMAP-Scanner aktiviert.")


async def _async_stop_imap_scanner(hass: HomeAssistant, entry_id: str) -> None:
    scanner = hass.data.get(IMAP_SCANNER_KEY, {}).pop(entry_id, None)
    if scanner:
        await scanner.async_stop()


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await _async_stop_imap_scanner(hass, entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await _async_stop_imap_scanner(hass, entry.entry_id)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "add_tracking"):
        return

    def _get_entry(entry_id=None):
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            return None
        return next((e for e in entries if e.entry_id == entry_id), entries[0])

    async def handle_add_tracking(call: ServiceCall) -> None:
        entry = _get_entry(call.data.get("entry_id"))
        if not entry: return
        number  = call.data["tracking_number"].strip().replace(" ", "").upper()
        label   = call.data.get("label", "").strip()
        plz     = call.data.get("postal_code", "").strip()
        numbers = list(entry.options.get(CONF_TRACKING_NUMBERS, []))
        labels  = dict(entry.options.get(CONF_LABELS, {}))
        postal  = dict(entry.options.get(CONF_POSTAL_CODES, {}))
        if number in numbers:
            _LOGGER.debug("Sendung %s wird bereits verfolgt.", number)
            return
        numbers.append(number)
        if label: labels[number] = label
        if plz:   postal[number] = plz
        hass.config_entries.async_update_entry(entry, options={
            **entry.options,
            CONF_TRACKING_NUMBERS: numbers,
            CONF_LABELS: labels,
            CONF_POSTAL_CODES: postal,
        })
        await hass.config_entries.async_reload(entry.entry_id)

    async def handle_remove_tracking(call: ServiceCall) -> None:
        entry = _get_entry(call.data.get("entry_id"))
        if not entry: return
        number     = call.data["tracking_number"].strip().replace(" ", "").upper()
        entity_reg = er.async_get(hass)
        unique_id  = f"dhl_{entry.entry_id}_{number}"
        entity_id  = entity_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id:
            entity_reg.async_remove(entity_id)
            _LOGGER.debug("Entitaet %s entfernt.", entity_id)
        hass.config_entries.async_update_entry(entry, options={
            **entry.options,
            CONF_TRACKING_NUMBERS: [n for n in entry.options.get(CONF_TRACKING_NUMBERS, []) if n != number],
            CONF_LABELS:    {k: v for k, v in entry.options.get(CONF_LABELS, {}).items() if k != number},
            CONF_POSTAL_CODES: {k: v for k, v in entry.options.get(CONF_POSTAL_CODES, {}).items() if k != number},
        })
        await hass.config_entries.async_reload(entry.entry_id)

    async def handle_refresh(call: ServiceCall) -> None:
        entry = _get_entry(call.data.get("entry_id"))
        if not entry: return
        coord = hass.data[DOMAIN].get(entry.entry_id)
        if coord: await coord.async_refresh()

    hass.services.async_register(DOMAIN, "add_tracking", handle_add_tracking,
        schema=vol.Schema({
            vol.Required("tracking_number"): cv.string,
            vol.Optional("label", default=""): cv.string,
            vol.Optional("postal_code", default=""): cv.string,
            vol.Optional("entry_id"): cv.string,
        }))
    hass.services.async_register(DOMAIN, "remove_tracking", handle_remove_tracking,
        schema=vol.Schema({
            vol.Required("tracking_number"): cv.string,
            vol.Optional("entry_id"): cv.string,
        }))
    hass.services.async_register(DOMAIN, "refresh", handle_refresh,
        schema=vol.Schema({vol.Optional("entry_id"): cv.string}))
