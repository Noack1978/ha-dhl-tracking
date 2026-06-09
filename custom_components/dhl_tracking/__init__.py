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
    CARRIER_DHL,
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
from datetime import timedelta
from homeassistant.helpers.event import async_track_time_interval
from .archive_store import DhlArchiveStore
from .coordinator import DhlTrackingCoordinator
from .imap_scanner import DhlImapScanner
from .const import (
    ARCHIVE_KEY,
    CONF_ARCHIVE_DAYS,
    CONF_NOTIFY_TARGET,
    CONF_REMINDER_ENABLED,
    DEFAULT_ARCHIVE_DAYS,
)

_LOGGER = logging.getLogger(__name__)
IMAP_SCANNER_KEY   = f"{DOMAIN}_imap_scanner"
REMINDER_UNSUB_KEY = f"{DOMAIN}_reminder_unsub"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Archiv laden
    archive = DhlArchiveStore(hass)
    await archive.async_load()
    hass.data.setdefault(ARCHIVE_KEY, {})[entry.entry_id] = archive

    coordinator = DhlTrackingCoordinator(
        hass=hass,
        api_key=entry.data.get(CONF_API_KEY, ""),
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
    entry.async_on_unload(
        coordinator.async_add_listener(_async_auto_update_labels(hass, entry, coordinator))
    )
    _async_register_services(hass)
    await _async_start_imap_scanner(hass, entry)
    _async_start_reminder(hass, entry)
    return True


async def _async_start_imap_scanner(hass: HomeAssistant, entry: ConfigEntry) -> None:
    if not entry.options.get(CONF_IMAP_ENABLED, False):
        return
    scanner = DhlImapScanner(hass, entry)
    hass.data.setdefault(IMAP_SCANNER_KEY, {})[entry.entry_id] = scanner
    await scanner.async_start()


async def _async_stop_imap_scanner(hass: HomeAssistant, entry_id: str) -> None:
    scanner = hass.data.get(IMAP_SCANNER_KEY, {}).pop(entry_id, None)
    if scanner:
        await scanner.async_stop()


def _async_start_reminder(hass: HomeAssistant, entry: ConfigEntry) -> None:
    if not entry.options.get(CONF_REMINDER_ENABLED, True):
        return

    async def _check(_now=None):
        archive = hass.data.get(ARCHIVE_KEY, {}).get(entry.entry_id)
        if not archive:
            return
        days    = entry.options.get(CONF_ARCHIVE_DAYS, DEFAULT_ARCHIVE_DAYS)
        target  = entry.options.get(CONF_NOTIFY_TARGET, "")
        pending = archive.get_pending(days)
        if not pending or archive.reminded_today() or not target:
            return
        count  = len(pending)
        labels = ", ".join(p.get("label", k) for k, p in list(pending.items())[:3])
        if count > 3:
            labels += f" und {count - 3} weitere"
        svc_domain, svc_name = (target.split(".", 1) if "." in target else (target, target))
        await hass.services.async_call(
            svc_domain, svc_name,
            {
                "title": f"DHL Archiv: {count} Sendung(en) loeschbereit",
                "message": (
                    f"{labels} {'ist' if count == 1 else 'sind'} "
                    f"seit mehr als {days} Tagen im Archiv. "
                    "Bitte in der DHL-Karte bestaetigen."
                ),
            },
            blocking=False,
        )
        await archive.async_set_reminded()

    unsub = async_track_time_interval(hass, _check, timedelta(hours=1))
    hass.data.setdefault(REMINDER_UNSUB_KEY, {})[entry.entry_id] = unsub


def _async_stop_reminder(hass: HomeAssistant, entry_id: str) -> None:
    unsub = hass.data.get(REMINDER_UNSUB_KEY, {}).pop(entry_id, None)
    if unsub:
        unsub()


def _async_auto_update_labels(hass, entry, coordinator):
    """Gibt einen Callback zurueck der Labels automatisch auf Absendernamen setzt."""
    def _callback():
        if not coordinator.data:
            return
        labels   = dict(entry.options.get(CONF_LABELS, {}))
        updated  = False
        for number, data in coordinator.data.items():
            sender = data.get("_sender", "")
            if sender and labels.get(number, "") in ("E-Mail Import", "", number):
                labels[number] = sender
                updated = True
                _LOGGER.info("Label fuer %s auf '%s' aktualisiert.", number, sender)
        if updated:
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, CONF_LABELS: labels}
            )
    return _callback


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await _async_stop_imap_scanner(hass, entry.entry_id)
    _async_stop_reminder(hass, entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await _async_stop_imap_scanner(hass, entry.entry_id)
    _async_stop_reminder(hass, entry.entry_id)
    hass.data.get(ARCHIVE_KEY, {}).pop(entry.entry_id, None)
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
        if not entry:
            return
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
            CONF_LABELS:           labels,
            CONF_POSTAL_CODES:     postal,
        })
        await hass.config_entries.async_reload(entry.entry_id)

    async def handle_remove_tracking(call: ServiceCall) -> None:
        entry = _get_entry(call.data.get("entry_id"))
        if not entry:
            return
        number     = call.data["tracking_number"].strip().replace(" ", "").upper()
        entity_reg = er.async_get(hass)
        unique_id  = f"dhl_{entry.entry_id}_{number}"
        entity_id  = entity_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id:
            entity_reg.async_remove(entity_id)
        hass.config_entries.async_update_entry(entry, options={
            **entry.options,
            CONF_TRACKING_NUMBERS: [n for n in entry.options.get(CONF_TRACKING_NUMBERS, []) if n != number],
            CONF_LABELS:    {k: v for k, v in entry.options.get(CONF_LABELS, {}).items() if k != number},
            CONF_POSTAL_CODES: {k: v for k, v in entry.options.get(CONF_POSTAL_CODES, {}).items() if k != number},
        })
        await hass.config_entries.async_reload(entry.entry_id)

    async def handle_refresh(call: ServiceCall) -> None:
        entry = _get_entry(call.data.get("entry_id"))
        if not entry:
            return
        coord = hass.data[DOMAIN].get(entry.entry_id)
        if coord:
            await coord.async_refresh()

    def _get_archive(entry_id=None):
        entry = _get_entry(entry_id)
        return hass.data.get(ARCHIVE_KEY, {}).get(entry.entry_id) if entry else None

    async def handle_archive_tracking(call: ServiceCall) -> None:
        entry   = _get_entry(call.data.get("entry_id"))
        archive = _get_archive(call.data.get("entry_id"))
        if not entry or not archive:
            return
        number = call.data["tracking_number"].strip().replace(" ", "").upper()
        coord  = hass.data[DOMAIN].get(entry.entry_id)
        label  = entry.options.get(CONF_LABELS, {}).get(number, number)
        status, events = "", []
        if coord and coord.data:
            d      = coord.data.get(number, {})
            status = d.get("status", {}).get("description", "")
            events = d.get("events", [])
        await archive.async_archive(number, label, status, events)
        entity_reg = er.async_get(hass)
        eid = entity_reg.async_get_entity_id("sensor", DOMAIN, f"dhl_{entry.entry_id}_{number}")
        if eid:
            entity_reg.async_remove(eid)
        hass.config_entries.async_update_entry(entry, options={
            **entry.options,
            CONF_TRACKING_NUMBERS: [n for n in entry.options.get(CONF_TRACKING_NUMBERS, []) if n != number],
            CONF_LABELS:      {k: v for k, v in entry.options.get(CONF_LABELS, {}).items() if k != number},
            CONF_POSTAL_CODES:{k: v for k, v in entry.options.get(CONF_POSTAL_CODES, {}).items() if k != number},
        })
        await hass.config_entries.async_reload(entry.entry_id)

    async def handle_purge_archive(call: ServiceCall) -> None:
        entry   = _get_entry(call.data.get("entry_id"))
        archive = _get_archive(call.data.get("entry_id"))
        if not archive:
            return
        numbers = call.data.get("tracking_numbers", [])
        if not numbers:
            days    = entry.options.get(CONF_ARCHIVE_DAYS, DEFAULT_ARCHIVE_DAYS) if entry else DEFAULT_ARCHIVE_DAYS
            numbers = list(archive.get_pending(days).keys())
        await archive.async_purge(numbers)
        if entry:
            await hass.config_entries.async_reload(entry.entry_id)

    hass.services.async_register(DOMAIN, "add_tracking", handle_add_tracking,
        schema=vol.Schema({
            vol.Required("tracking_number"): cv.string,
            vol.Optional("label",       default=""): cv.string,
            vol.Optional("postal_code", default=""): cv.string,
            vol.Optional("entry_id"):   cv.string,
        }))
    hass.services.async_register(DOMAIN, "remove_tracking", handle_remove_tracking,
        schema=vol.Schema({
            vol.Required("tracking_number"): cv.string,
            vol.Optional("entry_id"): cv.string,
        }))
    hass.services.async_register(DOMAIN, "archive_tracking", handle_archive_tracking,
        schema=vol.Schema({
            vol.Required("tracking_number"): cv.string,
            vol.Optional("entry_id"): cv.string,
        }))
    hass.services.async_register(DOMAIN, "purge_archive", handle_purge_archive,
        schema=vol.Schema({
            vol.Optional("tracking_numbers", default=[]): [cv.string],
            vol.Optional("entry_id"): cv.string,
        }))
    hass.services.async_register(DOMAIN, "refresh", handle_refresh,
        schema=vol.Schema({vol.Optional("entry_id"): cv.string}))
