# DHL Sendungsverfolgung fuer Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Offizielle DHL-Integration fuer Home Assistant. Verfolge Pakete direkt in HA.

## Features

- Sendungsverfolgung ueber DHL-Website-API (kein spezieller API-Zugang noetig)
- Unterstuetzt alle DHL-Sendungsnummernformate (00-Prefix, JJD, Express)
- Mehrere Sendungen gleichzeitig verfolgen
- Individuelle Bezeichnungen pro Sendung (manuell oder automatisch aus E-Mail-Betreff)
- Automatische Aktualisierung (konfigurierbares Intervall)
- E-Mail-Scanner: erkennt Sendungsnummern automatisch aus DHL-Mails (mehrere Ordner)
- Sendungsarchiv fuer zugestellte Pakete mit Erinnerungsfunktion
- Event `dhl_tracking_status_changed` bei Statuswechsel → einfache Automationen
- Services zum Hinzufuegen/Entfernen/Umbenennen/Archivieren von Sendungen
- UI auf Deutsch und Englisch
- Passende Lovelace-Karte: https://github.com/Noack1978/ha-dhl-tracking-card

## Voraussetzungen

Fuer den **Produktivbetrieb mit Parcel DE Tracking** wird kein
DHL Developer Account benoetigt.

### DHL Developer Account fuer andere API (optional, fuer Sandbox/Unified API)

1. Registrieren auf [developer.dhl.com](https://developer.dhl.com)
2. Neue App erstellen
3. Unified API oder andere zum Testen hinzufuegen
4. Consumer Key kopieren

## Im Produktivbetrieb wird die DHL-Website-API verwendet

Keine GKP-Credentials, kein Developer-Account oder spezielle Freischaltung noetig.

## Installation via HACS

1. HACS -> Integrationen -> Menue -> Benutzerdefinierte Repositories
2. URL: `https://github.com/Noack1978/ha-dhl-tracking`
3. Kategorie: Integration -> Hinzufuegen
4. "DHL Sendungsverfolgung" suchen -> Herunterladen
5. HA neu starten

## Einrichtung

1. Einstellungen -> Geraete & Dienste -> Integration hinzufuegen
2. "DHL Sendungsverfolgung" suchen
3. API-Key optional eintragen (nur fuer Sandbox / Unified API benoetigt)
4. API-Typ: Parcel DE Tracking (empfohlen)
5. Sandbox deaktiviert lassen (fuer echten Betrieb)

## Automationen mit dhl_tracking_status_changed

Die Integration feuert automatisch ein Event wenn eine Sendung den Status wechselt.
Damit keine Polling-Automationen noetig:

```yaml
alias: DHL Sendungsbenachrichtigung
triggers:
  - trigger: event
    event_type: dhl_tracking_status_changed
conditions: []
actions:
  - action: notify.mobile_app_mein_handy
    data:
      title: DHL
      message: >
        {% if trigger.event.data.status == 'out-for-delivery' %}
          {{ trigger.event.data.label }} trifft heute ein.
        {% else %}
          {{ trigger.event.data.label }} wurde zugestellt.
        {% endif %}
mode: parallel
max: 10
```

Event-Daten: `tracking_number`, `label`, `status` (`out-for-delivery` / `delivered`), `description`

## Sendungsarchiv

Zugestellte Sendungen koennen archiviert werden:

- In der Lovelace-Karte auf das Archiv-Symbol bei einer zugestellten Sendung tippen
- Die Sendung wandert ins Archiv und verschwindet aus der aktiven Liste
- Nach der konfigurierten Aufbewahrungsdauer wird sie zur Loeschung vorgeschlagen

**Einstellungen** (Konfigurieren -> Einstellungen):
- Aufbewahrungsdauer in Tagen (Standard: 30)
- Taeglich erinnern wenn Loeschung ausstehend (an/aus)
- Benachrichtigungsdienst z. B. `notify.mobile_app_mein_handy`

## Sandbox-Modus (nur fuer Tests)

Fuer Tests mit offiziellen DHL-Testnummern:
- Sandbox aktivieren
- API-Key UND API-Secret eintragen
- Testnummern: `00340434161094042557`, `00340434161094038253` usw.

## E-Mail-Scanner

Automatische Erkennung von Sendungsnummern aus DHL-E-Mails.
Einrichten unter: Konfigurieren -> E-Mail-Scanner

Mehrere Ordner moeglich: kommagetrennt eingeben, z. B. `INBOX, dhl`

Unterstuetzte Anbieter: Gmail, GMX, web.de, T-Online, Outlook, Yahoo, IONOS, freenet

Hinweis: Bei 2-Faktor-Authentifizierung ein App-Passwort verwenden.

## Sensor-Attribute

Pro Sendung wird ein Sensor erstellt mit:
- Status (In Transit, In Zustellung, Zugestellt usw.)
- Aktueller Ort
- Geschaetztes Lieferdatum
- Ereignisverlauf (neueste Ereignisse zuerst)
- Sendungsnummer und Bezeichnung
- Absendername (wird automatisch aus dem E-Mail-Betreff gelesen)

## Lizenz

MIT - siehe [LICENSE](LICENSE)
