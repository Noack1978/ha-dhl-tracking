"""Constants for the DHL Sendungsverfolgung integration."""

DOMAIN = "dhl_tracking"

# Config keys
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

# API types
API_TYPE_UNIFIED   = "unified"
API_TYPE_PARCEL_DE = "parcel_de"

# Shipment Tracking – Unified (JSON, DHL-API-Key Header)
UNIFIED_API_URL         = "https://api.dhl.com/track/shipments"
UNIFIED_API_SANDBOX_URL = "https://api-sandbox.dhl.com/track/shipments"

# Parcel DE – OAuth2 ROPC Token-Endpunkt
PARCEL_DE_AUTH_URL         = "https://api-eu.dhl.com/parcel/de/account/auth/ropc/v1/token"
PARCEL_DE_AUTH_SANDBOX_URL = "https://api-sandbox.dhl.com/parcel/de/account/auth/ropc/v1/token"

# Parcel DE – Tracking-Endpunkt (XML)
PARCEL_DE_URL         = "https://api-eu.dhl.com/parcel/de/tracking/v0/shipments"
PARCEL_DE_SANDBOX_URL = "https://api-sandbox.dhl.com/parcel/de/tracking/v0/shipments"

# Sandbox-Testdaten (offiziell von DHL bereitgestellt)
SANDBOX_GKP_USER     = "user-valid"
SANDBOX_GKP_PASSWORD = "SandboxPasswort2023!"

API_TIMEOUT = 15

# Defaults
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
