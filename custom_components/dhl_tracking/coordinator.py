"""DHL Tracking DataUpdateCoordinator."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import API_BASE_URL, API_SANDBOX_URL, API_TIMEOUT, DOMAIN

_LOGGER = logging.getLogger(__name__)


class DhlTrackingCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Koordiniert alle DHL-API-Abfragen für eine Config-Entry-Instanz."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_key: str,
        tracking_numbers: list[str],
        scan_interval: int,
        sandbox: bool = False,
    ) -> None:
        self.api_key = api_key
        self.tracking_numbers = tracking_numbers
        self.sandbox = sandbox
        self._base_url = API_SANDBOX_URL if sandbox else API_BASE_URL

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
                    data[number] = await self._fetch_shipment(session, number)
                except UpdateFailed:
                    raise
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Unerwarteter Fehler bei %s: %s", number, err)
                    data[number] = {"_error": str(err)}
        return data

    async def _fetch_shipment(
        self,
        session: aiohttp.ClientSession,
        tracking_number: str,
    ) -> dict[str, Any]:
        """Ruft Sendungsdaten für eine einzelne Sendungsnummer ab."""
        url = f"{self._base_url}?trackingNumber={tracking_number}"
        headers = {
            "DHL-API-Key": self.api_key,
            "Accept": "application/json",
        }

        try:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
            ) as resp:
                if resp.status == 401:
                    raise UpdateFailed(
                        "Ungültiger DHL-API-Schlüssel – bitte in der Integration prüfen."
                    )
                if resp.status == 429:
                    _LOGGER.warning(
                        "DHL API Limit erreicht (250 Calls/Tag). "
                        "Aktualisierungsintervall erhöhen."
                    )
                    return {"_error": "rate_limit"}
                if resp.status == 404:
                    return {
                        "status": {
                            "status": "not-found",
                            "description": "Sendung nicht gefunden",
                        },
                        "events": [],
                    }
                if resp.status != 200:
                    _LOGGER.error(
                        "DHL API HTTP %s für Sendung %s", resp.status, tracking_number
                    )
                    return {"_error": f"http_{resp.status}"}

                result = await resp.json()

        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Verbindung zur DHL API fehlgeschlagen: {err}") from err

        shipments = result.get("shipments", [])
        if not shipments:
            return {
                "status": {
                    "status": "not-found",
                    "description": "Keine Sendungsdaten",
                },
                "events": [],
            }

        return shipments[0]
