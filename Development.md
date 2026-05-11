Zusammenfassung für KI-Agenten: Refactoring einer Kostal KSEM Home Assistant Integration

Kontext:
Die bestehende Home Assistant Custom Component zur Steuerung eines Kostal Smart Energy Meter (KSEM) und einer ENECTOR Wallbox ist nach einem Firmware-Update des Geräts nicht mehr funktionsfähig. Die lokale REST-API des Geräts hat sich grundlegend geändert. Der alte Code, der von einer einfachen API (/api/v1/...) mit HTTP Basic Authentication und Modbus ausging, ist obsolet.

Ziel:
Refactoring des Python-Codes (insbesondere der api.py-Klasse), um die neue, Token-basierte, asynchrone API zu implementieren.
1. Authentifizierungs-Mechanismus

Die Authentifizierung erfolgt nicht mehr über Basic Auth, sondern über einen Token-basierten, zweistufigen Login-Prozess, der an eine HTTP-Session gebunden ist.

    Schritt 1: Token anfordern

        Methode: POST

        Endpunkt: /api/web-login/token

        Header Content-Type: application/x-www-form-urlencoded

        Payload (Form-Daten):

        grant_type: "password"
        client_id: "emos"
        client_secret: "56951025"
        username: "admin"      // Wichtig: Der Benutzername ist ein fester Wert.
        password: "[BENUTZER_PASSWORT]"

        Antwort (Response): Ein JSON-Objekt, das den Token enthält.

        {
          "access_token": "ey...",
          "token_type": "Bearer",
          "expires_in": 3600
        }

    Schritt 2: Authentifizierte Anfragen

        Alle nachfolgenden Anfragen an die API müssen den erhaltenen Token im Authorization-Header als Bearer-Token enthalten.

        Header-Format: Authorization: Bearer [access_token]

        Wichtiger Hinweis: Die Authentifizierung ist an die HTTP-Session (und das zugehörige Cookie) gebunden, in der der Token angefordert wurde. Ein Token kann nicht auf eine neue Session übertragen werden. Der Login muss pro Session (d.h. pro Start der Integration) erfolgen.

2. Wichtige API-Endpunkte und Methoden

Die Endpunkt-Struktur wurde ebenfalls überarbeitet.

    Schreiben/Ändern des Lademodus:

        Methode: PUT

        Endpunkt: /api/e-mobility/config/chargemode

        Payload (JSON): Es muss ein vollständiges JSON-Objekt mit 6 Feldern gesendet werden. Nur das mode-Feld zu senden, resultiert in einem 400 Bad Request.

        {
          "mode": "[MODUS_NAME]",
          "mincharginpowerquota": 0,
          "minpvpowerquota": 0,
          "lastminchargingpowerquota": 0,
          "lastminpvpowerquota": 0,
          "controlledby": 0
        }

    Lesen von Status- und Konfigurationsdaten:

        Methode: GET

        Verifizierte Endpunkte:

            /api/device-settings: Allgemeine Geräteinformationen.

            /api/device-settings/deviceusage: CPU- und Speicherauslastung.

            /api/kostal-energyflow/configuration: Energiefluss-Konfiguration (enthält den Zustand des "Battery Usage"-Schalters).

            /api/e-mobility/evselist: Listet verbundene Wallboxen und deren statische Parameter auf.

            /api/e-mobility/evparameterlist: Listet dynamische Parameter der Wallboxen auf.

        Hinweis: Der aktuelle Lademodus kann nicht per GET auf /api/e-mobility/config/chargemode gelesen werden (führt zu 405 Method Not Allowed). Der Zustand muss aus den Daten anderer Endpunkte (z.B. /api/kostal-energyflow/configuration oder WebSocket) abgeleitet werden.

    Echtzeit-Daten (Optional/Fortgeschritten):

        Die .har-Analyse hat gezeigt, dass die GUI WebSocket-Verbindungen (wss://...) für sofortige Updates verwendet, z.B. zu wss://.../api/data-transfer/ws/json/json/local/config/e-mobility/chargemode. Eine Implementierung via WebSockets ist eine Alternative zum ständigen Pollen der GET-Endpunkte.

3. Daten-Mapping (Beispiele)

Die Namen für die Lademodi haben sich geändert.
Neuer API-Wert (mode)	Entsprechung (vom Benutzer beobachtet)
hybrid	"Solar Plus Mode"
grid	"Power Mode"

Weitere Modi wie "lock" müssen noch zugeordnet werden. Die alten Werte (sc_solar_...) sind ungültig.
4. Implementierungs-Richtlinien für Home Assistant

    Die Implementierung muss asynchron erfolgen. Anstelle von requests ist die aiohttp-Bibliothek zu verwenden, die über homeassistant.helpers.aiohttp_client.async_get_clientsession verfügbar ist.

    Der Token-Login-Prozess sollte in der __init__-Methode der API-Wrapper-Klasse implementiert werden. Die aiohttp.ClientSession handhabt die Cookies automatisch.

    Die DataUpdateCoordinator der Integration muss so angepasst werden, dass er periodisch die relevanten GET-Endpunkte abfragt, um die Zustände in Home Assistant zu aktualisieren.