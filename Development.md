# Entwicklungs-Zusammenfassung: Refactoring der Kostal KSEM Integration

WIE IMNMER GILT, ERST REDEN, DANN CODEN!

## Status Quo (v1.0.0-alpha.6)

Die Integration nutzt eine Kombination aus REST-Polling (Coordinator) und einer
persistenten WebSocket-Verbindung für Echtzeit-Push-Updates des Lademodus.
Beide Richtungen sind getestet und funktionieren:

### Architektur

#### REST-Polling (Coordinator)
| Coordinator | Intervall | Endpunkte |
|---|---|---|
| `ksem_smartmeter` | 30 s | `/api/device-settings/deviceusage` |
| `ksem_wallbox` | 10 s | `/api/e-mobility/evselist`, `/api/evse-kostal/evse/<id>/details`, `/api/e-mobility/config/phaseswitching`, `/api/kostal-energyflow/configuration`, `/api/e-mobility/state`, `/api/e-mobility/evparameterlist` |

#### WebSocket-Push (`ksem_chargemode_ws`)
- Verbindung zu `ws://<host>/api/data-transfer/ws/json/json/local/config/e-mobility/chargemode`
- Authentifizierung: Bearer-Token im HTTP-Header + erste WS-Nachricht `"Bearer <token>"`
- `heartbeat=30` → stumme Verbindungsabbrüche werden schnell erkannt
- Exponentieller Backoff beim Reconnect: 5 s → 10 s → 20 s → … max 300 s
- Jede empfangene Nachricht füllt `chargemode_data` und sendet `SIGNAL_CHARGEMODE_UPDATE`

### Entitäten & Push-Verhalten

| Entität | Datei | Datenquelle | Push |
|---|---|---|---|
| Systemsensoren (CPU, RAM …) | `sensor.py` | `ksem_smartmeter` | Nein (Poll) |
| Wallbox-Sensoren (Leistung, EV-Params) | `sensor.py` | `ksem_wallbox` | Nein (Poll) |
| Lademodus | `select.py` | WS `chargemode_data` | **Ja** – sofort bei WS-Event |
| Min PV Power Quota | `number.py` | WS `chargemode_data` | **Ja** – sofort bei WS-Event |
| Min Charging Power Quota | `number.py` | WS `chargemode_data` | **Ja** – sofort bei WS-Event |
| Phasenumschaltung | `select.py` | `ksem_wallbox` | Nein (Poll) |
| Battery Usage | `switch.py` | `ksem_wallbox` | Nein (Poll) |

### Behobene Bugs (seit alpha.3)

1. **`api.py`**: Falsche HA-Entity-Imports entfernt; fehlende Methode `async_listen_ws` implementiert.
2. **`__init__.py`**: Fehlender WS-Task und `chargemode_data`-Dict implementiert; `SIGNAL_CHARGEMODE_UPDATE` Dispatcher-Signal hinzugefügt; exponentieller Backoff im Reconnect-Loop.
3. **`select.py`**: Ungültiger Parameter `token=None` entfernt; `current_option` liest jetzt echte WS-Daten; Dispatcher-Listener für sofortigen Push registriert.
4. **`number.py`**: `set_charge_mode()` mit ungültigen Argumenten korrigiert; Dispatcher-Listener für Quota-Sync via Push hinzugefügt.
5. **`sensor.py`**: Referenz auf nicht definierte Klasse `KsemWallboxSensor` entfernt.
6. **`select.py` / `number.py`** *(alpha.6)*: `@callback`-Decorator auf `_handle_chargemode_push` – `async_write_ha_state()` wurde aus SyncWorker-Threads aufgerufen und löste `RuntimeError` aus.

### Bekannte Einschränkungen

- **Wallbox-Live-Messwerte** (Strom, Spannung je Phase) werden über den Protobuf-WebSocket
  (`ws://.../ws/protobuf/gdr/local/values/+/evse`) übertragen. Die Integration liest diese
  noch nicht aus, da das `.proto`-Schema nicht vorliegt.
- **Wallbox-Zustandsstream** (`stateConnected`, `stateCharging` etc.) via JSON-WebSocket
  (`ws://.../ws/json/json/local/evse/+/state`) ist noch nicht implementiert.
  Beides ist für eine spätere Version geplant.
