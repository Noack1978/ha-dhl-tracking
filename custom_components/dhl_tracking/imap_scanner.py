"""IMAP-Scanner: Erkennt DHL- und DPD-Sendungsnummern aus E-Mails."""
from __future__ import annotations

import base64
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
    CARRIER_DHL,
    CARRIER_DPD,
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
    DPD_URL_PATTERN,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

# DHL: piececode= in URL (zuverlaessigste Methode)
_DHL_PIECECODE_RE = re.compile(r"piececode=([A-Z0-9]{5,30})", re.IGNORECASE)

# Absendernamen aus DHL-E-Mail-Betreff extrahieren
# Muster: "Ihre Sendung von ABSENDER", "Sendung von ABSENDER ist", "bei ABSENDER"
_SENDER_PATTERNS = [
    re.compile(r'(?:sendung|paket|bestellung)\s+(?:von|bei)\s+([\w\-.]+\.(?:com|de|net|org|eu|shop|io|at|ch|fr|es|co))', re.IGNORECASE),
    re.compile(r'(?:bestellung\s+bei|versand\s+(?:von|durch)|shipped\s+by)\s+([\w\-.]+\.(?:com|de|net|org|eu|shop|io|at|ch))', re.IGNORECASE),
    re.compile(r'([\w\-.]+\.(?:com|de|net|org|eu|shop|io))\s+(?:hat|has|verschickt|versendet)', re.IGNORECASE),
]

# DHL: Sendungsnummer per Regex (Fallback wenn kein URL-Link)
_DHL_NUMBER_RE = re.compile(
    r"\b(?:" + "|".join(DHL_TRACKING_PATTERNS) + r")\b", re.IGNORECASE
)

# DPD: Sendungsnummer aus Tracking-URL (alle E-Mails, nicht nur DPD-Absender!)
# Beispiel: https://tracking.dpd.de/status/de_DE/parcel/05025034752023
_DPD_URL_RE = re.compile(DPD_URL_PATTERN, re.IGNORECASE)

_MAX_AUTH_FAILURES = 3


def _decode_header(raw: str) -> str:
    parts = email.header.decode_header(raw or "")
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return " ".join(result)


def _get_body(msg: email.message.Message) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    parts.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            parts.append(payload.decode(charset, errors="replace"))
    return "\n".join(parts)


def _extract_sender_from_subject(subject: str) -> str:
    """Versucht den Absendernamen aus dem E-Mail-Betreff zu extrahieren."""
    for pattern in _SENDER_PATTERNS:
        m = pattern.search(subject)
        if m:
            name = m.group(1).strip().rstrip(".,")
            if len(name) > 3:
                return name
    return ""


def _is_dhl_sender(from_addr: str) -> bool:
    addr = from_addr.lower()
    return any(s in addr for s in DHL_SENDERS)


def _extract_all(text: str) -> list[dict[str, str]]:
    """Extrahiert alle Sendungsnummern mit Carrier-Zuordnung.

    Strategie:
    1. DPD-URLs (tracking.dpd.de) aus ALLEN E-Mails
    2. DHL piececode= URLs aus ALLEN E-Mails
    3. DHL-Regex nur wenn die E-Mail von DHL kommt (vermeidet false positives)
    """
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    # DPD via URL – funktioniert auch bei Haendler-E-Mails
    for m in _DPD_URL_RE.finditer(text):
        num = m.group(1).upper()
        if num not in seen:
            seen.add(num)
            found.append({"number": num, "carrier": CARRIER_DPD})
            _LOGGER.debug("IMAP: DPD-Nummer aus URL: %s", num)

    # DHL via piececode= URL
    for m in _DHL_PIECECODE_RE.finditer(text):
        num = m.group(1).upper()
        if num not in seen:
            seen.add(num)
            found.append({"number": num, "carrier": CARRIER_DHL})
            _LOGGER.debug("IMAP: DHL-Nummer aus URL: %s", num)

    return found


def _extract_dhl_regex(text: str) -> list[dict[str, str]]:
    """DHL-Regex-Fallback – nur fuer verifizierte DHL-Absender verwenden."""
    found = []
    seen: set[str] = set()
    for m in _DHL_NUMBER_RE.finditer(text):
        num = m.group(0).upper()
        if num not in seen:
            seen.add(num)
            found.append({"number": num, "carrier": CARRIER_DHL})
    return found


def _imap_login(mail: imaplib.IMAP4, username: str, password: str) -> None:
    try:
        mail.login(username, password)
        return
    except imaplib.IMAP4.error as login_err:
        _LOGGER.debug("LOGIN fehlgeschlagen (%s), versuche AUTHENTICATE PLAIN.", login_err)

    auth_string = f"\x00{username}\x00{password}"
    encoded = base64.b64encode(auth_string.encode()).decode()
    try:
        mail.authenticate("PLAIN", lambda _: encoded)
    except imaplib.IMAP4.error as auth_err:
        raise imaplib.IMAP4.error(
            f"Anmeldung fehlgeschlagen. App-Passwort verwenden. Details: {auth_err}"
        ) from auth_err


class DhlImapScanner:
    """Scannt IMAP-Postfach nach DHL- und DPD-Sendungsnummern."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        self._hass   = hass
        self._entry  = config_entry
        self._unsub  = None
        self._running       = False
        self._auth_failures = 0
        self._paused        = False

        opts = config_entry.options
        self._server   = opts.get(CONF_IMAP_SERVER, "")
        self._port     = int(opts.get(CONF_IMAP_PORT, 993))
        self._ssl      = opts.get(CONF_IMAP_SSL, True)
        self._username = opts.get(CONF_IMAP_USERNAME, "")
        self._password = opts.get(CONF_IMAP_PASSWORD, "")
        # Mehrere Ordner: kommagetrennt speichern, z. B. "INBOX, dhl"
        raw_folder = opts.get(CONF_IMAP_FOLDER, DEFAULT_IMAP_FOLDER) or DEFAULT_IMAP_FOLDER
        self._folders = [f.strip() for f in raw_folder.split(",") if f.strip()]
        self._interval = int(opts.get(CONF_IMAP_SCAN_INTERVAL, DEFAULT_IMAP_SCAN_INTERVAL))

    async def async_start(self) -> None:
        _LOGGER.info("IMAP-Scanner gestartet: %s@%s alle %ds",
                     self._username, self._server, self._interval)
        await self._async_scan()
        self._unsub = async_track_time_interval(
            self._hass, self._async_scan, timedelta(seconds=self._interval)
        )

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def _async_scan(self, _now=None) -> None:
        if self._running:
            return
        if self._paused:
            _LOGGER.warning(
                "IMAP-Scanner pausiert nach %d Auth-Fehlern. "
                "App-Passwort in Integrationseinstellungen pruefen.",
                _MAX_AUTH_FAILURES,
            )
            return

        self._running = True
        try:
            found = await self._hass.async_add_executor_job(self._scan_sync)
            self._auth_failures = 0
            for item in found:
                number = item["number"]
                label  = item.get("label", "E-Mail Import")
                _LOGGER.info("IMAP: Sendung erkannt: %s (Label: %s)", number, label)
                await self._hass.services.async_call(
                    DOMAIN, "add_tracking",
                    {"tracking_number": number, "label": label},
                    blocking=False,
                )
        except RuntimeError as err:
            self._auth_failures += 1
            _LOGGER.error("IMAP Auth-Fehler (%d/%d): %s",
                          self._auth_failures, _MAX_AUTH_FAILURES, err)
            if self._auth_failures >= _MAX_AUTH_FAILURES:
                self._paused = True
                _LOGGER.error(
                    "IMAP-Scanner pausiert. Bitte App-Passwort pruefen und "
                    "E-Mail-Scanner in der Integration neu konfigurieren."
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("IMAP Unerwarteter Fehler: %s", err)
        finally:
            self._running = False

    def _scan_sync(self) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        try:
            mail = (imaplib.IMAP4_SSL(self._server, self._port)
                    if self._ssl else imaplib.IMAP4(self._server, self._port))
            _imap_login(mail, self._username, self._password)
            # Alle konfigurierten Ordner scannen
            for folder in self._folders:
                try:
                    status, _ = mail.select(folder)
                    if status != "OK":
                        _LOGGER.warning("IMAP: Ordner '%s' nicht gefunden.", folder)
                        continue

                    _, data = mail.search(None, "(UNSEEN)")
                    if not data or not data[0]:
                        _LOGGER.debug("IMAP: Keine ungelesenen E-Mails in '%s'.", folder)
                        continue

                    msg_ids = data[0].split()
                    _LOGGER.debug("IMAP: %d ungelesene E-Mails in '%s'.",
                                  len(msg_ids), folder)

                    for msg_id in msg_ids:
                        try:
                            _, msg_data = mail.fetch(msg_id, "(RFC822)")
                            msg = email.message_from_bytes(msg_data[0][1])

                            from_addr = _decode_header(msg.get("From", ""))
                            subject   = _decode_header(msg.get("Subject", ""))
                            body      = _get_body(msg)
                            full_text = f"{subject}\n{body}"

                            sender = _extract_sender_from_subject(subject)

                            # 1. piececode= aus DHL-URL (alle E-Mails)
                            for num in _extract_dhl_from_url(full_text):
                                if num not in seen:
                                    seen.add(num)
                                    found.append({"number": num, "label": sender or "E-Mail Import"})
                                    _LOGGER.debug("IMAP [%s]: URL-Treffer: %s (Label: %s)", folder, num, sender or "E-Mail Import")

                            # 2. Regex – nur bei verifizierten DHL-Absendern
                            if _is_dhl_sender(from_addr):
                                for num in _extract_dhl_regex(full_text):
                                    if num not in seen:
                                        seen.add(num)
                                        found.append({"number": num, "label": sender or "E-Mail Import"})
                                        _LOGGER.debug("IMAP [%s]: Regex-Treffer: %s (Label: %s)", folder, num, sender or "E-Mail Import")

                        except Exception as msg_err:  # noqa: BLE001
                            _LOGGER.warning("IMAP [%s]: Fehler bei E-Mail: %s",
                                            folder, msg_err)

                except Exception as folder_err:  # noqa: BLE001
                    _LOGGER.warning("IMAP: Fehler bei Ordner '%s': %s", folder, folder_err)

            mail.logout()

        except imaplib.IMAP4.error as imap_err:
            raise RuntimeError(
                f"Anmeldung fehlgeschlagen. App-Passwort verwenden. Details: {imap_err}"
            ) from imap_err

        # Duplikate entfernen
        seen_nums: set[str] = set()
        unique = []
        for item in found:
            if isinstance(item, dict):
                num = item["number"]
            else:
                num = item
                item = {"number": num, "label": "E-Mail Import"}
            if num not in seen_nums:
                seen_nums.add(num)
                unique.append(item)
        return unique
