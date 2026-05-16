# DHL Sendungsverfolgung für Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Offizielle DHL API-Integration für Home Assistant. Verfolge Pakete direkt in HA – mit Sensor-Entitäten, Automationen und Dashboard-Karten.

---

## Features

- ✅ **Parcel DE Tracking** (Post & Parcel Germany) – empfohlen für Privatpersonen in Deutschland
- ✅ **Shipment Tracking – Unified** als Alternative (erfordert separate Freischaltung durch DHL)
- 📦 **Mehrere Sendungen** gleichzeitig verfolgen
- 🏷️ Individuelle **Bezeichnungen** pro Sendung (z. B. „Amazon Mai")
- 🔄 **Automatische Aktualisierung** (konfigurierbares Intervall)
- 🛠️ **Services** zum Hinzufügen/Entfernen von Sendungen (auch per Automation)
- 🌍 UI auf **Deutsch und Englisch**
- 🔒 API-Key wird sicher in HA gespeichert
- 🧪 **Sandbox-Modus** für die Test-Umgebung (Customer Integration Testing)

---

## Voraussetzungen

### DHL Developer Account & API-Key

1. Registriere dich kostenlos auf [developer.dhl.com](https://developer.dhl.com)
2. Erstelle eine neue App
3. Füge die API **„Parcel DE Tracking (Post & Parcel Germany)"** hinzu
4. Den **API-Schlüssel (Consumer Key)** aus dem Dashboard kopieren

> **Hinweis:** Die API ist für Privatpersonen in Deutschland direkt verfügbar und wird sofort freigeschaltet (kein manueller Genehmigungsprozess notwendig).

> **Limit:** 1000 API-Calls pro Tag im Testing-Modus. Bei Standardintervall von 30 Minuten und 5 Sendungen werden ~240 Calls/Tag benötigt.

### Welche API soll ich nehmen?

| API | Zielgruppe | Freischaltung | Calls/Tag |
|---|---|---|---|
| **Parcel DE Tracking** ✅ | Privatpersonen (DE) | Sofort | 1000 |
| Shipment Tracking – Unified | Alle DHL-Sparten | Manuell durch DHL | 250 |

→ **Empfehlung: Parcel DE Tracking** – sofort verfügbar, ausreichend für den Heimgebrauch.

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
3. API-Schlüssel eingeben
4. API-Typ wählen: **Parcel DE Tracking (Post & Parcel Germany)** – empfohlen
5. Sandbox aktivieren, wenn dein API-Status noch „Customer Integration Testing" ist
6. Speichern – die Integration ist eingerichtet

### Sendungen hinzufügen

**Option A – UI (Options Flow):**
Einstellungen → Geräte & Dienste → DHL → Konfigurieren → **Sendung hinzufügen**

**Option B – Service:**
```yaml
service: dhl_tracking.add_tracking
data:
  tracking_number: "1234567890"
  label: "Amazon Bestellung"
```

---

## Sensor-Entitäten

Pro Sendungsnummer wird **ein Sensor** erstellt:

| Eigenschaft | Beschreibung |
|---|---|
| **State** | Lesbarer Status (z. B. „In Zustellung") |
| `tracking_number` | Sendungsnummer |
| `label` | Bezeichnung |
| `status_code` | API-Statuscode (z. B. `out-for-delivery`) |
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
| `expired` | Abgelaufen | 🕐 |

---

## Services

### `dhl_tracking.add_tracking`
Fügt eine Sendung hinzu und erstellt einen Sensor.

| Parameter | Pflicht | Beschreibung |
|---|---|---|
| `tracking_number` | ✅ | DHL-Sendungsnummer |
| `label` | ❌ | Bezeichnung (wird Sensor-Name) |
| `entry_id` | ❌ | Nur bei mehreren Instanzen nötig |

### `dhl_tracking.remove_tracking`
Entfernt eine Sendung und löscht den Sensor.

| Parameter | Pflicht | Beschreibung |
|---|---|---|
| `tracking_number` | ✅ | DHL-Sendungsnummer |
| `entry_id` | ❌ | Nur bei mehreren Instanzen nötig |

### `dhl_tracking.refresh`
Erzwingt sofortige Aktualisierung (ignoriert Intervall).

| Parameter | Pflicht | Beschreibung |
|---|---|---|
| `entry_id` | ❌ | Nur bei mehreren Instanzen nötig |

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

## Hinweise & Limits

- **1000 Calls/Tag** im Parcel DE Testing-Modus. Bei Standard-Intervall (30 Min):
  - 1 Sendung → ~48 Calls/Tag
  - 5 Sendungen → ~240 Calls/Tag
- Sandbox-Modus aktivieren, solange der API-Status „Customer Integration Testing" ist
- API-Key wird **lokal in HA** gespeichert, nie übertragen

---

## Lizenz

MIT – siehe [LICENSE](LICENSE)
