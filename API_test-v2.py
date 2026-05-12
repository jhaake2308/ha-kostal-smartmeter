import requests
import pprint
import urllib3
import os
from dotenv import load_dotenv

# ... (Konfiguration und get_api_token Funktion bleiben identisch wie im letzten Skript) ...
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv('secrets.env')
DEVICE_IP = os.getenv("KSEM_SERVER")  # <-- IP-Adresse oder Hostname deines Geräts
PASSWORD = os.getenv("KSEM_PASSWORD")  # <-- Passwort aus der .env-Datei
BASE_URL = f"https://{DEVICE_IP}"
pp = pprint.PrettyPrinter(indent=2)

def get_api_token(session, password):
    # ... (Diese Funktion bleibt unverändert)
    login_endpoint = "/api/web-login/token"
    payload = { "grant_type": "password", "client_id": "emos", "client_secret": "56951025", "username": "admin", "password": password }
    print(f"--- Führe Token-Login per Passwort durch an: {login_endpoint} ---")
    try:
        response = session.post(f"{BASE_URL}{login_endpoint}", data=payload, timeout=10, verify=False)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        if not access_token: return None
        print("Login erfolgreich! Token erhalten.")
        session.headers.update({"Authorization": f"Bearer {access_token}"})
        return access_token
    except requests.exceptions.RequestException as e:
        print(f"!!! Login fehlgeschlagen: {e}")
        return None
    finally:
        print("-" * 30 + "\n")


def test_get_endpoint(session, endpoint):
    """Generische Funktion zum Testen von GET-Endpunkten."""
    print(f"--- GET Abfrage von: {endpoint} ---")
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

def test_post_endpoint(session, endpoint, payload):
    """Generische Funktion zum Testen von POST-Endpunkten."""
    print(f"--- POST an: {endpoint} ---")
    print("Sende Payload:")
    pp.pprint(payload)
    try:
        response = session.post(f"{BASE_URL}{endpoint}", json=payload, timeout=5, verify=False)
        print(f"Status-Code: {response.status_code}")
        response.raise_for_status()
        print("Antwort-JSON (falls vorhanden):")
        try:
            pp.pprint(response.json())
        except requests.exceptions.JSONDecodeError:
            print("Keine JSON-Antwort erhalten.")
    except requests.exceptions.RequestException as e:
        print(f"Fehler beim Senden: {e}")
    finally:
        print("-" * 30 + "\n")


if __name__ == "__main__":
    print(f"Starte Tests für Gerät unter: {DEVICE_IP}\n")
    
    s = requests.Session()
    
    if get_api_token(s, PASSWORD):
        print(">>> Authentifizierung erfolgreich. Teste weitere API-Endpunkte...")
        
        # --- GET-Anfragen (Lesen von Daten) ---
        
        # Systemauslastung testen
        test_get_endpoint(s, "/api/device-settings/deviceusage")
        
        # Konfiguration des Lademodus LESEN
        test_get_endpoint(s, "/api/e-mobility/config/chargemode")
        
        # Möglichen Live-Daten-Endpunkt testen (geraten, aber wahrscheinlich)
        # test_get_endpoint(s, "/api/energy-flow/live") 
        
        
        # --- POST-Anfragen (Schreiben/Ändern von Daten) ---
        
        # Testweise den Lademodus auf "Solar Pure" setzen
        # payload_solar_pure = {"mode": "sc_solar_photovoltaics_power_only"}
        # test_post_endpoint(s, "/api/e-mobility/config/chargemode", payload_solar_pure)
        
        # Testweise zurück auf "Lock Mode" setzen
        # payload_lock = {"mode": "sc_unlock"} # Annahme, dass 'sc_unlock' der Wert für Lock Mode ist
        # test_post_endpoint(s, "/api/e-mobility/config/chargemode", payload_lock)

    else:
        print("!!! Authentifizierung fehlgeschlagen. Breche weitere Tests ab.")
        
    print("Alle Tests abgeschlossen.")
