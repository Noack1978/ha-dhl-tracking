"""DHL Tracking DataUpdateCoordinator."""
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
    DHL_WEBSITE_API_URL,
    DOMAIN,
    PARCEL_DE_SANDBOX_URL,
    SANDBOX_APPNAME,
    SANDBOX_PASSWORD,
    UNIFIED_API_SANDBOX_URL,
    UNIFIED_API_URL,
)

_LOGGER = logging.getLogger(__name__)


class DhlTrackingCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Koordiniert alle DHL-API-Abfragen."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_key: str,
        api_secret: str,
        api_type: str,
        tracking_numbers: list[str],
        postal_codes: dict[str, str],
        scan_interval: int,
        sandbox: bool = False,
    ) -> None:
        self.api_key    = api_key
        self.api_secret = api_secret
        self.api_type   = api_type
        self.sandbox    = sandbox
        self.tracking_numbers = tracking_numbers
        self.postal_codes     = postal_codes

        if api_type == API_TYPE_PARCEL_DE:
            self._sandbox_url = PARCEL_DE_SANDBOX_URL
        else:
            self._tracking_url = UNIFIED_API_SANDBOX_URL if sandbox else UNIFIED_API_URL

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
                    if self.api_type == API_TYPE_PARCEL_DE:
                        if self.sandbox:
                            plz = self.postal_codes.get(number, "")
                            data[number] = await self._fetch_sandbox(session, number, plz)
                        else:
                            data[number] = await self._fetch_website(session, number)
                    else:
                        data[number] = await self._fetch_unified(session, number)
                except UpdateFailed:
                    raise
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Fehler bei %s: %s", number, err)
                    data[number] = {"_error": str(err)}
        return data

    # ── DHL Website-API (Produktion) ──────────────────────────────────────────

    async def _fetch_website(
        self,
        session: aiohttp.ClientSession,
        tracking_number: str,
    ) -> dict[str, Any]:
        """DHL-Website-Backend – kein API-Key noetig, alle Nummernformate."""
        url = f"{DHL_WEBSITE_API_URL}?piececode={tracking_number}&language=de&noredirect=true"
        headers = {
            "Accept":          "application/json",
            "User-Agent":      "Mozilla/5.0 (compatible; HomeAssistant)",
            "Accept-Language": "de-DE,de;q=0.9",
        }
        _LOGGER.debug("DHL Website-API: %s", tracking_number)
        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                _LOGGER.debug("DHL Website-API HTTP: %s", resp.status)
                if resp.status == 404:
                    return {"status": {"status": "not-found",
                                       "description": "Sendung nicht gefunden"}, "events": []}
                if resp.status != 200:
                    return {"_error": f"http_{resp.status}"}
                result = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindungsfehler: {err}") from err

        return self._parse_website(result, tracking_number)

    def _parse_website(self, data: dict, tracking_number: str) -> dict[str, Any]:
        """Parst JSON-Antwort des DHL-Website-Backends."""
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

        first    = events[0] if events else {}
        combined = (first.get("description", "") + " " +
                    verlauf.get("aktuellerStatus", "") + " " +
                    s.get("status", "")).lower()

        if any(x in combined for x in ("zugestellt", "delivered")):
            code = "delivered"
        elif any(x in combined for x in ("zustellung", "in delivery", "wird zugestellt")):
            code = "out-for-delivery"
        elif any(x in combined for x in ("transit", "unterwegs", "region",
                                          "angekommen", "weitergeleitet", "sortiert")):
            code = "transit"
        else:
            code = "transit"

        _LOGGER.debug("DHL Website: %d Events, Status=%s, ETD=%s", len(events), code, etd)

        status_obj: dict[str, Any] = {
            "status":      code,
            "description": first.get("description", ""),
            "timestamp":   first.get("timestamp", ""),
        }
        if first.get("location"):
            status_obj["location"] = {"address": {"addressLocality": first["location"]}}

        result: dict[str, Any] = {"status": status_obj, "events": events}
        if etd:
            result["estimatedTimeOfDelivery"] = str(etd)
        return result

    # ── Parcel DE DASS-API (Sandbox) ──────────────────────────────────────────

    def _build_basic_auth(self) -> str:
        return "Basic " + base64.b64encode(
            f"{self.api_key}:{self.api_secret}".encode()
        ).decode()

    async def _fetch_sandbox(
        self,
        session: aiohttp.ClientSession,
        tracking_number: str,
        postal_code: str = "",
    ) -> dict[str, Any]:
        """DASS-XML-API mit Sandbox-Credentials – nur fuer Sandbox-Modus."""
        attrs = {
            "appname":       SANDBOX_APPNAME,
            "language-code": "de",
            "password":      SANDBOX_PASSWORD,
            "piece-code":    tracking_number,
            "request":       "d-get-piece-detail",
        }
        if postal_code:
            attrs["zip-code"] = postal_code
        xml_body = (
            '<?xml version="1.0" encoding="UTF-8" standalone="no"?><data '
            + " ".join(f'{k}="{v}"' for k, v in attrs.items())
            + "/>"
        )
        url = f"{self._sandbox_url}?xml={urllib.parse.quote(xml_body)}"
        headers = {
            "DHL-API-Key":   self.api_key,
            "Authorization": self._build_basic_auth(),
            "Accept":        "application/xml,text/xml,*/*",
        }
        _LOGGER.debug("Sandbox Request: %s", xml_body.replace(SANDBOX_PASSWORD, "***"))
        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                if resp.status == 401:
                    raise UpdateFailed("HTTP 401 - API-Key oder Secret ungueltig.")
                if resp.status == 404:
                    return {"status": {"status": "not-found",
                                       "description": "Sendung nicht gefunden"}, "events": []}
                if resp.status != 200:
                    return {"_error": f"http_{resp.status}"}
                content = await resp.text()
                _LOGGER.debug("Sandbox XML: %s", content[:500])
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindungsfehler: {err}") from err

        return self._parse_sandbox_xml(content, tracking_number)

    def _parse_sandbox_xml(self, xml_content: str, tracking_number: str) -> dict[str, Any]:
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as err:
            _LOGGER.error("XML-Fehler fuer %s: %s", tracking_number, err)
            return {"_error": "parse_error"}

        def find_all(node, tag):
            return node.findall(f".//{tag}") or []

        root_code = root.get("code", "")
        root_error = root.get("error", "")
        if root_code == "-3" or "unbekannt" in root_error.lower():
            return {"_error": "format_not_supported"}

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

        if not events:
            for evt in find_all(root, "piece-event"):
                entry = {
                    "description": evt.findtext("event-status", ""),
                    "location":    evt.findtext("event-location", ""),
                }
                ts = evt.findtext("event-timestamp", "")
                if ts:
                    entry["timestamp"] = ts.replace(" ", "T")
                events.append(entry)

        first = events[0] if events else {}
        raw   = first.get("description", "").lower()
        code  = ("delivered"        if any(x in raw for x in ("zugestellt", "delivered")) else
                 "out-for-delivery" if any(x in raw for x in ("zustellung", "in delivery")) else
                 "transit")

        status_obj: dict[str, Any] = {
            "status": code,
            "description": first.get("description", ""),
            "timestamp":   first.get("timestamp", ""),
        }
        if first.get("location"):
            status_obj["location"] = {"address": {"addressLocality": first["location"]}}
        return {"status": status_obj, "events": events}

    # ── Shipment Tracking – Unified (JSON) ───────────────────────────────────

    async def _fetch_unified(self, session: aiohttp.ClientSession, tracking_number: str) -> dict[str, Any]:
        url = f"{self._tracking_url}?trackingNumber={tracking_number}"
        headers = {"DHL-API-Key": self.api_key, "Accept": "application/json"}
        try:
            async with session.get(url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as resp:
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
