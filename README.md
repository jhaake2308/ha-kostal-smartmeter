# Kostal KSEM Home Assistant Integration

This is a custom component for Home Assistant to integrate the Kostal Smart Energy Meter (KSEM) and a connected ENECTOR wallbox.

## Status (v2.0.0-alpha.13)

!! NOT FOR PRODUCTIVE USE !!

Die Kernfunktionalität ist implementiert und wird derzeit getestet:
- **Lademodus-Umschaltung** (HA → Wallbox): funktioniert
- **Laden startet**: Fix in alpha.9 – persistente WS-Verbindung wurde durch Snapshot-Polling ersetzt (dauerhafter WS blockierte KSEM-internen Ladestart)
- **Energiedaten via Modbus**: Grid-Leistung, PV, Batterie-SoC, Wallbox-Ladeleistung etc. wieder verfügbar
- **Zeitbasiertes Laden**: Service `ksem.set_timebased_charge` setzt Ladefenster pro Wochentag und Lademodus; `ksem.clear_timebased_charge` setzt zurück und aktiviert Lock-Mode

## Features

*   Sensoren für KSEM-Gerätestatus (CPU, RAM).
*   Energiefluss-Sensoren via Modbus (Grid, PV, Batterie, Hausverbrauch, Wallbox-Aufteilung).
*   Wallbox Live-Sensoren via Modbus (Ladestatus, Ladeleistung, geladene Energie, Strom L1).
*   Steuerung des Lademodus (Lock, Power, Solar Pure, Solar Plus).
*   Steuerung von Min PV Power und Min Charging Power Quotas.
*   Schalter für Batterienutzung im Solar-Pure-Modus.
*   Auswahl der Phasenumschaltung (1 Phase / 3 Phasen / Automatisch).
*   Zeitbasiertes Laden: Ladefenster per Service setzen (Wochentag als Zahl oder Deutsch, Lademodus pro Fenster).

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
