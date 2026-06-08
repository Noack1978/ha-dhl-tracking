# DHL Sendungsverfolgung fuer Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Offizielle DHL API-Integration fuer Home Assistant. Verfolge Pakete direkt in HA.

## Features

- Sendungsverfolgung ueber DHL-Website-API (kein spezieller API-Zugang noetig)
- Unterstuetzt alle DHL-Sendungsnummernformate (00-Prefix, JJD, Express)
- Mehrere Sendungen gleichzeitig verfolgen
- Individuelle Bezeichnungen pro Sendung
- Automatische Aktualisierung (konfigurierbares Intervall)
- E-Mail-Scanner: erkennt Sendungsnummern automatisch aus DHL-Mails
- Services zum Hinzufuegen/Entfernen von Sendungen (auch per Automation)
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

[![In HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Noack1978&repository=ha-dhl-tracking&category=integration)

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

## Automations-Beispiel

```yaml
alias: "Paket zugestellt"
trigger:
  - platform: state
    entity_id: sensor.dhl_sendungsverfolgung_mein_paket
    to: "Zugestellt"
action:
  - service: notify.mobile_app_mein_handy
    data:
      title: "Paket angekommen!"
      message: "{{ state_attr(trigger.entity_id, 'label') }} wurde zugestellt."
```

## Lizenz

MIT - siehe [LICENSE](LICENSE)
