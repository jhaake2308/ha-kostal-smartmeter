# Entwicklungs-Zusammenfassung: Refactoring der Kostal KSEM Integration

WIE IMMER GILT, ERST REDEN, DANN CODEN!

## Todo ##

1) ~~"time" modus implementieren~~ → **DONE (alpha.11)**
   - Service `ksem.set_timebased_charge` implementiert (windows-Liste → KSEM-Kantenformat)
   - Service `ksem.clear_timebased_charge` implementiert (alles zurücksetzen)
   - API: `PUT /api/e-mobility/timebasedCharge`, charge_mode 0/1, weekday 0=So..6=Sa
   - Anbindung an Strompreis-Extension steht noch aus (Extension wird erhoben)
   - **TODO (offen):** GET /api/e-mobility/timebasedCharge implementieren (aktuellen Plan in HA lesen)
2) Die Verbindung zu HA blockiert auch in Version 2.0.0Alpha10 weiterhin das konstante Laden des PKW im Solar Mode, nach einigen Minuten wird das Laden pausiert, Meldung in der Wallbox: "Auf Ladefreigabe wird gewartet" o.ä. -> Debugging nötig. Siehe unten "Behobene Bugs & Änderungen" - ich vermute wir müssen weiter vereinfachen.

   **Branch `debug/solar-mode-blocking` — was entfernt wurde (Stufe A + B):**
   - **SmartmeterCoordinator komplett entfernt** (CPU/RAM/Flash-Diagnostics, 30s-Polling) → 8 Sensor-Entities weg
   - **WS-Snapshot** (`get_chargemode_snapshot`) aus wallbox_coordinator entfernt → Hauptverdächtiger #1
   - **`GET /api/e-mobility/state`** (evse_state) entfernt → Verdächtiger #2
   - **`GET /api/e-mobility/evparameterlist`** (ev_params) entfernt → Verdächtiger #3
   - **Klassen entfernt:** `KsemSmartmeterSensor`, `KsemEvParameterSensor`, `KsemEvseAvailablePowerSensor`
   - **Modbus-Interval:** 10s → 30s (ausreichend für Ladeleistungsanzeige)
   - **ChargeModeSelect:** Liest jetzt optimistisch aus `hass.data["last_chargemode"]` statt WS-Daten
   - **Architektur nach Bereinigung:** Modbus (30s) für alle Live-Daten + schlanker wallbox_coordinator (60s) nur für evselist/phaseswitching/energyflow + REST on-demand für Steuerung
   - **Rollback:** `git checkout main` oder PR nicht mergen

## Status Quo (v2.0.0-alpha.12 / main)

Die Integration nutzt REST-Polling (ein Coordinator) und Modbus TCP für Energiedaten.
Der WS-Snapshot für den Chargemode wird weiterhin alle 60 s kurz geöffnet.
Solar-Mode-Blocking-Bug ist noch nicht abschließend gelöst (Debug-Branch aktiv).

### Architektur

#### REST-Polling (Coordinator)
| Coordinator | Intervall | Endpunkte |
|---|---|---|
| `ksem_wallbox` | 60 s | `/api/e-mobility/evselist`, `/api/evse-kostal/evse/<id>/details`, `/api/e-mobility/config/phaseswitching`, `/api/kostal-energyflow/configuration`, Chargemode-Snapshot (WS, kurz) |

#### Modbus TCP (`ksem_modbus_all`)
| Coordinator | Intervall | Register |
|---|---|---|
| `ksem_modbus_all` | 10 s | 19 Register in 4 Blöcken: Netzleistung (0–27), Energiezähler (512–519), Energiefluss (40972–41003), Wallbox Live (49206–49257) |

#### Chargemode-Snapshot
- Beim Coordinator-Update wird kurz eine WS-Verbindung zu
  `ws://<host>/api/data-transfer/ws/json/json/local/config/e-mobility/chargemode` aufgebaut
- Nach Empfang der ersten Nachricht wird die Verbindung **sofort getrennt** (~1 s)
- Kein dauerhafter WS → KSEM bleibt im autonomen Lademodus

### Entitäten & Datenquellen

| Entität | Datei | Datenquelle | Intervall |
|---|---|---|---|
| Modbus-Sensoren (Grid, PV, Batterie, WB-Live) | `sensor.py` | `ksem_modbus_all` | 10 s |
| Lademodus | `select.py` | `ksem_wallbox` (Chargemode-Snapshot, optimistisch) | 60 s |
| Min PV Power Quota | `number.py` | `ksem_wallbox` (Chargemode-Snapshot) | 60 s |
| Min Charging Power Quota | `number.py` | `ksem_wallbox` (Chargemode-Snapshot) | 60 s |
| Phasenumschaltung | `select.py` | `ksem_wallbox` | 60 s |
| Battery Usage | `switch.py` | `ksem_wallbox` | 60 s |

### Behobene Bugs & Änderungen

1. **`api.py`** *(alpha.1–3)*: Falsche HA-Entity-Imports entfernt; fehlende Methode `async_listen_ws` implementiert.
2. **`__init__.py`** *(alpha.3)*: WS-Task und `chargemode_data`-Dict implementiert; exponentieller Backoff im Reconnect-Loop.
3. **`select.py`** *(alpha.3)*: `current_option` liest WS-Daten; Dispatcher-Listener für Push registriert.
4. **`number.py`** *(alpha.3)*: `set_charge_mode()` korrigiert; Dispatcher-Listener für Quota-Sync.
5. **`sensor.py`** *(alpha.3)*: Referenz auf nicht definierte Klasse `KsemWallboxSensor` entfernt.
6. **`select.py` / `number.py`** *(alpha.6)*: `@callback`-Decorator auf `_handle_chargemode_push`.
7. **Laden startet nicht** *(alpha.9)*: Persistente WS-Verbindung zum Chargemode-Stream wurde durch einen einmaligen Snapshot ersetzt. Eine dauerhaft offene Verbindung signalisierte dem KSEM einen externen Controller, der das autonome Laden blockierte. Deaktivierung der Extension hob die Sperre auf → Bestätigung der Ursache.
8. **Modbus wiederhergestellt** *(alpha.9)*: `modbus_map.py` (19 fokussierte Register), `modbus_helper.py` und `ksem_modbus_all`-Coordinator wieder eingebaut. Energiefluss-, Batterie- und Wallbox-Livedaten verfügbar.
9. **Zeitbasiertes Laden** *(alpha.11)*: Services `ksem.set_timebased_charge` und `ksem.clear_timebased_charge` implementiert. API `PUT /api/e-mobility/timebasedCharge`, Kantenformat, weekday 1=Mo…6=Sa/0=So. Service aktiviert Modus automatisch auf `time`.
10. **Time Mode Dropdown + Weekday-Doku** *(alpha.12)*: `"time"` in MODE_MAP ergänzt; Chargemode-Select liest optimistisch aus `hass.data` statt WS; Wochentag-Beschreibung auf Mo-first umgestellt; `selector`-Bug in services.yaml behoben (List-Wert wurde als None übergeben).

### Bekannte Einschränkungen

- **Enector_\*-Sensoren** erscheinen aktuell unter dem Smartmeter-Gerät statt unter der Wallbox, wenn die Wallbox beim Start noch nicht erreichbar ist (race condition beim ersten Coordinator-Refresh).
- **Wallbox-Zustandsstream** (`stateConnected`, `stateCharging` etc.) via JSON-WebSocket (`ws://.../ws/json/json/local/evse/+/state`) ist nicht implementiert.
- **Wallbox-Protobuf-Stream** (Strom/Spannung je Phase) ist nicht implementiert (`.proto`-Schema nicht vorliegend).
