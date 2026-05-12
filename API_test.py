import requests
import pprint
import urllib3
import os
from dotenv import load_dotenv

# Deaktiviert Warnungen für unsichere HTTPS-Anfragen
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Lade Umgebungsvariablen aus der secrets.env-Datei
load_dotenv('secrets.env')

# -- KONFIGURATION --
DEVICE_IP = os.getenv("KSEM_SERVER")  # <-- IP-Adresse oder Hostname deines Geräts

# OPTION 1: Manueller Token
# Fülle diesen String, um den Login zu überspringen und diesen Token direkt zu verwenden.
# Lasse ihn leer (""), um den Login per Passwort zu erzwingen.
PRE_GENERATED_TOKEN = ""  # <--- HIER EINEN GÜLTIGEN TOKEN EINFÜGEN

# OPTION 2: Passwort (wird nur verwendet, wenn PRE_GENERATED_TOKEN leer ist)
#PASSWORD = "dein_passwort"     # <-- Dein Passwort als Fallback
PASSWORD = os.getenv("KSEM_PASSWORD")  # <-- Passwort aus der .env-Datei

# --------------------

BASE_URL = f"https://{DEVICE_IP}"
pp = pprint.PrettyPrinter(indent=2)

def get_api_token(session, password):
    """Holt einen NEUEN Bearer-Token vom Login-Endpunkt."""
    login_endpoint = "/api/web-login/token"
    payload = {
        "grant_type": "password",
        "client_id": "emos",
        "client_secret": "56951025",
        "username": "admin",
        "password": password,
    }

    print(f"--- Führe Token-Login per Passwort durch an: {login_endpoint} ---")
    try:
        response = session.post(f"{BASE_URL}{login_endpoint}", data=payload, timeout=10, verify=False)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")

        if not access_token:
            print("!!! Login fehlgeschlagen: Konnte keinen access_token in der Antwort finden.")
            pp.pprint(token_data)
            return None

        print("Login erfolgreich! Token erhalten.")
        session.headers.update({"Authorization": f"Bearer {access_token}"})
        return access_token

    except requests.exceptions.RequestException as e:
        print(f"!!! Login fehlgeschlagen: {e}")
        return None
    finally:
        print("-" * 30 + "\n")

def test_api_endpoint(session, endpoint):
    """Eine generische Funktion zum Testen von GET-Endpunkten."""
    print(f"--- Abfrage von: {endpoint} ---")
    try:
        response = session.get(f"{BASE_URL}{endpoint}", timeout=5, verify=False)
        print(f"Status-Code: {response.status_code}")
        response.raise_for_status()
        print("Antwort-JSON:")
        pp.pprint(response.json())
    except requests.exceptions.RequestException as e:
        print(f"Fehler bei der Abfrage: {e}")
    finally:
        print("-" * 30 + "\n")

if __name__ == "__main__":
    print(f"Starte Tests für Gerät unter: {DEVICE_IP}\n")
    
    s = requests.Session()
    login_successful = False

    # NEUE LOGIK: Prüfe, ob ein manueller Token vorhanden ist
    if PRE_GENERATED_TOKEN:
        print("--- Verwende vordefinierten Token ---")
        s.headers.update({"Authorization": f"Bearer {PRE_GENERATED_TOKEN}"})
        login_successful = True
        print("Authorization-Header wurde gesetzt.\n")
    else:
        print("Kein vordefinierter Token gefunden, versuche Login per Passwort...")
        # Wenn kein Token da ist, führe den normalen Login durch
        if get_api_token(s, PASSWORD):
            login_successful = True
    
    # Führe die Tests nur aus, wenn die Authentifizierung (egal wie) geklappt hat
    if login_successful:
        print(">>> Authentifizierung erfolgreich. Teste verschiedene API-Endpunkte...")
        
        test_api_endpoint(s, "/api/device-settings")
        test_api_endpoint(s, "/api/kostal-energyflow/configuration")
        test_api_endpoint(s, "/api/e-mobility/evselist")
    else:
        print("!!! Authentifizierung fehlgeschlagen. Breche weitere Tests ab.")
        
    print("Alle Tests abgeschlossen.")

