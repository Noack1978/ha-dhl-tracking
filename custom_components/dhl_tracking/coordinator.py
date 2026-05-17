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
    SANDBOX_GKP_PASSWORD,
    SANDBOX_GKP_USER,
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
        self.api_key      = api_key
        self.api_secret   = api_secret
        self.api_type     = api_type
        self.sandbox      = sandbox
        self.tracking_numbers = tracking_numbers
        self.postal_codes     = postal_codes

        # Im Sandbox-Modus offizielle DHL-Testdaten verwenden wenn keine eigenen vorhanden
        if api_type == API_TYPE_PARCEL_DE and sandbox:
            self._gkp_user     = gkp_user or SANDBOX_GKP_USER
            self._gkp_password = gkp_password or SANDBOX_GKP_PASSWORD
        else:
            self._gkp_user     = gkp_user
            self._gkp_password = gkp_password

        # OAuth2-Token-Cache
        self._bearer_token: str | None = None
        self._token_expires: datetime  = datetime.min

        if api_type == API_TYPE_PARCEL_DE:
            self._auth_url     = PARCEL_DE_AUTH_SANDBOX_URL if sandbox else PARCEL_DE_AUTH_URL
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
                token = await self._get_bearer_token(session)
                if not token:
                    raise UpdateFailed(
                        "Kein Bearer Token erhalten. "
                        "API-Schlüssel, Secret und GKP-Zugangsdaten prüfen."
                    )
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

    # ── OAuth2 Token – ROPC Password Flow ────────────────────────────────────

    async def _get_bearer_token(self, session: aiohttp.ClientSession) -> str | None:
        """OAuth2 Resource Owner Password Credentials Flow.

        Benötigt:
          - client_id / client_secret  (API-Key & Secret vom Developer Portal)
          - username / password        (GKP-Zugangsdaten von DHL; im Sandbox-Modus
                                        werden automatisch die offiziellen Testdaten genutzt)
        """
        if self._bearer_token and datetime.now() < self._token_expires:
            return self._bearer_token

        _LOGGER.debug(
            "DHL OAuth2: Token anfordern für GKP-User '%s' von %s",
            self._gkp_user, self._auth_url,
        )

        payload = {
            "grant_type":    "password",
            "username":      self._gkp_user,
            "password":      self._gkp_password,
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
                body = await resp.text()
                _LOGGER.debug("OAuth2 Antwort: HTTP %s | %s", resp.status, body[:200])

                if resp.status == 400:
                    _LOGGER.error(
                        "OAuth2 HTTP 400 – Anfrage-Format ungültig oder "
                        "GKP-Zugangsdaten fehlen. Antwort: %s", body[:300]
                    )
                    return None
                if resp.status == 401:
                    _LOGGER.error("OAuth2 HTTP 401 – Ungültige Zugangsdaten.")
                    return None
                if resp.status != 200:
                    _LOGGER.error("OAuth2 HTTP %s | %s", resp.status, body[:200])
                    return None

                result = await resp.json(content_type=None)
                token      = result.get("access_token")
                expires_in = int(result.get("expires_in", 17999))

                self._bearer_token = token
                self._token_expires = datetime.now() + timedelta(seconds=expires_in - 300)
                _LOGGER.debug("OAuth2: Token erhalten, gültig %s Sekunden.", expires_in)
                return token

        except aiohttp.ClientError as err:
            raise UpdateFailed(f"OAuth2 Verbindungsfehler: {err}") from err

    # ── Parcel DE Tracking (XML + Bearer Token) ───────────────────────────────

    async def _fetch_parcel_de(
        self,
        session: aiohttp.ClientSession,
        token: str,
        tracking_number: str,
        postal_code: str = "",
    ) -> dict[str, Any]:
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
            "Accept":        "application/xml,text/xml,*/*",
        }
        _LOGGER.debug("Parcel DE Tracking: %s", url[:120])

        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                _LOGGER.debug("Parcel DE Antwort: HTTP %s", resp.status)
                if resp.status == 401:
                    self._bearer_token = None  # Token erneuern beim nächsten Abruf
                    return {"_error": "token_expired"}
                if resp.status == 404:
                    return {"status": {"status": "not-found", "description": "Sendung nicht gefunden"}, "events": []}
                if resp.status != 200:
                    return {"_error": f"http_{resp.status}"}
                content = await resp.text()
                _LOGGER.debug("Parcel DE XML (500 Z.): %s", content[:500])
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindungsfehler: {err}") from err

        return self._parse_parcel_de_xml(content, tracking_number)

    def _parse_parcel_de_xml(self, xml_content: str, tracking_number: str) -> dict[str, Any]:
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as err:
            _LOGGER.error("XML-Parsing fehlgeschlagen: %s | %s", err, xml_content[:300])
            return {"_error": "parse_error"}

        def find_all(node: ET.Element, tag: str) -> list[ET.Element]:
            return node.findall(f".//{tag}") or []

        def find_first(node: ET.Element, tag: str) -> ET.Element | None:
            r = find_all(node, tag)
            return r[0] if r else None

        error_el = find_first(root, "error") or find_first(root, "Error")
        if error_el is not None:
            msg = error_el.get("message") or error_el.text or "Unbekannter Fehler"
            _LOGGER.warning("DHL Parcel DE Fehler für %s: %s", tracking_number, msg)
            if "not found" in msg.lower():
                return {"status": {"status": "not-found", "description": "Sendung nicht gefunden"}, "events": []}
            return {"_error": msg}

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
        code  = ("delivered" if "zugestellt" in raw or "delivered" in raw
                 else "out-for-delivery" if "zustellung" in raw or "in delivery" in raw
                 else "transit")

        status_obj: dict[str, Any] = {
            "status": code, "description": first.get("description", ""),
            "timestamp": first.get("timestamp", ""),
        }
        if first.get("location"):
            status_obj["location"] = {"address": {"addressLocality": first["location"]}}

        return {"status": status_obj, "events": events}

    # ── Shipment Tracking – Unified (JSON) ───────────────────────────────────

    async def _fetch_unified(self, session: aiohttp.ClientSession, tracking_number: str) -> dict[str, Any]:
        url = f"{self._tracking_url}?trackingNumber={tracking_number}"
        headers = {"DHL-API-Key": self.api_key, "Accept": "application/json"}
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as resp:
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
