# Kostal KSEM Home Assistant Integration

This is a custom component for Home Assistant to integrate the Kostal Smart Energy Meter (KSEM) and a connected ENECTOR wallbox.

## Status (v1.0.0-alpha.6)

Die Kernfunktionalität ist stabil und getestet:
- **KSEM-GUI → HA**: Lademodus-Änderungen kommen per WebSocket-Push praktisch verzögerungsfrei an
- **HA → Wallbox**: Lademodus-Umschaltung per REST funktioniert

## Features

*   Sensors for KSEM device status (CPU, RAM).
*   Sensors for live charging data from the wallbox (Available Power, EV Current, Phases, etc.).
*   Control for the charging mode (Lock, Power, Solar Pure, Solar Plus) — **live push from device**.
*   Control for Min PV Power and Min Charging Power quotas — **live push from device**.
*   Switch to control battery usage during Solar Pure mode.
*   Selector for phase switching (1 Phase / 3 Phases / Automatic).

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
