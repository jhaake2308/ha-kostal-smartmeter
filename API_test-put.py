# ... (der obere Teil des Skripts mit Konfiguration, get_api_token etc. bleibt gleich) ...
import requests
import pprint
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
DEVICE_IP = "ksem.haake.io"
PASSWORD = "dein_passwort"
BASE_URL = f"https://{DEVICE_IP}"
pp = pprint.PrettyPrinter(indent=2)

def get_api_token(session, password):
    # (Funktion unverändert)
    login_endpoint = "/api/web-login/token"
    payload = { "grant_type": "password", "client_id": "emos", "client_secret": "56951025", "username": "admin", "password": password }
    try:
        response = session.post(f"{BASE_URL}{login_endpoint}", data=payload, timeout=10, verify=False)
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        if not access_token: return None
        session.headers.update({"Authorization": f"Bearer {access_token}"})
        return access_token
    except requests.exceptions.RequestException: return None

def test_put_endpoint(session, endpoint, payload):
    """NEUE Funktion zum Testen von PUT-Endpunkten."""
    print(f"--- PUT an: {endpoint} ---")
    print("Sende Payload:")
    pp.pprint(payload)
    try:
        response = session.put(f"{BASE_URL}{endpoint}", json=payload, timeout=5, verify=False)
        print(f"Status-Code: {response.status_code}")
        response.raise_for_status()
        print("Antwort-JSON (falls vorhanden):")
        try: pp.pprint(response.json())
        except requests.exceptions.JSONDecodeError: print("Keine JSON-Antwort erhalten.")
    except requests.exceptions.RequestException as e:
        print(f"Fehler beim Senden: {e}")
    finally:
        print("-" * 30 + "\n")


if __name__ == "__main__":
    print(f"Starte Tests für Gerät unter: {DEVICE_IP}\n")
    s = requests.Session()
    if get_api_token(s, PASSWORD):
        print(">>> Authentifizierung erfolgreich. Teste den PUT-Request...")
        
        # Teste das Ändern des Lademodus mit der PUT-Methode
        payload_solar_pure = {"mode": "sc_solar_photovoltaics_power_only"}
        test_put_endpoint(s, "/api/e-mobility/config/chargemode", payload_solar_pure)

    else:
        print("!!! Authentifizierung fehlgeschlagen.")
        
    print("Alle Tests abgeschlossen.")
