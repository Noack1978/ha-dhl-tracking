"""DHL Tracking DataUpdateCoordinator."""
from __future__ import annotations

import logging
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_TIMEOUT,
    API_TYPE_PARCEL_DE,
    DOMAIN,
    PARCEL_DE_AUTH_SANDBOX_URL,
    PARCEL_DE_AUTH_URL,
    PARCEL_DE_SANDBOX_URL,
    PARCEL_DE_URL,
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
        self.tracking_numbers = tracking_numbers
        self.postal_codes     = postal_codes
        self.sandbox = sandbox

        # OAuth2-Token-Cache
        self._bearer_token: str | None = None
        self._token_expires: datetime  = datetime.min

        if api_type == API_TYPE_PARCEL_DE:
            self._auth_url    = PARCEL_DE_AUTH_SANDBOX_URL if sandbox else PARCEL_DE_AUTH_URL
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
            if self.api_type == API_TYPE_PARCEL_DE:
                # Bearer Token holen/erneuern (gültig 5 Stunden)
                token = await self._get_bearer_token(session)
                if not token:
                    raise UpdateFailed("Kein Bearer Token – API-Schlüssel oder Secret prüfen.")
            else:
                token = None

            for number in self.tracking_numbers:
                try:
                    if self.api_type == API_TYPE_PARCEL_DE:
                        plz = self.postal_codes.get(number, "")
                        data[number] = await self._fetch_parcel_de(session, token, number, plz)
                    else:
                        data[number] = await self._fetch_unified(session, number)
                except UpdateFailed:
                    raise
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Fehler bei %s: %s", number, err)
                    data[number] = {"_error": str(err)}
        return data

    # ── OAuth2 Token (Parcel DE) ─────────────────────────────────────────────

    async def _get_bearer_token(self, session: aiohttp.ClientSession) -> str | None:
        """Holt einen Bearer Token per OAuth2 client_credentials flow."""
        if self._bearer_token and datetime.now() < self._token_expires:
            return self._bearer_token  # Cache verwenden

        _LOGGER.debug("DHL OAuth2: Neuen Bearer Token anfordern von %s", self._auth_url)

        payload = {
            "grant_type":    "client_credentials",
            "client_id":     self.api_key,
            "client_secret": self.api_secret,
        }
        try:
            async with session.post(
                self._auth_url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                _LOGGER.debug("OAuth2 Antwort: HTTP %s", resp.status)
                if resp.status == 401:
                    _LOGGER.error("OAuth2 fehlgeschlagen: Ungültiger API-Key oder Secret.")
                    return None
                if resp.status != 200:
                    _LOGGER.error("OAuth2 HTTP %s", resp.status)
                    return None

                result = await resp.json(content_type=None)
                token = result.get("access_token")
                expires_in = int(result.get("expires_in", 17999))

                self._bearer_token = token
                # 5 Minuten Puffer vor Ablauf
                self._token_expires = datetime.now() + timedelta(seconds=expires_in - 300)
                _LOGGER.debug("OAuth2: Token erhalten, gültig %s Sekunden.", expires_in)
                return token

        except aiohttp.ClientError as err:
            raise UpdateFailed(f"OAuth2 Verbindungsfehler: {err}") from err

    # ── Parcel DE Tracking (XML + Bearer Token) ──────────────────────────────

    async def _fetch_parcel_de(
        self,
        session: aiohttp.ClientSession,
        token: str,
        tracking_number: str,
        postal_code: str = "",
    ) -> dict[str, Any]:
        """Parcel DE Tracking – XML-Request mit Bearer-Auth."""

        xml_parts = [
            '<data request="get-status-for-public-user">',
            f'<Id value="{tracking_number}" schemaVersion="1.0"/>',
        ]
        if postal_code:
            xml_parts.append(f'<zipCode value="{postal_code}"/>')
        xml_parts.append("</data>")
        xml_body = "".join(xml_parts)

        url = f"{self._tracking_url}?xml={urllib.parse.quote(xml_body)}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/xml,text/xml,*/*",
        }

        _LOGGER.debug("Parcel DE Tracking: %s", url)

        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                _LOGGER.debug("Parcel DE Antwort: HTTP %s", resp.status)
                if resp.status == 401:
                    # Token abgelaufen – Cache leeren und beim nächsten Update neu holen
                    self._bearer_token = None
                    return {"_error": "token_expired"}
                if resp.status == 404:
                    return {"status": {"status": "not-found", "description": "Sendung nicht gefunden"}, "events": []}
                if resp.status != 200:
                    return {"_error": f"http_{resp.status}"}

                content = await resp.text()
                _LOGGER.debug("Parcel DE XML (erste 500 Z.): %s", content[:500])

        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindungsfehler: {err}") from err

        return self._parse_parcel_de_xml(content, tracking_number)

    def _parse_parcel_de_xml(self, xml_content: str, tracking_number: str) -> dict[str, Any]:
        """Parst XML-Antwort in das interne Datenformat."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as err:
            _LOGGER.error("XML-Parsing fehlgeschlagen: %s | Inhalt: %s", err, xml_content[:300])
            return {"_error": "parse_error"}

        def find_all(node: ET.Element, tag: str) -> list[ET.Element]:
            return node.findall(f".//{tag}") or []

        def find_first(node: ET.Element, tag: str) -> ET.Element | None:
            r = find_all(node, tag)
            return r[0] if r else None

        # Fehlercheck
        error_el = find_first(root, "error") or find_first(root, "Error")
        if error_el is not None:
            msg = error_el.get("message") or error_el.text or "Unbekannter Fehler"
            _LOGGER.warning("DHL Parcel DE Fehler für %s: %s", tracking_number, msg)
            if "not found" in msg.lower() or "nicht gefunden" in msg.lower():
                return {"status": {"status": "not-found", "description": "Sendung nicht gefunden"}, "events": []}
            return {"_error": msg}

        # Ereignisse
        events: list[dict[str, Any]] = []
        for evt in (find_all(root, "data-set") or find_all(root, "event")):
            a = evt.attrib
            entry: dict[str, Any] = {
                "description": a.get("event-status") or a.get("status") or evt.findtext("description", ""),
                "location":    a.get("event-location") or a.get("location") or evt.findtext("location", ""),
            }
            ts = a.get("event-timestamp") or a.get("timestamp") or evt.findtext("timestamp", "")
            if ts:
                entry["timestamp"] = ts.replace(" ", "T")
            events.append(entry)

        first = events[0] if events else {}
        raw   = first.get("description", "").lower()

        if "zugestellt" in raw or "delivered" in raw:
            code = "delivered"
        elif "zustellung" in raw or "in delivery" in raw:
            code = "out-for-delivery"
        elif "transit" in raw or "unterwegs" in raw:
            code = "transit"
        else:
            code = "transit"

        status_obj: dict[str, Any] = {
            "status":      code,
            "description": first.get("description", ""),
            "timestamp":   first.get("timestamp", ""),
        }
        if first.get("location"):
            status_obj["location"] = {"address": {"addressLocality": first["location"]}}

        return {"status": status_obj, "events": events}

    # ── Shipment Tracking – Unified (JSON) ───────────────────────────────────

    async def _fetch_unified(
        self,
        session: aiohttp.ClientSession,
        tracking_number: str,
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
