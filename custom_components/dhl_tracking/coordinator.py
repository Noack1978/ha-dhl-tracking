"""DHL / DPD Tracking DataUpdateCoordinator."""
from __future__ import annotations

import base64
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
    CARRIER_DHL,
    CARRIER_DPD,
    DHL_WEBSITE_API_URL,
    DOMAIN,
    DPD_WEBSITE_API_URL,
    PARCEL_DE_SANDBOX_URL,
    SANDBOX_APPNAME,
    SANDBOX_PASSWORD,
    UNIFIED_API_SANDBOX_URL,
    UNIFIED_API_URL,
)

_LOGGER = logging.getLogger(__name__)


def detect_carrier(tracking_number: str) -> str:
    """Erkennt den Carrier anhand der Sendungsnummer."""
    num = tracking_number.upper()
    if num.startswith("00") and len(num) == 20:
        return CARRIER_DHL
    if num.startswith("JJD"):
        return CARRIER_DHL
    # DPD: typischerweise 14-stellig, beginnt mit 0
    if num.isdigit() and len(num) == 14 and num.startswith("0"):
        return CARRIER_DPD
    # Standard-Fallback: DHL
    return CARRIER_DHL


class DhlTrackingCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Koordiniert Tracking-Abfragen fuer DHL und DPD."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_key: str,
        api_secret: str,
        api_type: str,
        tracking_numbers: list[str],
        postal_codes: dict[str, str],
        carriers: dict[str, str],
        scan_interval: int,
        sandbox: bool = False,
    ) -> None:
        self.api_key    = api_key
        self.api_secret = api_secret
        self.api_type   = api_type
        self.sandbox    = sandbox
        self.tracking_numbers = tracking_numbers
        self.postal_codes     = postal_codes
        self.carriers         = carriers

        if api_type != API_TYPE_PARCEL_DE:
            self._unified_url = UNIFIED_API_SANDBOX_URL if sandbox else UNIFIED_API_URL

        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    # ── Hauptupdate ──────────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, Any]:
        if not self.tracking_numbers:
            return {}
        data: dict[str, Any] = {}
        async with aiohttp.ClientSession() as session:
            for number in self.tracking_numbers:
                try:
                    if self.api_type != API_TYPE_PARCEL_DE:
                        data[number] = await self._fetch_unified(session, number)
                        continue

                    if self.sandbox:
                        data[number] = await self._fetch_sandbox(session, number,
                                            self.postal_codes.get(number, ""))
                        continue

                    # Produktion: Carrier erkennen und passendes Backend waehlen
                    carrier = self.carriers.get(number) or detect_carrier(number)
                    if carrier == CARRIER_DPD:
                        data[number] = await self._fetch_dpd(session, number)
                    else:
                        data[number] = await self._fetch_dhl_website(session, number)

                except UpdateFailed:
                    raise
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Fehler bei %s: %s", number, err)
                    data[number] = {"_error": str(err)}
        return data

    # ── DHL Website-API ───────────────────────────────────────────────────────

    async def _fetch_dhl_website(
        self, session: aiohttp.ClientSession, tracking_number: str
    ) -> dict[str, Any]:
        url = (f"{DHL_WEBSITE_API_URL}"
               f"?piececode={tracking_number}&language=de&noredirect=true")
        headers = {
            "Accept":          "application/json",
            "User-Agent":      "Mozilla/5.0 (compatible; HomeAssistant)",
            "Accept-Language": "de-DE,de;q=0.9",
        }
        _LOGGER.debug("DHL Website-API: %s", tracking_number)
        try:
            async with session.get(url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as resp:
                if resp.status == 404:
                    return {"status": {"status": "not-found",
                                       "description": "Sendung nicht gefunden"},
                            "events": [], "_carrier": CARRIER_DHL}
                if resp.status != 200:
                    return {"_error": f"http_{resp.status}"}
                result = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"DHL Verbindungsfehler: {err}") from err

        parsed = self._parse_dhl_website(result, tracking_number)
        parsed["_carrier"] = CARRIER_DHL
        return parsed

    def _parse_dhl_website(self, data: dict, tracking_number: str) -> dict[str, Any]:
        sendungen = data.get("sendungen", [])
        if not sendungen:
            return {"status": {"status": "not-found",
                               "description": "Sendung nicht gefunden"}, "events": []}
        s       = sendungen[0]
        details = s.get("sendungsdetails", {})
        verlauf = details.get("sendungsverlauf", {})
        events_raw = verlauf.get("events", [])
        etd = (details.get("voraussichtlicheZustellzeit") or
               details.get("zustelltermin", {}).get("value", ""))

        events: list[dict[str, Any]] = []
        for evt in events_raw:
            entry: dict[str, Any] = {
                "description": evt.get("beschreibung", "") or evt.get("status", ""),
                "location":    evt.get("ort", ""),
            }
            ts = evt.get("datum") or evt.get("zeitstempel", "")
            if ts:
                entry["timestamp"] = str(ts).replace(" ", "T")
            events.append(entry)

        # Neuestes Ereignis zuerst (fuer Status-Bestimmung und Kartenanzeige)
        events = list(reversed(events))
        first    = events[0] if events else {}
        combined = (first.get("description", "") + " " +
                    verlauf.get("aktuellerStatus", "")).lower()
        code = self._status_code(combined)

        status_obj: dict[str, Any] = {
            "status": code,
            "description": first.get("description", ""),
            "timestamp":   first.get("timestamp", ""),
        }
        if first.get("location"):
            status_obj["location"] = {"address": {"addressLocality": first["location"]}}

        result: dict[str, Any] = {"status": status_obj, "events": events}
        if etd:
            result["estimatedTimeOfDelivery"] = str(etd)
        return result

    # ── DPD Website-API ───────────────────────────────────────────────────────

    async def _fetch_dpd(
        self, session: aiohttp.ClientSession, tracking_number: str
    ) -> dict[str, Any]:
        url = f"{DPD_WEBSITE_API_URL}/{tracking_number}"
        headers = {
            "Accept":          "application/json",
            "User-Agent":      "Mozilla/5.0 (compatible; HomeAssistant)",
            "Accept-Language": "de-DE,de;q=0.9",
        }
        _LOGGER.debug("DPD Website-API: %s", tracking_number)
        try:
            async with session.get(url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as resp:
                _LOGGER.debug("DPD HTTP: %s", resp.status)
                if resp.status == 404:
                    return {"status": {"status": "not-found",
                                       "description": "DPD Sendung nicht gefunden"},
                            "events": [], "_carrier": CARRIER_DPD}
                if resp.status != 200:
                    return {"_error": f"http_{resp.status}"}
                result = await resp.json(content_type=None)
                _LOGGER.debug("DPD Antwort: %s", str(result)[:300])
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"DPD Verbindungsfehler: {err}") from err

        parsed = self._parse_dpd(result, tracking_number)
        parsed["_carrier"] = CARRIER_DPD
        return parsed

    def _parse_dpd(self, data: dict, tracking_number: str) -> dict[str, Any]:
        """Parst DPD REST-API Antwort."""
        # DPD gibt parcellifecycleResponse oder aehnlich zurueck
        lifecycle = (data.get("parcellifecycleResponse") or
                     data.get("parcelLifecycleResponse") or data)
        plc_data  = (lifecycle.get("parcelLifeCycleData") or
                     lifecycle.get("parcellifecycledata") or lifecycle)

        scan_info = (plc_data.get("scanInfo") or
                     plc_data.get("scaninfo") or {})
        scans = (scan_info.get("scan") or
                 scan_info.get("Scan") or [])

        # Fallback: scans direkt im Root
        if not scans:
            scans = data.get("scans") or data.get("events") or []

        events: list[dict[str, Any]] = []
        for scan in scans:
            if isinstance(scan, dict):
                date = scan.get("date") or scan.get("scanDate", "")
                time = scan.get("time") or scan.get("scanTime", "")
                ts   = f"{date}T{time}" if date and time else (date or time or "")
                entry: dict[str, Any] = {
                    "description": (scan.get("description") or
                                    scan.get("scanDescription") or
                                    scan.get("label") or ""),
                    "location":    (scan.get("city") or
                                    scan.get("depotCity") or
                                    scan.get("location") or ""),
                }
                if ts:
                    entry["timestamp"] = ts
                events.append(entry)

        # ETD
        etd = (plc_data.get("plannedDeliveryDate") or
               plc_data.get("deliveryDate") or
               data.get("deliveryDate") or "")

        events = list(reversed(events))
        first    = events[0] if events else {}
        combined = (first.get("description", "") + " " +
                    (data.get("status") or "")).lower()
        code = self._status_code(combined)

        _LOGGER.debug("DPD: %d Events, Status=%s, ETD=%s", len(events), code, etd)

        status_obj: dict[str, Any] = {
            "status": code,
            "description": first.get("description", ""),
            "timestamp":   first.get("timestamp", ""),
        }
        if first.get("location"):
            status_obj["location"] = {"address": {"addressLocality": first["location"]}}

        result: dict[str, Any] = {"status": status_obj, "events": events}
        if etd:
            result["estimatedTimeOfDelivery"] = str(etd)
        return result

    # ── Sandbox (DASS-XML) ────────────────────────────────────────────────────

    def _build_basic_auth(self) -> str:
        return "Basic " + base64.b64encode(
            f"{self.api_key}:{self.api_secret}".encode()
        ).decode()

    async def _fetch_sandbox(
        self, session: aiohttp.ClientSession, tracking_number: str, postal_code: str = ""
    ) -> dict[str, Any]:
        attrs = {
            "appname": SANDBOX_APPNAME, "language-code": "de",
            "password": SANDBOX_PASSWORD, "piece-code": tracking_number,
            "request": "d-get-piece-detail",
        }
        if postal_code:
            attrs["zip-code"] = postal_code
        xml_body = (
            '<?xml version="1.0" encoding="UTF-8" standalone="no"?><data '
            + " ".join(f'{k}="{v}"' for k, v in attrs.items()) + "/>"
        )
        url = f"{PARCEL_DE_SANDBOX_URL}?xml={urllib.parse.quote(xml_body)}"
        headers = {
            "DHL-API-Key": self.api_key,
            "Authorization": self._build_basic_auth(),
            "Accept": "application/xml,text/xml,*/*",
        }
        try:
            async with session.get(url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as resp:
                if resp.status == 401:
                    raise UpdateFailed("HTTP 401 - API-Key oder Secret ungueltig.")
                if resp.status == 404:
                    return {"status": {"status": "not-found",
                                       "description": "Sendung nicht gefunden"}, "events": []}
                if resp.status != 200:
                    return {"_error": f"http_{resp.status}"}
                content = await resp.text()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindungsfehler: {err}") from err

        return self._parse_sandbox_xml(content)

    def _parse_sandbox_xml(self, xml_content: str) -> dict[str, Any]:
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            return {"_error": "parse_error"}

        if root.get("code") == "-3":
            return {"_error": "format_not_supported"}

        def find_all(node, tag):
            return node.findall(f".//{tag}") or []

        events: list[dict[str, Any]] = []
        for evt in find_all(root, "data-set"):
            a = evt.attrib
            entry: dict[str, Any] = {
                "description": a.get("event-status", ""),
                "location":    a.get("event-location", ""),
            }
            ts = a.get("event-timestamp", "")
            if ts:
                entry["timestamp"] = ts.replace(" ", "T")
            events.append(entry)

        events = list(reversed(events))
        first = events[0] if events else {}
        code  = self._status_code(first.get("description", "").lower())
        status_obj: dict[str, Any] = {
            "status": code, "description": first.get("description", ""),
            "timestamp": first.get("timestamp", ""),
        }
        if first.get("location"):
            status_obj["location"] = {"address": {"addressLocality": first["location"]}}
        return {"status": status_obj, "events": events}

    # ── Unified API ───────────────────────────────────────────────────────────

    async def _fetch_unified(
        self, session: aiohttp.ClientSession, tracking_number: str
    ) -> dict[str, Any]:
        url = f"{self._unified_url}?trackingNumber={tracking_number}"
        try:
            async with session.get(url,
                headers={"DHL-API-Key": self.api_key, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                if resp.status == 401:
                    raise UpdateFailed("Ungueltiger API-Key.")
                if resp.status == 429:
                    return {"_error": "rate_limit"}
                if resp.status == 404:
                    return {"status": {"status": "not-found",
                                       "description": "Sendung nicht gefunden"}, "events": []}
                if resp.status != 200:
                    return {"_error": f"http_{resp.status}"}
                result = await resp.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindungsfehler: {err}") from err

        shipments = result.get("shipments", [])
        if not shipments:
            return {"status": {"status": "not-found",
                               "description": "Keine Sendungsdaten"}, "events": []}
        return shipments[0]

    # ── Status-Hilfsfunktion ──────────────────────────────────────────────────

    @staticmethod
    def _status_code(text: str) -> str:
        if any(x in text for x in (
            "zugestellt", "delivered", "abgeliefert",
            "packstation", "abholstation", "paketshop",
            "zur abholung bereit", "ready for pickup",
        )):
            return "delivered"
        if any(x in text for x in (
            "in zustellung", "in delivery", "wird zugestellt",
            "zustellfahrzeug", "unterwegs zum empfaenger",
            "auf dem weg zur packstation",
        )):
            return "out-for-delivery"
        if any(x in text for x in (
            "transit", "unterwegs", "region", "angekommen",
            "weitergeleitet", "sortiert", "depot", "hub",
            "zustellbasis", "bearbeitet",
        )):
            return "transit"
        return "transit"
