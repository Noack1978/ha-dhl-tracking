# DHL Sendungsverfolgung für Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Offizielle DHL API-Integration für Home Assistant. Verfolge Pakete direkt in HA – mit Sensor-Entitäten, Automationen und Dashboard-Karten.

---

## Features

- ✅ **Parcel DE Tracking** (Post & Parcel Germany) – empfohlen für Deutschland
- ✅ **Shipment Tracking – Unified** als Alternative
- 📦 **Mehrere Sendungen** gleichzeitig verfolgen
- 🏷️ Individuelle **Bezeichnungen** pro Sendung
- 🔄 **Automatische Aktualisierung** (konfigurierbares Intervall)
- 🛠️ **Services** zum Hinzufügen/Entfernen von Sendungen (auch per Automation)
- 🌍 UI auf **Deutsch und Englisch**
- 🔒 API-Zugangsdaten werden sicher in HA gespeichert
- 🧪 **Sandbox-Modus** mit offiziellen DHL-Testdaten

---

## Passende Lovelace-Karte

Zur komfortablen Nutzung im Dashboard gibt es eine eigene Karte:
**[DHL Sendungsverfolgung Karte](https://github.com/Noack1978/ha-dhl-tracking-card)**

Die Karte ermöglicht das Hinzufügen und Entfernen von Sendungen direkt im Dashboard und zeigt Status, Ort und Lieferdatum übersichtlich an. Installation ebenfalls über HACS.

---

## Voraussetzungen

### DHL Developer Account & API-Key

1. Registriere dich kostenlos auf [developer.dhl.com](https://developer.dhl.com)
2. Erstelle eine neue App
3. Füge die API **„Parcel DE Tracking (Post & Parcel Germany)"** hinzu
4. **Consumer Key** und **Consumer Secret** aus dem Dashboard kopieren

> **Limit:** 1000 API-Calls pro Tag im Testing-Modus.

### Welche API soll ich nehmen?

| API | Freischaltung | Calls/Tag |
|---|---|---|
| **Parcel DE Tracking** ✅ | Testing sofort, Produktion auf Anfrage | 1000 |
| Shipment Tracking – Unified | Manuelle Prüfung durch DHL | 250 |

---

## Wie die Authentifizierung funktioniert

Die **Parcel DE Tracking API** verwendet **HTTP Basic Auth** – kein OAuth2, kein separater Token-Abruf:

- **HTTP-Header:** `DHL-API-Key: {Consumer Key}` + `Authorization: Basic base64(Consumer Key:Consumer Secret)`
- **XML-Body:** Sandbox-Zugangsdaten (`appname`, `password`) werden automatisch eingebettet
- Im Sandbox-Modus werden die offiziellen DHL-Testdaten automatisch verwendet – keine eigenen Zugangsdaten nötig

---

## Installation

### Via HACS (empfohlen)

1. HACS öffnen → **Integrationen** → Menü (⋮) → **Benutzerdefinierte Repositories**
2. URL eintragen: `https://github.com/Noack1978/ha-dhl-tracking`
3. Kategorie: **Integration** → Hinzufügen
4. Integration suchen: **DHL Sendungsverfolgung** → Herunterladen
5. Home Assistant neu starten

### Manuell

1. Dieses Repository herunterladen
2. Ordner `custom_components/dhl_tracking/` nach `<config>/custom_components/dhl_tracking/` kopieren
3. Home Assistant neu starten

---

## Einrichtung

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen**
2. „DHL Sendungsverfolgung" suchen
3. Felder ausfüllen:

| Feld | Wert |
|---|---|
| API-Schlüssel | Consumer Key von developer.dhl.com |
| API-Secret | Consumer Secret von developer.dhl.com |
| API-Typ | Parcel DE Tracking (empfohlen) |
| GKP-Benutzername | Leer lassen (nur Produktivbetrieb) |
| GKP-Passwort | Leer lassen (nur Produktivbetrieb) |
| Sandbox | ✅ aktivieren (solange Status „Customer Integration Testing") |

4. Speichern → Integration eingerichtet

---

## ⚠️ Sandbox-Modus: Nur Test-Sendungsnummern verwenden!

Im Sandbox-Modus liefert die DHL API **ausschließlich** Daten für diese offiziell bereitgestellten Testnummern. Eigene/echte Sendungsnummern geben „Nicht gefunden" zurück.

| Sandbox-Testnummer |
|---|
| `00340434161094042557` |
| `00340434161094038253` |
| `00340434161094032954` |
| `00340434161094027318` |
| `00340434161094022115` |
| `00340434161094015902` |

→ Eine dieser Nummern beim Hinzufügen einer Sendung eintragen um den Sandbox-Betrieb zu testen.

Sobald die Produktiv-API freigeschaltet ist (Sandbox deaktivieren), können echte Sendungsnummern verwendet werden.

---

## Sendungen hinzufügen

**Option A – UI (Options Flow):**
Einstellungen → Geräte & Dienste → DHL → Konfigurieren → **Sendung hinzufügen**

**Option B – Service:**
```yaml
service: dhl_tracking.add_tracking
data:
  tracking_number: "00340434161094042557"
  label: "Test-Sendung"
  postal_code: "12345"   # optional, für erweiterte Standortdaten
```

---

## Sensor-Entitäten

Pro Sendungsnummer wird **ein Sensor** erstellt:

| Attribut | Beschreibung |
|---|---|
| **State** | Lesbarer Status (z. B. „In Zustellung") |
| `tracking_number` | Sendungsnummer |
| `label` | Bezeichnung |
| `status_code` | Statuscode (z. B. `out-for-delivery`) |
| `current_location` | Aktueller Ort |
| `last_event_time` | Zeitstempel letztes Ereignis |
| `estimated_delivery` | Geschätztes Lieferdatum |
| `events` | Liste der letzten 10 Ereignisse |
| `event_count` | Gesamtanzahl Ereignisse |

### Status-Codes

| Code | Bezeichnung | Icon |
|---|---|---|
| `pre-transit` | Voranmeldung | 📦 |
| `transit` | In Transit | 🚚 |
| `out-for-delivery` | In Zustellung | ⚡ |
| `delivered` | Zugestellt | ✅ |
| `delivery-failure` | Zustellung fehlgeschlagen | ❌ |
| `not-found` | Nicht gefunden | ❓ |
| `exception` | Ausnahme | ⚠️ |

---

## Services

### `dhl_tracking.add_tracking`

| Parameter | Pflicht | Beschreibung |
|---|---|---|
| `tracking_number` | ✅ | DHL-Sendungsnummer |
| `label` | ❌ | Bezeichnung (wird Sensor-Name) |
| `postal_code` | ❌ | Empfänger-PLZ für Standortdaten |
| `entry_id` | ❌ | Nur bei mehreren Instanzen |

### `dhl_tracking.remove_tracking`

| Parameter | Pflicht | Beschreibung |
|---|---|---|
| `tracking_number` | ✅ | Zu entfernende Sendungsnummer |

### `dhl_tracking.refresh`
Erzwingt sofortige Aktualisierung aller Sendungsdaten.

---

## Dashboard-Beispiel

```yaml
type: sections
title: 📦 Pakete
sections:
  - type: grid
    cards:
      - type: markdown
        title: Sendungsverlauf
        content: >
          {% set events = state_attr('sensor.dhl_SENDUNGSNUMMER', 'events') %}
          {% if events %}
            {% for e in events %}
            **{{ e.time }}** – {{ e.location }}
            {{ e.description }}
            ---
            {% endfor %}
          {% else %}
            *Keine Ereignisse*
          {% endif %}
```

---

## Automations-Beispiele

**Benachrichtigung bei Zustellung:**
```yaml
automation:
  - alias: "Paket zugestellt"
    trigger:
      - platform: state
        entity_id: sensor.dhl_SENDUNGSNUMMER
        to: "Zugestellt"
    action:
      - service: notify.mobile_app_mein_handy
        data:
          title: "📦 Paket angekommen!"
          message: >
            {{ state_attr('sensor.dhl_SENDUNGSNUMMER', 'label') }} wurde zugestellt.
```

**Sendung nach Zustellung automatisch entfernen:**
```yaml
automation:
  - alias: "Zugestellte Sendung nach 24h entfernen"
    trigger:
      - platform: state
        entity_id: sensor.dhl_SENDUNGSNUMMER
        to: "Zugestellt"
        for: "24:00:00"
    action:
      - service: dhl_tracking.remove_tracking
        data:
          tracking_number: "{{ state_attr('sensor.dhl_SENDUNGSNUMMER', 'tracking_number') }}"
```

---

## Produktivbetrieb freischalten

Sobald DHL die Produktiv-API freigeschaltet hat (E-Mail-Benachrichtigung):

1. Einstellungen → Geräte & Dienste → DHL → **Löschen**
2. Integration neu einrichten mit **Sandbox deaktiviert** ☐
3. Echte Sendungsnummern hinzufügen

---

## Hinweise & Limits

- **1000 Calls/Tag** im Sandbox-Modus (Customer Integration Testing)
- Bei Standard-Intervall 30 Min und 5 Sendungen: ~240 Calls/Tag
- Intervall anpassbar unter Konfigurieren → Einstellungen (Minimum: 600 s)

---

## Lizenz

MIT – siehe [LICENSE](LICENSE)

---

## E-Mail-Scanner (optional)

Der eingebaute E-Mail-Scanner verbindet sich per IMAP mit deinem Postfach und erkennt DHL-Sendungsnummern automatisch in eingehenden E-Mails.

### Aktivieren

Einstellungen -> Geraete & Dienste -> DHL -> Konfigurieren -> **E-Mail-Scanner**

### Unterstuetzte Anbieter

| Anbieter | Server (automatisch) |
|---|---|
| Gmail | imap.gmail.com |
| GMX | imap.gmx.net |
| web.de | imap.web.de |
| T-Online | secureimap.t-online.de |
| Outlook / Hotmail / Live | outlook.office365.com |
| Yahoo Mail | imap.mail.yahoo.com |
| IONOS (1&1) | imap.ionos.de |
| freenet Mail | mx.freenet.de |
| Benutzerdefiniert | manuell eingeben |

### Hinweise

- **Gmail**: Normales Google-Passwort funktioniert nicht. App-Passwort erstellen unter: Google-Konto -> Sicherheit -> App-Passwoerter
- **GMX / web.de**: IMAP muss im Postfach aktiviert sein (Einstellungen -> E-Mail -> IMAP)
- **T-Online**: Nur mit E-Mail-Adresse (nicht Telekom-Login) und separatem E-Mail-Passwort
- Der Scanner liest ausschliesslich **ungelesene** E-Mails von DHL-Adressen
- Erkannte Sendungsnummern werden automatisch als Sensor hinzugefuegt (Label: "E-Mail Import")
- Standard-Scan-Intervall: 5 Minuten (konfigurierbar)

### Erkannte DHL-Sendungsformate

- 20-stellig beginnend mit `00` (Standard DHL Paket Deutschland)
- `JJD`-Format (DHL Express)
- 10- und 12-stellig (DHL Express Kurznummern)
