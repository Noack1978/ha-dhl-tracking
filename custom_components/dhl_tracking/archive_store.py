"""DHL Archiv – persistente Speicherung archivierter Sendungen."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)
STORAGE_KEY     = "dhl_tracking.archive"
STORAGE_VERSION = 1


class DhlArchiveStore:
    """Verwaltet archivierte Sendungen im HA-Speicher."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, Any] = {"archived": {}, "last_reminder": ""}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data:
            self._data = data

    async def async_archive(self, number: str, label: str, status: str, events: list) -> None:
        self._data["archived"][number] = {
            "tracking_number": number,
            "label":           label or number,
            "status":          status,
            "events":          events[:5],
            "archived_at":     datetime.now().isoformat(),
        }
        await self._store.async_save(self._data)
        _LOGGER.info("Sendung %s archiviert.", number)

    async def async_purge(self, numbers: list[str]) -> None:
        for n in numbers:
            self._data["archived"].pop(n, None)
        await self._store.async_save(self._data)
        _LOGGER.info("Archiv bereinigt: %s", numbers)

    async def async_set_reminded(self) -> None:
        self._data["last_reminder"] = datetime.now().date().isoformat()
        await self._store.async_save(self._data)

    def get_all(self) -> dict[str, Any]:
        return dict(self._data.get("archived", {}))

    def get_pending(self, days: int) -> dict[str, Any]:
        if days <= 0:
            return {}
        cutoff = datetime.now() - timedelta(days=days)
        result = {}
        for num, item in self._data.get("archived", {}).items():
            try:
                if datetime.fromisoformat(item["archived_at"]) < cutoff:
                    result[num] = item
            except (KeyError, ValueError):
                pass
        return result

    def reminded_today(self) -> bool:
        return self._data.get("last_reminder", "") == datetime.now().date().isoformat()
