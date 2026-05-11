# Offene TODOs und Fragen zum Refactoring

Dieses Dokument listet die offenen Punkte und Fragen auf, die während des Refactorings der Kostal KSEM Home Assistant Integration geklärt werden müssen.

## Fragen an den Entwickler

1.  **Priorisierung der Aufgaben:**
    - Was ist das erste, wichtigste Ziel?
    - Sollen wir mit dem **Login-Mechanismus** in der `api.py` unter Verwendung von `aiohttp` beginnen?

2.  **Zuordnung der Lademodi:**
    - Gibt es bereits Erkenntnisse, welche API-Werte den anderen Modi (`lock`, `time`, etc.) entsprechen?
    -> Ja, siehe Development.md, alle verfügbaren modi sind gemappt

3.  **Lesen des aktuellen Lademodus:**
    - Gibt es eine Vermutung, welches Feld in der Antwort von `/api/kostal-energyflow/configuration` (oder einem anderen Endpunkt) den aktuellen Lademodus repräsentiert?
    - Der Lademodus kann nur via websocket abgerufen werden, siehe development.md

4.  **WebSocket-Implementierung:**
    - Soll die WebSocket-Anbindung (für Echtzeit-Daten) vorerst ignoriert und stattdessen auf das übliche Polling der GET-Endpunkte gesetzt werden?
    -> Websocket-Implementierung, allerdings muss hier der Aufwand abgeschätzt werden
