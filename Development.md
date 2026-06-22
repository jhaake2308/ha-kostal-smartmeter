# Entwicklungs-Zusammenfassung: Refactoring der Kostal KSEM Integration

WIE IMMER GILT, ERST REDEN, DANN CODEN!

## Todo ##

0) ~~Sensor `Aktive Ladefenster` als Automation-Bedingung nutzbar machen~~ → **DONE (alpha.17, 2026-06-22)**
   - `KsemActiveScheduleSensor` fehlte `device_class = SensorDeviceClass.ENUM` und `options = ["kein Zeitplan", "aktiv"]`
   - Ohne diese Attribute behandelt HA den Sensor als generischen Text-Sensor → Zustände nicht als Bedingung auswählbar
   - Fix: beide Attribute in `__init__()` ergänzt; HA zeigt im Automation-Editor jetzt Dropdown mit den beiden Zuständen

1) ~~"time" modus implementieren~~ → **DONE (alpha.13)**
   - Service `ksem.set_timebased_charge` implementiert (windows-Liste → KSEM-Kantenformat)
   - Service `ksem.clear_timebased_charge` implementiert (alles zurücksetzen)
   - API: `PUT /api/e-mobility/timebasedCharge`, charge_mode 0/1, weekday 0=So..6=Sa
   - Anbindung an Strompreis-Extension steht noch aus (Extension wird erhoben)

   **Erkenntnisse aus Tests (alpha.12, 2026-05-26):**
   - `PUT /api/e-mobility/timebasedCharge` funktioniert, Zeitplan wird korrekt übernommen ✓
   - `GET /api/e-mobility/timebasedCharge` → **404** – Endpunkt existiert nicht (nur PUT, wie bei chargemode)
   - `set_charge_mode(mode="time")` → **400 Bad Request** – "time" ist kein gültiger API-Modus;
     erlaubt sind nur: `grid | pv | hybrid | lock`. Aufruf wurde entfernt.
   - Der Zeitplan aktiviert sich ohne expliziten chargemode-Aufruf von selbst ✓
   - `_CHARGE_MODE_INT`: `grid=1` ✓, `pv=2` ✓, `hybrid=3` ✓ – alle drei bestätigt (2026-05-26)
   - Wochentagnamen (Deutsch, kurz/lang) werden jetzt im Schema akzeptiert ✓
   - Optionales Feld `mode` (grid/pv/hybrid) pro Ladefenster implementiert ✓
   - `clear_timebased_charge` + anschließender Lock-Mode-Wechsel: funktioniert, kein Fehler im Log ✓
   - **Granularität:** KSEM unterstützt offenbar nur stundenbasierte Zeitslots (Minuten werden ignoriert)
   - Coordinator-Refresh (60s): Modus bleibt aktiv, kein ungewolltes Zurückspringen ✓
   - HA zeigt während aktivem Zeitplan "Lock Mode" – technisch korrekt (KSEM meldet lock als Basismodus)

   **Testergebnisse (alpha.13, 2026-05-26):**
   | # | Testfall | Ergebnis |
   |---|----------|---------|
   | 1 | `mode: hybrid` | Solar Plus Mode ✓ |
   | 2 | `clear_timebased_charge` | Zeitplan geleert + Lock Mode in HA ✓ |
   | 3 | Zeitplan-Granularität | Nur stundenbasiert (Minuten ggf. ignoriert) |
   | 5 | HA-Neustart mit aktivem Zeitplan | Pending |
   | 6 | Coordinator-Refresh 60s | Modus bleibt stabil ✓ |

   **TODO (offen): Anzeige ob Zeitplan aktiv ist**
   - GET-Endpunkt nicht vorhanden → lokaler State nötig
   - Einfachster Ansatz: Flag in `hass.data` + Override in `KsemChargeModeSelect.current_option`
   - **Gelöst in alpha.14** über `KsemActiveScheduleSensor` + Dispatcher-Signal

2) ~~evcc-Strompreis-Integration~~ → **DONE (alpha.14)** (Branch `feature/evcc-cheapest-charging`)

   **Neue Entitäten / Services:**

   | Was | Typ | Name in HA |
   |-----|-----|------------|
   | Günstigste Slots planen | Button | "Günstig laden planen" |
   | Zeitplan löschen | Button | "Zeitplan löschen" |
   | Aktiven Zeitplan anzeigen | Sensor (RestoreEntity) | "Aktive Ladefenster" |
   | Slots aus evcc wählen | Service | `ksem.set_cheapest_charge_windows` |

   **evcc REST API:**
   - `GET http://<evcc_host>:7070/api/tariff/grid`
   - Antwort: `{"result": {"rates": [{"start": "ISO", "end": "ISO", "price": float}, ...]}}`
   - Kein Auth nötig, Timeout 10 s

   **Logik `set_cheapest_charge_windows`:**
   1. evcc-URL aus Config-Entry lesen (oder Service-Parameter – überschreibt Config)
   2. `GET /api/tariff/grid` abrufen
   3. Rates auf Suchfenster (`search_from`–`search_until`, Mitternacht-Überspannung möglich) filtern
  4. 15-Minuten-Slots werden zu vollen Stundenblöcken aggregiert (KSEM akzeptiert nur volle Stunden)
  5. Durchschnittspreis pro Stunde berechnen, dann die N günstigsten Stundenblöcke auswählen (keine Duplikate pro Stunde)
  6. UTC-Slots → Lokale Zeit → KSEM-Wochentag (Python 0=Mo → KSEM 0=So, 1=Mo … 6=Sa)
  7. `set_timebased_charge` mit erzeugten Fenstern aufrufen
  8. `SIGNAL_SCHEDULE_UPDATED` feuern → `KsemActiveScheduleSensor` aktualisiert sich

   **Config-Flow Schritt 2 (optional, überspringbar):**
   - Felder: `evcc_url`, `evcc_hours_needed` (1–8, default 3),
     `evcc_search_from` (default "22:00"), `evcc_search_until` (default "06:00"),
     `evcc_mode` (grid/pv/hybrid, default grid)
   - URL-Feld leer lassen = Schritt überspringen


  **Wöchentlich wiederkehrender Plan – wichtig für den Betrieb:**

  Der KSEM speichert einen **wöchentlich wiederkehrenden** Zeitplan, keine Einmal-Termine.
  Wird der Service z. B. am Montag-Abend mit Slots Di 01:00–02:00 und Di 04:00–05:00 aufgerufen,
  feuert dieser Plan **nächste Woche Dienstag erneut automatisch** – mit den Preisen von letzter Woche.

  **Empfohlene Betriebsstrategie:** Den Service täglich per HA-Automation aufrufen, so dass der Plan
  jeden Abend mit frischen Preisen überschrieben wird. Beispiel siehe unten.

   **Empfohlene HA-Automation:**

   ```yaml
   # Automation 1: Jeden Abend günstigste Ladefenster aus evcc planen
   - alias: "KSEM – Günstige Ladefenster planen"
     trigger:
       - platform: time
         at: "21:00:00"
     action:
       - service: ksem.set_cheapest_charge_windows
         data: {}  # alle Parameter kommen aus der Integration (Config-Entry)

   # Automation 2: Morgens Zeitplan löschen (verhindert ungewolltes
   # Wiederholen falls der Abend-Aufruf mal ausbleibt)
   - alias: "KSEM – Ladeplan morgens aufräumen"
     trigger:
       - platform: time
         at: "07:00:00"
     action:
       - service: ksem.clear_timebased_charge
         data: {}
   ```

   Ablauf mit beiden Automationen:
   - 21:00 Uhr: Plan mit günstigsten Stunden der kommenden Nacht setzen
   - Laden startet/stoppt automatisch zur gesetzten Zeit
   - 07:00 Uhr: Plan leeren; KSEM wechselt auf Lock-Mode
   - Kommen keine evcc-Daten → kein Plan, kein Fehler, nur Warning im HA-Log


  **Fehlerbehandlung & Logging:**
  - `evcc_url` fehlt/leer → Warning, kein Schedule, kein Absturz
  - HTTP-Fehler (Timeout, 4xx/5xx) → Error-Log, kein Schedule
  - Keine Rates im Suchfenster → Warning, bestehender Schedule bleibt unverändert
  - Syntaxfehler in einzelnen Rates → betroffener Slot übersprungen, Rest wird verarbeitet
  - Kein Time Mode mehr ohne gültigen Zeitplan (Schutz gegen leere/lückenhafte Pläne)
  - Alle kritischen Entscheidungen und Fehlerfälle werden klar im HA-Log dokumentiert

   **Status (alpha.14):** Implementiert, Syntaxcheck OK. Noch nicht auf echter Hardware getestet.

3) Die Verbindung zu HA blockiert auch in Version 2.0.0Alpha10 weiterhin das konstante Laden des PKW im Solar Mode, nach einigen Minuten wird das Laden pausiert, Meldung in der Wallbox: "Auf Ladefreigabe wird gewartet" o.ä. -> Debugging nötig. Siehe unten "Behobene Bugs & Änderungen" - ich vermute wir müssen weiter vereinfachen.

## Status Quo (v2.0.0-alpha.10)


Die Integration nutzt REST-Polling (zwei Coordinatoren) und Modbus TCP für Energiedaten.
Polling-Intervalle: Wallbox 60s, Smartmeter 30s, Modbus 10s. Kein dauerhafter WebSocket mehr.
Der frühere persistente WebSocket für den Lademodus wurde entfernt, da er den
internen Ladestart des KSEM blockiert hat.

### Architektur

#### REST-Polling (Coordinator)
| Coordinator | Intervall | Endpunkte |
|---|---|---|
| `ksem_smartmeter` | 30 s | `/api/device-settings/deviceusage` |
| `ksem_wallbox` | 60 s | `/api/e-mobility/evselist`, `/api/evse-kostal/evse/<id>/details`, `/api/e-mobility/config/phaseswitching`, `/api/kostal-energyflow/configuration`, `/api/e-mobility/state`, `/api/e-mobility/evparameterlist`, Chargemode-Snapshot (WS, kurz) |

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
| Systemsensoren (CPU, RAM …) | `sensor.py` | `ksem_smartmeter` | 30 s |
| Wallbox-Sensoren (Leistung, EV-Params) | `sensor.py` | `ksem_wallbox` | 60 s |
| Modbus-Sensoren (Grid, PV, Batterie, WB-Live) | `sensor.py` | `ksem_modbus_all` | 10 s |
| Lademodus | `select.py` | `ksem_wallbox` (Chargemode-Snapshot) | 60 s |
| Min PV Power Quota | `number.py` | `ksem_wallbox` (Chargemode-Snapshot) | 60 s |
| Min Charging Power Quota | `number.py` | `ksem_wallbox` (Chargemode-Snapshot) | 60 s |
| Phasenumschaltung | `select.py` | `ksem_wallbox` | 60 s |
| Battery Usage | `switch.py` | `ksem_wallbox` | 60 s |
| Aktive Ladefenster | `sensor.py` | Dispatcher `SIGNAL_SCHEDULE_UPDATED` | bei Änderung |
| Button: Günstig laden planen | `button.py` | – (ruft Service auf) | – |
| Button: Zeitplan löschen | `button.py` | – (ruft Service auf) | – |

### Behobene Bugs & Änderungen

1. **`api.py`** *(alpha.1–3)*: Falsche HA-Entity-Imports entfernt; fehlende Methode `async_listen_ws` implementiert.
2. **`__init__.py`** *(alpha.3)*: WS-Task und `chargemode_data`-Dict implementiert; exponentieller Backoff im Reconnect-Loop.
3. **`select.py`** *(alpha.3)*: `current_option` liest WS-Daten; Dispatcher-Listener für Push registriert.
4. **`number.py`** *(alpha.3)*: `set_charge_mode()` korrigiert; Dispatcher-Listener für Quota-Sync.
5. **`sensor.py`** *(alpha.3)*: Referenz auf nicht definierte Klasse `KsemWallboxSensor` entfernt.
6. **`select.py` / `number.py`** *(alpha.6)*: `@callback`-Decorator auf `_handle_chargemode_push`.
7. **Laden startet nicht** *(alpha.9)*: Persistente WS-Verbindung zum Chargemode-Stream wurde durch einen einmaligen Snapshot ersetzt. Eine dauerhaft offene Verbindung signalisierte dem KSEM einen externen Controller, der das autonome Laden blockierte. Deaktivierung der Extension hob die Sperre auf → Bestätigung der Ursache.
8. **Modbus wiederhergestellt** *(alpha.9)*: `modbus_map.py` (19 fokussierte Register), `modbus_helper.py` und `ksem_modbus_all`-Coordinator wieder eingebaut. Energiefluss-, Batterie- und Wallbox-Livedaten verfügbar.

### Bekannte Einschränkungen

- **Enector_\*-Sensoren** erscheinen aktuell unter dem Smartmeter-Gerät statt unter der Wallbox, wenn die Wallbox beim Start noch nicht erreichbar ist (race condition beim ersten Coordinator-Refresh).
- **Wallbox-Zustandsstream** (`stateConnected`, `stateCharging` etc.) via JSON-WebSocket (`ws://.../ws/json/json/local/evse/+/state`) ist nicht implementiert.
- **Wallbox-Protobuf-Stream** (Strom/Spannung je Phase) ist nicht implementiert (`.proto`-Schema nicht vorliegend).
- **KSEM-Zeitplan ist wöchentlich wiederkehrend** – ohne tägliche HA-Automation (siehe Todo 2) wiederholen sich die gesetzten Fenster jede Woche. Empfehlung: Automation täglich ~21 Uhr (Plan setzen) + ~07 Uhr (Plan leeren).
- **evcc-Feature noch ungetestet auf echter Hardware** (alpha.14, 2026-05-26).
- **Aktive-Ladefenster-Sensor als Bedingung**: Fix in alpha.17 – Integration muss nach dem Update in HA neu geladen werden, damit HA den Sensor neu als Enum-Sensor registriert.
