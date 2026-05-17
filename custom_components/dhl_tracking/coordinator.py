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
    DOMAIN,
    PARCEL_DE_SANDBOX_URL,
    PARCEL_DE_URL,
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
        gkp_user: str,
        gkp_password: str,
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

        # Im Sandbox-Modus: offizielle DHL-Testdaten (gehen in XML, kein OAuth2)
        # Im Produktivbetrieb: GKP-Zugangsdaten des Benutzers
        if sandbox:
            self._xml_appname  = SANDBOX_APPNAME
            self._xml_password = SANDBOX_PASSWORD
        else:
            self._xml_appname  = gkp_user
            self._xml_password = gkp_password

        if api_type == API_TYPE_PARCEL_DE:
            self._tracking_url = PARCEL_DE_SANDBOX_URL if sandbox else PARCEL_DE_URL
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
                        plz = self.postal_codes.get(number, "")
                        data[number] = await self._fetch_parcel_de(session, number, plz)
                    else:
                        data[number] = await self._fetch_unified(session, number)
                except UpdateFailed:
                    raise
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Fehler bei %s: %s", number, err)
                    data[number] = {"_error": str(err)}
        return data

    # ── Parcel DE Tracking ────────────────────────────────────────────────────
    # Auth: HTTP Basic Auth (base64(api_key:api_secret)) + DHL-API-Key Header
    # Zugangsdaten gehen als Attribute in den XML-Body – KEIN OAuth2!

    def _build_basic_auth(self) -> str:
        """Erstellt den Authorization: Basic Header-Wert."""
        token = base64.b64encode(
            f"{self.api_key}:{self.api_secret}".encode()
        ).decode()
        return f"Basic {token}"

    def _build_parcel_de_xml(
        self, tracking_number: str, postal_code: str = ""
    ) -> str:
        """Baut den XML-Body für die Parcel DE Tracking API.

        Im Sandbox-Modus: request='d-get-piece-detail' (public-user geht im Test nicht).
        Im Produktivbetrieb: request='get-status-for-public-user'.
        """
        request_type = (
            "d-get-piece-detail"
            if self.sandbox
            else "get-status-for-public-user"
        )

        attrs = {
            "appname":       self._xml_appname,
            "language-code": "de",
            "password":      self._xml_password,
            "piece-code":    tracking_number,
            "request":       request_type,
        }
        if postal_code:
            attrs["zip-code"] = postal_code

        attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
        return f'<?xml version="1.0" encoding="UTF-8" standalone="no"?><data {attr_str}/>'

    async def _fetch_parcel_de(
        self,
        session: aiohttp.ClientSession,
        tracking_number: str,
        postal_code: str = "",
    ) -> dict[str, Any]:
        """Parcel DE Tracking – XML-Request, HTTP Basic Auth."""
        xml_body = self._build_parcel_de_xml(tracking_number, postal_code)
        url = f"{self._tracking_url}?xml={urllib.parse.quote(xml_body)}"

        headers = {
            "DHL-API-Key":   self.api_key,
            "Authorization": self._build_basic_auth(),
            "Accept":        "application/xml,text/xml,*/*",
        }

        _LOGGER.debug("Parcel DE Tracking URL: %s", url[:150])
        _LOGGER.debug("Parcel DE XML: %s", xml_body)

        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                _LOGGER.debug("Parcel DE Antwort: HTTP %s", resp.status)
                content = await resp.text()
                _LOGGER.debug("Parcel DE Inhalt: %s", content[:500])

                if resp.status == 401:
                    raise UpdateFailed(
                        "HTTP 401 – API-Schlüssel oder Secret ungültig."
                    )
                if resp.status == 404:
                    return {
                        "status": {"status": "not-found",
                                   "description": "Sendung nicht gefunden"},
                        "events": [],
                    }
                if resp.status != 200:
                    return {"_error": f"http_{resp.status}"}

        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindungsfehler: {err}") from err

        return self._parse_parcel_de_xml(content, tracking_number)

    def _parse_parcel_de_xml(
        self, xml_content: str, tracking_number: str
    ) -> dict[str, Any]:
        """Parst die DASS-XML-Antwort der Parcel DE Tracking API."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as err:
            _LOGGER.error(
                "XML-Parsing fehlgeschlagen für %s: %s | Inhalt: %s",
                tracking_number, err, xml_content[:300],
            )
            return {"_error": "parse_error"}

        def find_all(node: ET.Element, tag: str) -> list[ET.Element]:
            return node.findall(f".//{tag}") or []

        def find_first(node: ET.Element, tag: str) -> ET.Element | None:
            r = find_all(node, tag)
            return r[0] if r else None

        # Fehlercheck
        for error_tag in ("error", "Error", "Fault"):
            err_el = find_first(root, error_tag)
            if err_el is not None:
                msg = (
                    err_el.get("message")
                    or err_el.findtext("message")
                    or err_el.text
                    or "Unbekannter Fehler"
                )
                _LOGGER.warning("DHL Parcel DE Fehler für %s: %s", tracking_number, msg)
                if any(x in msg.lower() for x in ["not found", "nicht gefunden", "unknown"]):
                    return {
                        "status": {"status": "not-found",
                                   "description": "Sendung nicht gefunden"},
                        "events": [],
                    }
                return {"_error": msg[:100]}

        # Gesamtstatus aus piece-status / piece-status-desc
        piece_status      = root.findtext(".//piece-status", "")
        piece_status_desc = root.findtext(".//piece-status-desc", "")

        # Ereignisse aus piece-event Elementen
        events: list[dict[str, Any]] = []
        for evt in find_all(root, "piece-event"):
            entry: dict[str, Any] = {
                "description": evt.findtext("event-status", ""),
                "location":    evt.findtext("event-location", ""),
                "rule_id":     evt.findtext("ruleId", ""),
            }
            ts = evt.findtext("event-timestamp", "")
            if ts:
                entry["timestamp"] = ts.replace(" ", "T")
            events.append(entry)

        # Fallback: data-set Elemente (älteres Format)
        if not events:
            for evt in find_all(root, "data-set"):
                a = evt.attrib
                entry = {
                    "description": a.get("event-status", ""),
                    "location":    a.get("event-location", ""),
                }
                ts = a.get("event-timestamp", "")
                if ts:
                    entry["timestamp"] = ts.replace(" ", "T")
                events.append(entry)

        # Status aus piece-status ableiten
        raw = (piece_status_desc or (events[0].get("description", "") if events else "")).lower()
        rule = piece_status.upper() if piece_status else ""

        if rule in ("DLVRD", "DLVRD-NG") or "zugestellt" in raw or "delivered" in raw:
            code = "delivered"
        elif rule == "LDTCA" or "zustellung" in raw or "in delivery" in raw:
            code = "out-for-delivery"
        elif rule in ("INTRAN", "PCKD") or "transit" in raw or "unterwegs" in raw:
            code = "transit"
        elif "nicht" in raw and "gefunden" in raw:
            code = "not-found"
        else:
            code = "transit"

        first = events[0] if events else {}
        status_obj: dict[str, Any] = {
            "status":      code,
            "description": piece_status_desc or first.get("description", ""),
            "timestamp":   first.get("timestamp", ""),
        }
        if first.get("location"):
            status_obj["location"] = {
                "address": {"addressLocality": first["location"]}
            }

        _LOGGER.debug(
            "Parcel DE: %d Ereignisse für %s, Status: %s",
            len(events), tracking_number, code,
        )
        return {"status": status_obj, "events": events}

    # ── Shipment Tracking – Unified (JSON) ────────────────────────────────────

    async def _fetch_unified(
        self, session: aiohttp.ClientSession, tracking_number: str
    ) -> dict[str, Any]:
        url = f"{self._tracking_url}?trackingNumber={tracking_number}"
        headers = {"DHL-API-Key": self.api_key, "Accept": "application/json"}
        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                if resp.status == 401:
                    raise UpdateFailed("Ungültiger DHL-API-Schlüssel.")
                if resp.status == 429:
                    return {"_error": "rate_limit"}
                if resp.status == 404:
                    return {
                        "status": {"status": "not-found",
                                   "description": "Sendung nicht gefunden"},
                        "events": [],
                    }
                if resp.status != 200:
                    return {"_error": f"http_{resp.status}"}
                result = await resp.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindungsfehler: {err}") from err

        shipments = result.get("shipments", [])
        if not shipments:
            return {
                "status": {"status": "not-found", "description": "Keine Sendungsdaten"},
                "events": [],
            }
        return shipments[0]
