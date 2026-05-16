"""DHL Tracking DataUpdateCoordinator – unterstützt Unified API und Parcel DE Tracking."""
from __future__ import annotations

import logging
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_TIMEOUT,
    API_TYPE_PARCEL_DE,
    API_TYPE_UNIFIED,
    DOMAIN,
    PARCEL_DE_SANDBOX_URL,
    PARCEL_DE_URL,
    UNIFIED_API_SANDBOX_URL,
    UNIFIED_API_URL,
)

_LOGGER = logging.getLogger(__name__)


class DhlTrackingCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Koordiniert alle DHL-API-Abfragen für eine Config-Entry-Instanz."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_key: str,
        api_type: str,
        tracking_numbers: list[str],
        scan_interval: int,
        sandbox: bool = False,
    ) -> None:
        self.api_key = api_key
        self.api_type = api_type
        self.tracking_numbers = tracking_numbers
        self.sandbox = sandbox

        if api_type == API_TYPE_PARCEL_DE:
            self._base_url = PARCEL_DE_SANDBOX_URL if sandbox else PARCEL_DE_URL
        else:
            self._base_url = UNIFIED_API_SANDBOX_URL if sandbox else UNIFIED_API_URL

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Holt Daten für alle gespeicherten Sendungsnummern."""
        if not self.tracking_numbers:
            return {}

        data: dict[str, Any] = {}
        async with aiohttp.ClientSession() as session:
            for number in self.tracking_numbers:
                try:
                    if self.api_type == API_TYPE_PARCEL_DE:
                        data[number] = await self._fetch_parcel_de(session, number)
                    else:
                        data[number] = await self._fetch_unified(session, number)
                except UpdateFailed:
                    raise
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Unerwarteter Fehler bei %s: %s", number, err)
                    data[number] = {"_error": str(err)}
        return data

    # ── Shipment Tracking – Unified (JSON) ──────────────────────────────────

    async def _fetch_unified(
        self,
        session: aiohttp.ClientSession,
        tracking_number: str,
    ) -> dict[str, Any]:
        """Unified API – JSON-basiert, Header: DHL-API-Key."""
        url = f"{self._base_url}?trackingNumber={tracking_number}"
        headers = {"DHL-API-Key": self.api_key, "Accept": "application/json"}

        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)
            ) as resp:
                if resp.status == 401:
                    raise UpdateFailed("Ungültiger DHL-API-Schlüssel.")
                if resp.status == 429:
                    _LOGGER.warning("DHL API Limit erreicht. Intervall erhöhen.")
                    return {"_error": "rate_limit"}
                if resp.status == 404:
                    return {"status": {"status": "not-found", "description": "Sendung nicht gefunden"}, "events": []}
                if resp.status != 200:
                    return {"_error": f"http_{resp.status}"}

                result = await resp.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindungsfehler: {err}") from err

        shipments = result.get("shipments", [])
        if not shipments:
            return {"status": {"status": "not-found", "description": "Keine Sendungsdaten"}, "events": []}
        return shipments[0]

    # ── Parcel DE Tracking (XML) ─────────────────────────────────────────────

    async def _fetch_parcel_de(
        self,
        session: aiohttp.ClientSession,
        tracking_number: str,
    ) -> dict[str, Any]:
        """Parcel DE Tracking – XML-basiert, Header: DHL-API-Key."""
        xml_body = (
            '<data request="get-status-for-public-user">'
            f'<Id value="{tracking_number}" schemaVersion="1.0"/>'
            '</data>'
        )
        url = f"{self._base_url}?xml={urllib.parse.quote(xml_body)}"
        headers = {
            "DHL-API-Key": self.api_key,
            "Accept": "application/xml,text/xml,*/*",
        }

        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)
            ) as resp:
                if resp.status == 401:
                    raise UpdateFailed("Ungültiger DHL-API-Schlüssel.")
                if resp.status == 429:
                    _LOGGER.warning("DHL API Limit erreicht. Intervall erhöhen.")
                    return {"_error": "rate_limit"}
                if resp.status == 404:
                    return {"status": {"status": "not-found", "description": "Sendung nicht gefunden"}, "events": []}
                if resp.status != 200:
                    _LOGGER.error("Parcel DE API HTTP %s für %s", resp.status, tracking_number)
                    return {"_error": f"http_{resp.status}"}

                content = await resp.text()
                _LOGGER.debug("Parcel DE Tracking Antwort: %s", content[:500])

        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindungsfehler: {err}") from err

        return self._parse_parcel_de_xml(content, tracking_number)

    def _parse_parcel_de_xml(
        self, xml_content: str, tracking_number: str
    ) -> dict[str, Any]:
        """Parst die XML-Antwort der Parcel DE Tracking API in das interne Format."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as err:
            _LOGGER.error("XML-Parsing fehlgeschlagen: %s | Inhalt: %s", err, xml_content[:300])
            return {"_error": "parse_error"}

        # Namespace-unabhängige Suche
        def find_all(node: ET.Element, tag: str) -> list[ET.Element]:
            return node.findall(f".//{tag}") or node.findall(f".//*[local-name()='{tag}']")

        def find_first(node: ET.Element, tag: str) -> ET.Element | None:
            results = find_all(node, tag)
            return results[0] if results else None

        # Fehlerprüfung
        error_el = find_first(root, "error") or find_first(root, "Error")
        if error_el is not None:
            error_msg = error_el.get("message") or error_el.text or "Unbekannter Fehler"
            _LOGGER.warning("DHL Parcel DE Fehler für %s: %s", tracking_number, error_msg)
            if "not found" in error_msg.lower() or "nicht gefunden" in error_msg.lower():
                return {"status": {"status": "not-found", "description": "Sendung nicht gefunden"}, "events": []}
            return {"_error": error_msg}

        # Ereignisse suchen
        events: list[dict[str, Any]] = []
        event_elements = (
            find_all(root, "data-set")
            or find_all(root, "AuftraggeberData")
            or find_all(root, "event")
        )

        for evt in event_elements:
            attrib = evt.attrib
            event_entry: dict[str, Any] = {
                "description": (
                    attrib.get("event-status")
                    or attrib.get("status")
                    or evt.findtext("description", "")
                    or evt.findtext("status", "")
                    or ""
                ),
                "location": (
                    attrib.get("event-location")
                    or attrib.get("location")
                    or evt.findtext("location", "")
                    or ""
                ),
            }
            # Zeitstempel
            ts = (
                attrib.get("event-timestamp")
                or attrib.get("timestamp")
                or evt.findtext("timestamp", "")
                or ""
            )
            if ts:
                event_entry["timestamp"] = ts.replace(" ", "T")

            events.append(event_entry)

        # Status aus erstem Ereignis ableiten
        first_event = events[0] if events else {}
        raw_status = first_event.get("description", "").lower()

        if "zugestellt" in raw_status or "delivered" in raw_status:
            status_code = "delivered"
        elif "zustellung" in raw_status or "delivery" in raw_status:
            status_code = "out-for-delivery"
        elif "transit" in raw_status or "unterwegs" in raw_status:
            status_code = "transit"
        elif "nicht" in raw_status and "gefunden" in raw_status:
            status_code = "not-found"
        else:
            status_code = "transit"

        status_obj: dict[str, Any] = {
            "status": status_code,
            "description": first_event.get("description", ""),
            "timestamp": first_event.get("timestamp", ""),
        }
        if first_event.get("location"):
            status_obj["location"] = {
                "address": {"addressLocality": first_event["location"]}
            }

        _LOGGER.debug(
            "Parcel DE: %s Ereignisse für %s, Status: %s",
            len(events),
            tracking_number,
            status_code,
        )

        return {"status": status_obj, "events": events}
