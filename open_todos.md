# Offene TODOs und Fragen zum Refactoring

Dieses Dokument listet die offenen Punkte und Fragen auf, die während des Refactorings der Kostal KSEM Home Assistant Integration geklärt werden müssen.

## Fragen an den Entwickler

1.  **Priorisierung der Aufgaben:**
    - Was ist das erste, wichtigste Ziel?
    - Sollen wir mit dem **Login-Mechanismus** in der `api.py` unter Verwendung von `aiohttp` beginnen?

2.  **Zuordnung der Lademodi:**
    - Gibt es bereits Erkenntnisse, welche API-Werte den anderen Modi (`lock`, `time`, etc.) entsprechen?
    - Oder soll der Code vorerst nur für `hybrid` und `grid` angepasst werden?

3.  **Lesen des aktuellen Lademodus:**
    - Gibt es eine Vermutung, welches Feld in der Antwort von `/api/kostal-energyflow/configuration` (oder einem anderen Endpunkt) den aktuellen Lademodus repräsentiert?
    - Falls nicht, soll der Code so gestaltet werden, dass dies später leicht ergänzt werden kann?

4.  **WebSocket-Implementierung:**
    - Soll die WebSocket-Anbindung (für Echtzeit-Daten) vorerst ignoriert und stattdessen auf das übliche Polling der GET-Endpunkte gesetzt werden?
