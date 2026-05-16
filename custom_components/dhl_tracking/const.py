"""Constants for the DHL Sendungsverfolgung integration."""

DOMAIN = "dhl_tracking"

# Config keys
CONF_API_KEY = "api_key"
CONF_SANDBOX = "sandbox"
CONF_TRACKING_NUMBERS = "tracking_numbers"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_LABELS = "labels"

# API
API_BASE_URL = "https://api.dhl.com/track/shipments"
API_SANDBOX_URL = "https://api-sandbox.dhl.com/track/shipments"
API_TIMEOUT = 15

# Defaults
DEFAULT_SCAN_INTERVAL = 1800  # 30 Minuten – sicher bei 250 Calls/Tag
MIN_SCAN_INTERVAL = 600       # 10 Minuten Minimum

PLATFORMS = ["sensor"]

# Status → lesbare Bezeichnung (Deutsch)
STATUS_DESCRIPTIONS: dict[str, str] = {
    "pre-transit":        "Voranmeldung",
    "transit":            "In Transit",
    "out-for-delivery":   "In Zustellung",
    "delivered":          "Zugestellt",
    "delivery-failure":   "Zustellung fehlgeschlagen",
    "not-found":          "Nicht gefunden",
    "exception":          "Ausnahme",
    "pickup-failure":     "Abholung fehlgeschlagen",
    "expired":            "Abgelaufen",
}

# Status → MDI-Icon
STATUS_ICONS: dict[str, str] = {
    "pre-transit":        "mdi:package-variant",
    "transit":            "mdi:truck-delivery",
    "out-for-delivery":   "mdi:truck-fast",
    "delivered":          "mdi:package-variant-closed-check",
    "delivery-failure":   "mdi:package-variant-remove",
    "not-found":          "mdi:help-circle-outline",
    "exception":          "mdi:alert-circle-outline",
    "pickup-failure":     "mdi:alert-outline",
    "expired":            "mdi:package-variant-minus",
}

DEFAULT_ICON = "mdi:package-variant-closed"
