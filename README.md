# Kostal KSEM Home Assistant Integration

This is a custom component for Home Assistant to integrate the Kostal Smart Energy Meter (KSEM) and a connected ENECTOR wallbox.


## Status (v2.0.0-beta.2)

!! NICHT FÜR PRODUKTIVE NUTZUNG !!

Die Kernfunktionalität ist implementiert und wird derzeit getestet:
- **Lademodus-Umschaltung** (HA → Wallbox): funktioniert
- **Laden startet**: Fix in alpha.9 – persistente WS-Verbindung wurde durch Snapshot-Polling ersetzt (dauerhafter WS blockierte KSEM-internen Ladestart)
- **Energiedaten via Modbus**: Grid-Leistung, PV, Batterie-SoC, Wallbox-Ladeleistung etc. wieder verfügbar
- **Zeitbasiertes Laden**: Service `ksem.set_timebased_charge` setzt Ladefenster pro Wochentag und Lademodus; `ksem.clear_timebased_charge` setzt zurück und aktiviert Lock-Mode
- **Günstigste Ladefenster (evcc)**: Es werden immer die N günstigsten vollen Stunden (egal ob zusammenhängend oder verteilt) als Ladefenster gesetzt. 15-Minuten-Slots werden zu Stundenblöcken aggregiert. Kein Time Mode mehr ohne gültigen Zeitplan.
- **Zeitplan-Sensor & Binary Sensor**: `sensor.…aktive_ladefenster` zeigt Fenster-Details; `binary_sensor.…zeitplan_aktiv` dient als robuste Automation-Bedingung ("ist eingeschaltet").
- **Logging & Fehlerbehandlung**: Ausführliche Logs bei Problemen, keine Aktivierung des Time Mode ohne gültigen Zeitplan.


## Features

* Sensoren für KSEM-Gerätestatus (CPU, RAM).
* Energiefluss-Sensoren via Modbus (Grid, PV, Batterie, Hausverbrauch, Wallbox-Aufteilung).
* Wallbox Live-Sensoren via Modbus (Ladestatus, Ladeleistung, geladene Energie, Strom L1).
* Steuerung des Lademodus (Lock, Power, Solar Pure, Solar Plus).
* Steuerung von Min PV Power und Min Charging Power Quotas.
* Schalter für Batterienutzung im Solar-Pure-Modus.
* Auswahl der Phasenumschaltung (1 Phase / 3 Phasen / Automatisch).
* Zeitbasiertes Laden: Ladefenster per Service setzen (Wochentag als Zahl oder Deutsch, Lademodus pro Fenster).
* **Automatische Auswahl der günstigsten Ladefenster:** Die Integration wählt die N günstigsten Stundenblöcke im gewünschten Zeitfenster (z.B. 3h zwischen 22 und 6 Uhr). Es werden keine Minuten- oder Halbstundenslots gesetzt, sondern immer volle Stunden.
* **Binary Sensor `Zeitplan aktiv`:** Einfache `an`/`aus`-Entität für Automation-Bedingungen (z.B. `ksem.clear_timebased_charge` nur ausführen wenn Zeitplan aktiv).
* **Empfohlene Automation:** Tägliches Überschreiben des Zeitplans per HA-Automation, um wöchentlich wiederkehrende Fenster zu vermeiden und immer aktuelle Preise zu nutzen.
* **Polling-Intervalle:** Wallbox wird alle 60s, Smartmeter alle 30s, Modbus alle 10s abgefragt.
* **Erweiterte Fehlerbehandlung und Logging:** Alle kritischen Entscheidungen und Fehlerfälle werden klar im HA-Log dokumentiert.

## Installation via HACS (empfohlen)

1.  In HA: **HACS → drei Punkte → Custom repositories**
2.  URL: `https://github.com/jhaake2308/ha-kostal-smartmeter`, Kategorie: **Integration**
3.  Integration installieren, HA neu starten
4.  Zukünftige Updates erscheinen automatisch unter HACS → Updates

## Installation manuell

1.  Den Ordner `custom_components/ksem` in `<config>/custom_components/` kopieren.
2.  HA neu starten.

## Konfiguration

1.  **Einstellungen → Geräte & Dienste → Integration hinzufügen**
2.  Nach **Kostal Smartmeter** suchen
3.  IP-Adresse des KSEM und Gerätepasswort eingeben
