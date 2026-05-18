"""IMAP-Scanner: Erkennt DHL-Sendungsnummern automatisch in eingehenden E-Mails."""
from __future__ import annotations

import email
import email.header
import imaplib
import logging
import re
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_IMAP_FOLDER,
    CONF_IMAP_PASSWORD,
    CONF_IMAP_PORT,
    CONF_IMAP_SERVER,
    CONF_IMAP_SSL,
    CONF_IMAP_USERNAME,
    CONF_IMAP_SCAN_INTERVAL,
    DEFAULT_IMAP_FOLDER,
    DEFAULT_IMAP_SCAN_INTERVAL,
    DHL_SENDERS,
    DHL_TRACKING_PATTERNS,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

# Sendungsnummern-Regex fuer DHL Deutschland
# - 20-stellig beginnend mit 00 (Standard DHL Paket)
# - JJD + alphanumerisch (DHL Express)
# - 10-12-stellig rein numerisch (DHL Express Kurznummer)
_TRACKING_RE = re.compile(
    r"\b(?:" + "|".join(DHL_TRACKING_PATTERNS) + r")\b",
    re.IGNORECASE,
)


def _decode_header(raw: str) -> str:
    """Dekodiert Email-Header (z. B. =?utf-8?...?)."""
    parts = email.header.decode_header(raw or "")
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return " ".join(result)


def _get_body(msg: email.message.Message) -> str:
    """Extrahiert den Textinhalt einer E-Mail (plain text bevorzugt)."""
    body_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body_parts.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body_parts.append(payload.decode(charset, errors="replace"))
    return "\n".join(body_parts)


def _is_dhl_sender(from_addr: str) -> bool:
    """Prueft ob die Absenderadresse zu DHL gehoert."""
    addr = from_addr.lower()
    return any(sender in addr for sender in DHL_SENDERS)


def _extract_tracking_numbers(text: str) -> set[str]:
    """Findet alle DHL-Sendungsnummern in einem Text."""
    return {m.upper() for m in _TRACKING_RE.findall(text)}


class DhlImapScanner:
    """Verbindet sich per IMAP mit dem E-Mail-Postfach und sucht DHL-Sendungsnummern."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ) -> None:
        self._hass    = hass
        self._entry   = config_entry
        self._unsub   = None
        self._running = False

        opts = config_entry.options
        self._server   = opts.get(CONF_IMAP_SERVER, "")
        self._port     = int(opts.get(CONF_IMAP_PORT, 993))
        self._ssl      = opts.get(CONF_IMAP_SSL, True)
        self._username = opts.get(CONF_IMAP_USERNAME, "")
        self._password = opts.get(CONF_IMAP_PASSWORD, "")
        self._folder   = opts.get(CONF_IMAP_FOLDER, DEFAULT_IMAP_FOLDER)
        self._interval = int(opts.get(CONF_IMAP_SCAN_INTERVAL, DEFAULT_IMAP_SCAN_INTERVAL))

    async def async_start(self) -> None:
        """Startet den periodischen IMAP-Scan."""
        _LOGGER.info(
            "DHL IMAP-Scanner gestartet: %s@%s alle %ds",
            self._username, self._server, self._interval,
        )
        # Direkt beim Start einmal scannen
        await self._async_scan()
        # Danach periodisch
        self._unsub = async_track_time_interval(
            self._hass,
            self._async_scan,
            timedelta(seconds=self._interval),
        )

    async def async_stop(self) -> None:
        """Stoppt den Scanner."""
        if self._unsub:
            self._unsub()
            self._unsub = None
        _LOGGER.info("DHL IMAP-Scanner gestoppt.")

    async def _async_scan(self, _now=None) -> None:
        """Fuehrt den IMAP-Scan im Executor-Thread aus (nicht-blockierend)."""
        if self._running:
            return
        self._running = True
        try:
            found = await self._hass.async_add_executor_job(self._scan_sync)
            for number in found:
                _LOGGER.info("DHL IMAP: Neue Sendungsnummer erkannt: %s", number)
                await self._hass.services.async_call(
                    DOMAIN,
                    "add_tracking",
                    {
                        "tracking_number": number,
                        "label": "E-Mail Import",
                    },
                    blocking=False,
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("DHL IMAP-Scan fehlgeschlagen: %s", err)
        finally:
            self._running = False

    def _scan_sync(self) -> set[str]:
        """Synchroner IMAP-Scan – laeuft im Thread-Pool."""
        found_numbers: set[str] = set()

        try:
            if self._ssl:
                mail = imaplib.IMAP4_SSL(self._server, self._port)
            else:
                mail = imaplib.IMAP4(self._server, self._port)

            mail.login(self._username, self._password)
            mail.select(self._folder)

            # Ungelesene E-Mails von DHL-Adressen suchen
            search_criteria = '(UNSEEN)'
            _, data = mail.search(None, search_criteria)

            if not data or not data[0]:
                mail.logout()
                return found_numbers

            msg_ids = data[0].split()
            _LOGGER.debug("DHL IMAP: %d ungelesene E-Mails gefunden.", len(msg_ids))

            for msg_id in msg_ids:
                try:
                    _, msg_data = mail.fetch(msg_id, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    from_addr = _decode_header(msg.get("From", ""))
                    subject   = _decode_header(msg.get("Subject", ""))

                    # Nur DHL-Absender verarbeiten
                    if not _is_dhl_sender(from_addr):
                        _LOGGER.debug("IMAP: Kein DHL-Absender (%s) – uebersprungen.", from_addr)
                        continue

                    _LOGGER.debug(
                        "IMAP: DHL-E-Mail gefunden: Von=%s | Betreff=%s",
                        from_addr, subject,
                    )

                    # Betreff + Body durchsuchen
                    body = _get_body(msg)
                    full_text = f"{subject}\n{body}"
                    numbers = _extract_tracking_numbers(full_text)

                    if numbers:
                        _LOGGER.info(
                            "IMAP: Sendungsnummern in E-Mail gefunden: %s", numbers
                        )
                        found_numbers.update(numbers)

                except Exception as msg_err:  # noqa: BLE001
                    _LOGGER.warning("IMAP: Fehler beim Verarbeiten einer E-Mail: %s", msg_err)

            mail.logout()

        except imaplib.IMAP4.error as imap_err:
            raise RuntimeError(f"IMAP-Fehler: {imap_err}") from imap_err

        return found_numbers
