import requests
import pprint
import urllib3
import os
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -- KONFIGURATION --
# Lade Umgebungsvariablen aus der secrets.env-Datei
load_dotenv('secrets.env')

DEVICE_IP = "ksem.haake.io"
PASSWORD = os.getenv("KSEM_PASSWORD")  # <-- Passwort aus der .env-Datei
# --------------------

if not PASSWORD:
    raise ValueError("KSEM_PASSWORD nicht in der secrets.env-Datei gefunden!")

BASE_URL = f"https://{DEVICE_IP}"
pp = pprint.PrettyPrinter(indent=2)

def get_api_token(session, password):
    # (Diese Funktion ist korrekt und bleibt unverändert)
    login_endpoint = "/api/web-login/token"
    payload = { "grant_type": "password", "client_id": "emos", "client_secret": "56951025", "username": "admin", "password": password }
    print("--- Führe Token-Login durch ---")
    try:
        response = session.post(f"{BASE_URL}{login_endpoint}", data=payload, timeout=10, verify=False)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        if not access_token:
            print("!!! Login fehlgeschlagen.")
            return None
        print("Login erfolgreich!")
        session.headers.update({"Authorization": f"Bearer {access_token}"})
        return access_token
    except requests.exceptions.RequestException as e:
        print(f"!!! Login fehlgeschlagen: {e}")
        return None
    finally:
        print("-" * 30 + "\n")

def set_charge_mode(session, mode_name):
    """Setzt den Lademodus mit der korrekten PUT-Methode und dem vollständigen Payload."""
    endpoint = "/api/e-mobility/config/chargemode"
    
    # Der vollständige Payload, wie im HAR-Trace entdeckt
    payload = {
        "mode": mode_name,
        "mincharginpowerquota": 0,
        "minpvpowerquota": 0,
        "lastminchargingpowerquota": 0,
        "lastminpvpowerquota": 0,
        "controlledby": 0
    }
    
    print(f"--- Sende PUT-Request an: {endpoint} ---")
    print("Sende Payload:")
    pp.pprint(payload)
    try:
        response = session.put(f"{BASE_URL}{endpoint}", json=payload, timeout=5, verify=False)
        print(f"Status-Code: {response.status_code}")
        response.raise_for_status()
        print("Befehl erfolgreich gesendet!")
        
    except requests.exceptions.RequestException as e:
        print(f"Fehler beim Senden: {e}")
    finally:
        print("-" * 30 + "\n")

if __name__ == "__main__":
    print(f"Starte Tests für Gerät unter: {DEVICE_IP}\n")
    s = requests.Session()
    if get_api_token(s, PASSWORD):
        
        # Test 1: Setze den Lademodus auf "hybrid" (das war der Wert im Trace)
        # Die alten Werte wie "sc_solar_photovoltaics_power_only" müssen wir ggf.
        # durch die neuen Werte ("hybrid", "grid", "lock" etc.) ersetzen.
        #set_charge_mode(s, "hybrid")
# hybrid = solar plus mode
        # Test 2: Setze den Lademodus auf "grid" (gesehen im zweiten PUT des Traces)
        set_charge_mode(s, "lock")
# grid = power mode
        
    else:
        print("!!! Authentifizierung fehlgeschlagen. Breche weitere Tests ab.")
        
    print("Alle Tests abgeschlossen.")

