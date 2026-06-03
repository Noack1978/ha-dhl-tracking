"""IMAP-Scanner: Erkennt DHL-Sendungsnummern automatisch in eingehenden E-Mails."""
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

_TRACKING_RE = re.compile(
    r"\b(?:" + "|".join(DHL_TRACKING_PATTERNS) + r")\b",
    re.IGNORECASE,
)

# DHL-Tracking-URL – piececode-Parameter ist die sicherste Erkennungsmethode
# Beispiel: https://www.dhl.de/.../verfolgen.html?piececode=00340434287479856042
_PIECECODE_RE = re.compile(
    r"piececode=([A-Z0-9]{5,30})",
    re.IGNORECASE,
)

# Nach dieser Anzahl aufeinanderfolgender Auth-Fehler pausiert der Scanner
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
    body_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
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
    addr = from_addr.lower()
    return any(sender in addr for sender in DHL_SENDERS)


def _extract_tracking_numbers(text: str) -> set[str]:
    """Extrahiert Sendungsnummern – piececode-URL hat Vorrang vor Regex.

    Primaer:  piececode=XXXX aus DHL-Links (100% praezise, keine Fehlerkennungen)
    Fallback: Regex fuer Nummern ohne URL (nur wenn kein piececode gefunden)
    """
    numbers: set[str] = set()

    # 1. piececode= aus DHL-Tracking-URLs extrahieren
    #    z. B. https://www.dhl.de/.../verfolgen.html?piececode=00340434287479856042
    for match in _PIECECODE_RE.finditer(text):
        numbers.add(match.group(1).upper())

    if numbers:
        return numbers  # URL-Treffer sind zuverlaessig – kein Regex noetig

    # 2. Fallback: Regex (nur sehr spezifische Muster, kaum Fehlerkennungen)
    numbers.update(m.upper() for m in _TRACKING_RE.findall(text))
    return numbers


def _imap_login(mail: imaplib.IMAP4, username: str, password: str) -> None:
    """Versucht Login – faellt auf AUTHENTICATE PLAIN zurueck (Yahoo-Kompatibilitaet)."""
    try:
        mail.login(username, password)
        return
    except imaplib.IMAP4.error as login_err:
        _LOGGER.debug("LOGIN fehlgeschlagen (%s), versuche AUTHENTICATE PLAIN.", login_err)

    # AUTHENTICATE PLAIN Fallback (benoetigt von Yahoo und einigen anderen Anbietern)
    auth_string = f"\x00{username}\x00{password}"
    encoded = base64.b64encode(auth_string.encode()).decode()
    try:
        mail.authenticate("PLAIN", lambda _: encoded)
        _LOGGER.debug("AUTHENTICATE PLAIN erfolgreich.")
    except imaplib.IMAP4.error as auth_err:
        raise imaplib.IMAP4.error(
            f"Anmeldung fehlgeschlagen. Bitte App-Passwort verwenden "
            f"(kein normales Konto-Passwort). Details: {auth_err}"
        ) from auth_err


class DhlImapScanner:
    """Verbindet sich per IMAP und sucht DHL-Sendungsnummern."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        self._hass    = hass
        self._entry   = config_entry
        self._unsub   = None
        self._running = False
        self._auth_failures = 0  # Zaehler aufeinanderfolgender Auth-Fehler
        self._paused = False     # Pausiert nach zu vielen Auth-Fehlern

        opts = config_entry.options
        self._server   = opts.get(CONF_IMAP_SERVER, "")
        self._port     = int(opts.get(CONF_IMAP_PORT, 993))
        self._ssl      = opts.get(CONF_IMAP_SSL, True)
        self._username = opts.get(CONF_IMAP_USERNAME, "")
        self._password = opts.get(CONF_IMAP_PASSWORD, "")
        self._folder   = opts.get(CONF_IMAP_FOLDER, DEFAULT_IMAP_FOLDER)
        self._interval = int(opts.get(CONF_IMAP_SCAN_INTERVAL, DEFAULT_IMAP_SCAN_INTERVAL))

    async def async_start(self) -> None:
        _LOGGER.info(
            "DHL IMAP-Scanner gestartet: %s@%s alle %ds",
            self._username, self._server, self._interval,
        )
        await self._async_scan()
        self._unsub = async_track_time_interval(
            self._hass, self._async_scan, timedelta(seconds=self._interval),
        )

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        _LOGGER.info("DHL IMAP-Scanner gestoppt.")

    async def _async_scan(self, _now=None) -> None:
        if self._running:
            return
        if self._paused:
            _LOGGER.warning(
                "DHL IMAP-Scanner pausiert nach %d Auth-Fehlern. "
                "Bitte App-Passwort in den Integrationseinstellungen pruefen "
                "und E-Mail-Scanner neu konfigurieren.",
                _MAX_AUTH_FAILURES,
            )
            return

        self._running = True
        try:
            found = await self._hass.async_add_executor_job(self._scan_sync)
            self._auth_failures = 0  # Erfolg: Zaehler zuruecksetzen
            for number in found:
                _LOGGER.info("DHL IMAP: Neue Sendungsnummer erkannt: %s", number)
                await self._hass.services.async_call(
                    DOMAIN, "add_tracking",
                    {"tracking_number": number, "label": "E-Mail Import"},
                    blocking=False,
                )
        except RuntimeError as err:
            err_str = str(err)
            if "App-Passwort" in err_str or "fehlgeschlagen" in err_str:
                self._auth_failures += 1
                _LOGGER.error(
                    "DHL IMAP Auth-Fehler (%d/%d): %s",
                    self._auth_failures, _MAX_AUTH_FAILURES, err,
                )
                if self._auth_failures >= _MAX_AUTH_FAILURES:
                    self._paused = True
                    _LOGGER.error(
                        "DHL IMAP-Scanner wird pausiert. "
                        "Bitte App-Passwort pruefen: "
                        "Yahoo: myaccount.yahoo.com -> Sicherheit -> App-Passwoerter. "
                        "Danach E-Mail-Scanner in der Integration neu konfigurieren."
                    )
            else:
                _LOGGER.error("DHL IMAP-Scan fehlgeschlagen: %s", err)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("DHL IMAP-Scan unerwarteter Fehler: %s", err)
        finally:
            self._running = False

    def _scan_sync(self) -> set[str]:
        found_numbers: set[str] = set()
        try:
            if self._ssl:
                mail = imaplib.IMAP4_SSL(self._server, self._port)
            else:
                mail = imaplib.IMAP4(self._server, self._port)

            _imap_login(mail, self._username, self._password)
            mail.select(self._folder)

            _, data = mail.search(None, "(UNSEEN)")
            if not data or not data[0]:
                mail.logout()
                return found_numbers

            msg_ids = data[0].split()
            _LOGGER.debug("DHL IMAP: %d ungelesene E-Mails in '%s'.", len(msg_ids), self._folder)

            for msg_id in msg_ids:
                try:
                    _, msg_data = mail.fetch(msg_id, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    from_addr = _decode_header(msg.get("From", ""))
                    subject   = _decode_header(msg.get("Subject", ""))

                    if not _is_dhl_sender(from_addr):
                        _LOGGER.debug("IMAP: Kein DHL-Absender (%s).", from_addr)
                        continue

                    _LOGGER.debug("IMAP: DHL-E-Mail: Von=%s | Betreff=%s", from_addr, subject)

                    body    = _get_body(msg)
                    numbers = _extract_tracking_numbers(f"{subject}\n{body}")
                    if numbers:
                        _LOGGER.info("IMAP: Gefundene Sendungsnummern: %s", numbers)
                        found_numbers.update(numbers)

                except Exception as msg_err:  # noqa: BLE001
                    _LOGGER.warning("IMAP: Fehler bei E-Mail-Verarbeitung: %s", msg_err)

            mail.logout()

        except imaplib.IMAP4.error as imap_err:
            raise RuntimeError(
                f"Anmeldung fehlgeschlagen. Bitte App-Passwort verwenden. Details: {imap_err}"
            ) from imap_err

        return found_numbers
