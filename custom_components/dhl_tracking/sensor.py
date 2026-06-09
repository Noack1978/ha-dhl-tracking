"""DHL Tracking Sensor-Plattform."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_LABELS,
    CONF_TRACKING_NUMBERS,
    DEFAULT_ICON,
    DOMAIN,
    STATUS_DESCRIPTIONS,
    STATUS_ICONS,
)
from .coordinator import DhlTrackingCoordinator
from .archive_store import DhlArchiveStore

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Richtet Sensoren fuer Sendungen und Archiv ein."""
    from .const import ARCHIVE_KEY
    coordinator: DhlTrackingCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    labels: dict[str, str] = config_entry.options.get(CONF_LABELS, {})

    entities = [
        DhlShipmentSensor(coordinator, number, labels.get(number, ""), config_entry)
        for number in coordinator.tracking_numbers
    ]
    archive = hass.data.get(ARCHIVE_KEY, {}).get(config_entry.entry_id)
    if archive:
        entities.append(DhlArchiveSensor(config_entry, archive))
    async_add_entities(entities)


class DhlShipmentSensor(CoordinatorEntity[DhlTrackingCoordinator], SensorEntity):
    """Sensor für eine einzelne DHL-Sendung."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DhlTrackingCoordinator,
        tracking_number: str,
        label: str,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._tracking_number = tracking_number
        self._label = label
        self._config_entry = config_entry
        self._attr_unique_id = f"dhl_{config_entry.entry_id}_{tracking_number}"
        self._attr_name = label if label else tracking_number

    # ── Verfügbarkeit ────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """Sensor ist verfügbar, solange kein hard API-Fehler vorliegt."""
        if not super().available:
            return False
        data = self._shipment_data
        if data is None:
            return False
        error = data.get("_error", "")
        return error not in ("http_401",)

    # ── State ────────────────────────────────────────────────────────────────

    @property
    def native_value(self) -> str:
        """Gibt den lesbaren Sendungsstatus zurück."""
        data = self._shipment_data
        if not data:
            return "Unbekannt"
        if "_error" in data:
            return self._error_state(data["_error"])

        status = data.get("status", {})
        code = status.get("status", "")
        return STATUS_DESCRIPTIONS.get(code, status.get("description", "Unbekannt"))

    # ── Icon ─────────────────────────────────────────────────────────────────

    @property
    def icon(self) -> str:
        data = self._shipment_data
        if not data or "_error" in data:
            return DEFAULT_ICON
        code = data.get("status", {}).get("status", "")
        return STATUS_ICONS.get(code, DEFAULT_ICON)

    # ── Attribute ────────────────────────────────────────────────────────────

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "tracking_number": self._tracking_number,
            "label": self._label,
            "sandbox": self.coordinator.sandbox,
        }

        data = self._shipment_data
        if not data or "_error" in data:
            attrs["error"] = data.get("_error", "no_data") if data else "no_data"
            return attrs

        # Absendername aus API-Antwort
        if data.get("_sender"):
            attrs["sender"] = data["_sender"]

        # Status
        status = data.get("status", {})
        attrs["status_code"] = status.get("status", "")
        attrs["status_description"] = status.get("description", "")

        # Letzter bekannter Ort
        loc_addr = (status.get("location") or {}).get("address") or {}
        if loc_addr:
            attrs["current_location"] = loc_addr.get("addressLocality", "")
            attrs["current_country"] = loc_addr.get("countryCode", "")

        # Zeitstempel letztes Ereignis
        if ts_raw := status.get("timestamp"):
            attrs["last_event_time"] = self._format_datetime(ts_raw)

        # Geschätztes Lieferdatum
        if etd := data.get("estimatedTimeOfDelivery"):
            attrs["estimated_delivery"] = self._format_datetime(etd)

        # Zeitfenster (falls vorhanden)
        if etd_start := data.get("estimatedTimeOfDeliveryRemark"):
            attrs["estimated_delivery_remark"] = etd_start

        # Dienstleistung
        if service := data.get("service"):
            attrs["service"] = service

        # Herkunft / Ziel
        for key in ("origin", "destination"):
            node = data.get(key) or {}
            addr = node.get("address") or {}
            value = addr.get("addressLocality") or addr.get("countryCode", "")
            if value:
                attrs[key] = value

        # Ereignisse (max. 10, neueste zuerst)
        events: list[dict] = data.get("events", [])
        if events:
            attrs["events"] = [
                {
                    "time": self._format_datetime(e.get("timestamp", "")),
                    "description": e.get("description", ""),
                    "location": (
                        (e.get("location") or {})
                        .get("address", {})
                        .get("addressLocality", "")
                    ),
                }
                for e in events[:10]
            ]
            attrs["event_count"] = len(events)

        return attrs

    # ── Device Info ──────────────────────────────────────────────────────────

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, self._config_entry.entry_id)},
            "name": "DHL Sendungsverfolgung",
            "manufacturer": "DHL Group",
            "model": "Parcel DE Tracking API" if self._config_entry.data.get("api_type") == "parcel_de" else "Shipment Tracking – Unified API",
            "configuration_url": "https://developer.dhl.com",
        }

    # ── Hilfsmethoden ────────────────────────────────────────────────────────

    @property
    def _shipment_data(self) -> dict[str, Any] | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._tracking_number)

    @staticmethod
    def _format_datetime(raw: str) -> str:
        try:
            dt = dt_util.parse_datetime(raw)
            if dt:
                return dt_util.as_local(dt).strftime("%d.%m.%Y %H:%M")
        except Exception:  # noqa: BLE001
            pass
        return raw

    @staticmethod
    def _error_state(error_code: str) -> str:
        mapping = {
            "rate_limit":          "API-Limit erreicht",
            "http_401":            "Ungültiger API-Key",
            "http_403":            "Zugriff verweigert",
            "http_500":            "DHL Server-Fehler",
            "format_not_supported":"Format nicht unterstuetzt (JJD/Express)",
            "token_expired":       "Token abgelaufen",
            "parse_error":         "XML-Fehler",
        }
        return mapping.get(error_code, f"Fehler ({error_code})")


class DhlArchiveSensor(SensorEntity):
    """Sensor fuer das DHL-Sendungsarchiv – stellt Daten fuer die Lovelace-Karte bereit."""

    def __init__(self, config_entry, archive: DhlArchiveStore) -> None:
        self._entry   = config_entry
        self._archive = archive
        self._attr_name      = "DHL Archiv"
        self._attr_unique_id = f"dhl_{config_entry.entry_id}_archive"
        self._attr_icon      = "mdi:archive"

    @property
    def state(self) -> int:
        return len(self._archive.get_all())

    @property
    def extra_state_attributes(self) -> dict:
        from .const import CONF_ARCHIVE_DAYS, CONF_REMINDER_ENABLED, DEFAULT_ARCHIVE_DAYS
        days     = self._entry.options.get(CONF_ARCHIVE_DAYS, DEFAULT_ARCHIVE_DAYS)
        archived = self._archive.get_all()
        pending  = self._archive.get_pending(days)
        return {
            "archived_items":   archived,
            "pending_deletion": list(pending.keys()),
            "pending_count":    len(pending),
            "archive_days":     days,
            "reminder_enabled": self._entry.options.get(CONF_REMINDER_ENABLED, True),
        }

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "DHL Sendungsverfolgung",
            "manufacturer": "DHL Group",
            "model": "Parcel DE Tracking API",
        }
