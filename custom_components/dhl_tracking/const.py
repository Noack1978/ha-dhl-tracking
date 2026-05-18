"""Constants for the DHL Sendungsverfolgung integration."""

DOMAIN = "dhl_tracking"

# ── Basis-Konfiguration ──────────────────────────────────────────────────────
CONF_API_KEY        = "api_key"
CONF_API_SECRET     = "api_secret"
CONF_API_TYPE       = "api_type"
CONF_GKP_USER       = "gkp_user"
CONF_GKP_PASSWORD   = "gkp_password"
CONF_SANDBOX        = "sandbox"
CONF_TRACKING_NUMBERS = "tracking_numbers"
CONF_UPDATE_INTERVAL  = "update_interval"
CONF_LABELS           = "labels"
CONF_POSTAL_CODES     = "postal_codes"

# ── IMAP E-Mail-Scanner ──────────────────────────────────────────────────────
CONF_IMAP_ENABLED       = "imap_enabled"
CONF_IMAP_PROVIDER      = "imap_provider"
CONF_IMAP_SERVER        = "imap_server"
CONF_IMAP_PORT          = "imap_port"
CONF_IMAP_SSL           = "imap_ssl"
CONF_IMAP_USERNAME      = "imap_username"
CONF_IMAP_PASSWORD      = "imap_password"
CONF_IMAP_FOLDER        = "imap_folder"
CONF_IMAP_SCAN_INTERVAL = "imap_scan_interval"

DEFAULT_IMAP_PORT          = 993
DEFAULT_IMAP_FOLDER        = "INBOX"
DEFAULT_IMAP_SCAN_INTERVAL = 300  # 5 Minuten

# Bekannte IMAP-Provider (Label → (Server, Port))
IMAP_PROVIDERS: dict[str, tuple[str, int]] = {
    "gmail":    ("imap.gmail.com",             993),
    "gmx":      ("imap.gmx.net",               993),
    "web_de":   ("imap.web.de",                993),
    "t_online": ("secureimap.t-online.de",     993),
    "outlook":  ("outlook.office365.com",      993),
    "yahoo":    ("imap.mail.yahoo.com",        993),
    "ionos":    ("imap.ionos.de",              993),
    "freenet":  ("mx.freenet.de",              993),
    "custom":   ("",                           993),
}

IMAP_PROVIDER_LABELS: dict[str, str] = {
    "gmail":    "Gmail (Google)",
    "gmx":      "GMX",
    "web_de":   "web.de",
    "t_online": "T-Online",
    "outlook":  "Outlook / Hotmail / Live",
    "yahoo":    "Yahoo Mail",
    "ionos":    "IONOS (1&1)",
    "freenet":  "freenet Mail",
    "custom":   "Benutzerdefiniert",
}

# DHL-Absenderdomains
DHL_SENDERS = [
    "@dhl.de",
    "@dhl.com",
    "@deutschepost.de",
    "@post.de",
    "@paket.dhl.de",
    "@noreply.dhl.de",
    "@dhl-news.com",
]

# Sendungsnummer-Regex-Muster
# Nur sehr spezifische Formate um Fehlerkennungen zu vermeiden:
# - 20-stellig beginnend mit 00 (Standard DHL Paket DE, z. B. 00340434161094042557)
# - JJD + alphanumerisch (DHL Express)
# NICHT verwendet: \d{10} / \d{12} – zu allgemein, treffen Telefon-/Bestellnummern
DHL_TRACKING_PATTERNS = [
    r"00\d{18}",          # Standard DHL Paket DE (20-stellig, beginnt mit 00)
    r"JJD[A-Z0-9]{15,}",  # DHL Express JJD-Format
]

# ── API-Typen ────────────────────────────────────────────────────────────────
API_TYPE_UNIFIED   = "unified"
API_TYPE_PARCEL_DE = "parcel_de"

# Shipment Tracking - Unified
UNIFIED_API_URL         = "https://api.dhl.com/track/shipments"
UNIFIED_API_SANDBOX_URL = "https://api-sandbox.dhl.com/track/shipments"

# Parcel DE Tracking - HTTP Basic Auth + XML
PARCEL_DE_URL         = "https://api-eu.dhl.com/parcel/de/tracking/v0/shipments"
PARCEL_DE_SANDBOX_URL = "https://api-sandbox.dhl.com/parcel/de/tracking/v0/shipments"

# Sandbox-Testdaten (offiziell von DHL)
SANDBOX_APPNAME  = "zt12345"
SANDBOX_PASSWORD = "geheim"

SANDBOX_TRACKING_NUMBERS = [
    "00340434161094042557",
    "00340434161094038253",
    "00340434161094032954",
    "00340434161094027318",
    "00340434161094022115",
    "00340434161094015902",
]

API_TIMEOUT = 15

DEFAULT_SCAN_INTERVAL = 1800
MIN_SCAN_INTERVAL     = 600

PLATFORMS = ["sensor"]

STATUS_DESCRIPTIONS: dict[str, str] = {
    "pre-transit":      "Voranmeldung",
    "transit":          "In Transit",
    "out-for-delivery": "In Zustellung",
    "delivered":        "Zugestellt",
    "delivery-failure": "Zustellung fehlgeschlagen",
    "not-found":        "Nicht gefunden",
    "exception":        "Ausnahme",
    "pickup-failure":   "Abholung fehlgeschlagen",
    "expired":          "Abgelaufen",
}

STATUS_ICONS: dict[str, str] = {
    "pre-transit":      "mdi:package-variant",
    "transit":          "mdi:truck-delivery",
    "out-for-delivery": "mdi:truck-fast",
    "delivered":        "mdi:package-variant-closed-check",
    "delivery-failure": "mdi:package-variant-remove",
    "not-found":        "mdi:help-circle-outline",
    "exception":        "mdi:alert-circle-outline",
    "pickup-failure":   "mdi:alert-outline",
    "expired":          "mdi:package-variant-minus",
}

DEFAULT_ICON = "mdi:package-variant-closed"
