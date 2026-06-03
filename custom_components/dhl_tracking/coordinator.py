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

    # ── Parcel DE Tracking (XML + HTTP Basic Auth) ────────────────────────────

    def _build_basic_auth(self) -> str:
        token = base64.b64encode(
            f"{self.api_key}:{self.api_secret}".encode()
        ).decode()
        return f"Basic {token}"

    def _build_parcel_de_xml(self, tracking_number: str, postal_code: str = "") -> str:
        # Sandbox: d-get-piece-detail  |  Produktion: get-status-for-public-user
        request_type = "d-get-piece-detail" if self.sandbox else "get-status-for-public-user"
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
        xml_body = self._build_parcel_de_xml(tracking_number, postal_code)
        url = f"{self._tracking_url}?xml={urllib.parse.quote(xml_body)}"
        headers = {
            "DHL-API-Key":   self.api_key,
            "Authorization": self._build_basic_auth(),
            "Accept":        "application/xml,text/xml,*/*",
        }
        _LOGGER.debug("Parcel DE Request: %s", xml_body)
        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                _LOGGER.debug("Parcel DE HTTP: %s", resp.status)
                if resp.status == 401:
                    raise UpdateFailed("HTTP 401 - API-Key oder Secret ungueltig.")
                if resp.status == 404:
                    return {"status": {"status": "not-found",
                                       "description": "Sendung nicht gefunden"}, "events": []}
                if resp.status != 200:
                    return {"_error": f"http_{resp.status}"}
                content = await resp.text()
                _LOGGER.debug("Parcel DE XML-Antwort: %s", content[:800])
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindungsfehler: {err}") from err

        return self._parse_parcel_de_xml(content, tracking_number)

    def _parse_parcel_de_xml(
        self, xml_content: str, tracking_number: str
    ) -> dict[str, Any]:
        """Parst DHL XML-Antwort – unterstuetzt beide API-Formate:
        - get-status-for-public-user: <data name="..." status-code="INTRAN"><data-set .../></data>
        - d-get-piece-detail:         <piece-status>, <piece-event> Elemente
        """
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as err:
            _LOGGER.error("XML-Parsing fehlgeschlagen fuer %s: %s | %s",
                          tracking_number, err, xml_content[:300])
            return {"_error": "parse_error"}

        def find_all(node: ET.Element, tag: str) -> list[ET.Element]:
            return node.findall(f".//{tag}") or []

        def find_first(node: ET.Element, tag: str) -> ET.Element | None:
            r = find_all(node, tag)
            return r[0] if r else None

        # ── Fehlercheck ───────────────────────────────────────────────────────
        for err_tag in ("error", "Error", "Fault"):
            err_el = find_first(root, err_tag)
            if err_el is not None:
                msg = err_el.get("message") or err_el.text or "Unbekannter Fehler"
                _LOGGER.warning("DHL Fehler fuer %s: %s", tracking_number, msg)
                if "not found" in msg.lower() or "nicht gefunden" in msg.lower():
                    return {"status": {"status": "not-found",
                                       "description": "Sendung nicht gefunden"}, "events": []}
                return {"_error": msg[:100]}

        # ── Format 1: get-status-for-public-user ─────────────────────────────
        # Aeusseres <data> enthaelt Status-Attribute, innere <data-set> sind Events
        data_el = None
        for el in find_all(root, "data") + [root]:
            if el.get("status-code") or el.get("status") or el.get("name"):
                data_el = el
                break

        status_code_raw = ""
        status_desc     = ""
        etd_raw         = ""
        status_ts       = ""
        dest_country    = ""

        if data_el is not None:
            status_code_raw = data_el.get("status-code", "")
            status_desc     = data_el.get("status-description", "")
            etd_raw         = (data_el.get("estimated-time-of-delivery", "") or
                               data_el.get("eta", ""))
            status_ts       = data_el.get("status-timestamp", "")
            dest_country    = data_el.get("dest-country", "")

        # ── Format 2: d-get-piece-detail ─────────────────────────────────────
        # <piece-status> und <piece-status-desc> Textelemente
        piece_status      = root.findtext(".//piece-status", "")
        piece_status_desc = root.findtext(".//piece-status-desc", "")
        if not status_code_raw:
            status_code_raw = piece_status
        if not status_desc:
            status_desc = piece_status_desc

        # ── Events sammeln ────────────────────────────────────────────────────
        events: list[dict[str, Any]] = []

        # Format 1: <data-set> Attribute
        search_root = data_el if data_el is not None else root
        for evt in find_all(search_root, "data-set"):
            a = evt.attrib
            entry: dict[str, Any] = {
                "description": (a.get("event-status") or
                                a.get("event-short-status") or ""),
                "location":    a.get("event-location", ""),
            }
            ts = a.get("event-timestamp", "")
            if ts:
                entry["timestamp"] = ts.replace(" ", "T")
            if entry["description"] or ts:
                events.append(entry)

        # Format 2: <piece-event> Kindelemente
        if not events:
            for evt in find_all(root, "piece-event"):
                entry = {
                    "description": evt.findtext("event-status", ""),
                    "location":    evt.findtext("event-location", ""),
                }
                ts = evt.findtext("event-timestamp", "")
                if ts:
                    entry["timestamp"] = ts.replace(" ", "T")
                if entry["description"] or ts:
                    events.append(entry)

        _LOGGER.debug(
            "Parcel DE geparst: %d Events, status_code=%r, desc=%r, etd=%r",
            len(events), status_code_raw, status_desc[:60] if status_desc else "",
            etd_raw,
        )

        # ── Status-Code ableiten ──────────────────────────────────────────────
        first = events[0] if events else {}
        raw   = (status_desc or first.get("description", "") or status_code_raw).lower()
        rule  = status_code_raw.upper()

        if rule in ("DLVRD", "DLVRD-NG") or any(x in raw for x in ("zugestellt", "delivered")):
            code = "delivered"
        elif rule in ("LDTCA", "OUTFOR") or any(x in raw for x in
                ("in zustellung", "zustellbasis", "in delivery", "out for delivery")):
            code = "out-for-delivery"
        elif rule in ("INTRAN", "PCKD", "TRANSIT") or any(x in raw for x in
                ("transit", "unterwegs", "region des empfaengers", "region des empfängers",
                 "region", "angekommen", "bearbeitet")):
            code = "transit"
        elif "nicht" in raw and "gefunden" in raw:
            code = "not-found"
        else:
            code = "transit"

        # ── Status-Objekt ─────────────────────────────────────────────────────
        ts_clean = status_ts.replace(" ", "T") if status_ts else first.get("timestamp", "")
        status_obj: dict[str, Any] = {
            "status":      code,
            "description": status_desc or first.get("description", ""),
            "timestamp":   ts_clean,
        }
        first_loc = first.get("location", "")
        if first_loc:
            status_obj["location"] = {"address": {"addressLocality": first_loc}}
        if dest_country:
            status_obj["countryCode"] = dest_country

        result: dict[str, Any] = {"status": status_obj, "events": events}

        # Estimated Delivery fuer sensor.py
        if etd_raw:
            result["estimatedTimeOfDelivery"] = etd_raw.replace(" ", "T")

        return result

    # ── Shipment Tracking – Unified (JSON) ───────────────────────────────────

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
                    raise UpdateFailed("Ungueltiger DHL-API-Schluessel.")
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
