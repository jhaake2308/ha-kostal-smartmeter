#!/usr/bin/env python3
"""
Schnelltest: Setzt / löscht zeitbasierten Ladeplan direkt am KSEM-Gerät.

Aufruf:
  python3 test_timebased.py <host> <password>           # Test-Fenster setzen
  python3 test_timebased.py <host> <password> --clear   # Plan löschen

Beispiel:
  python3 test_timebased.py ksem.haake.io MeinPasswort
  python3 test_timebased.py ksem.haake.io MeinPasswort --clear
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "custom_components", "ksem"))

HOST = sys.argv[1] if len(sys.argv) > 1 else "ksem.haake.io"
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else ""
CLEAR = "--clear" in sys.argv


def _parse_hhmm(time_str: str) -> tuple[int, int]:
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1])


def _build_timebased_schedule(windows: list) -> list:
    edges: dict[tuple, int] = {}
    for wd in range(7):
        edges[(wd, 0, 0)] = 0
    for w in windows:
        wd = int(w["weekday"])
        sh, sm = _parse_hhmm(w["start"])
        eh, em = _parse_hhmm(w["end"])
        edges[(wd, sh, sm)] = 1
        if not (eh == 0 and em == 0):
            edges[(wd, eh, em)] = 0
    return [
        {"weekday": wd, "start_hour": h, "start_minute": m, "charge_mode": mode}
        for (wd, h, m), mode in sorted(edges.items())
    ]


# Minimales Auth + PUT ohne HA-Session (aiohttp direkt)
import aiohttp
import json as _json
import datetime


async def main():
    if not PASSWORD:
        print("FEHLER: Kein Passwort angegeben.")
        print(f"Aufruf: python3 {sys.argv[0]} <host> <password> [--clear]")
        return

    print(f"Verbinde mit http://{HOST} ...")

    async with aiohttp.ClientSession() as session:
        # Auth
        resp = await session.post(
            f"http://{HOST}/api/web-login/token",
            data={
                "grant_type": "password",
                "client_id": "emos",
                "client_secret": "56951025",
                "username": "admin",
                "password": PASSWORD,
            },
        )
        resp.raise_for_status()
        token_data = await resp.json()
        if "error" in token_data:
            print(f"FEHLER: Authentifizierung fehlgeschlagen: {token_data}")
            return
        token = token_data["access_token"]
        print("Authentifizierung OK.")

        headers = {"Authorization": f"Bearer {token}"}

        if CLEAR:
            schedule = [
                {"weekday": wd, "start_hour": 0, "start_minute": 0, "charge_mode": 0}
                for wd in range(7)
            ]
            print("Sende CLEAR-Plan (alle Tage auf AUS) ...")
        else:
            # Test-Fenster: morgen (aktueller Wochentag+1) 03:00–05:00
            tomorrow_wd = (datetime.datetime.now().isoweekday() % 7 + 1) % 7
            days = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"]
            print(f"Setze Test-Fenster: {days[tomorrow_wd]} 03:00–05:00 ...")
            schedule = _build_timebased_schedule([
                {"weekday": tomorrow_wd, "start": "03:00", "end": "05:00"},
            ])

        print("Payload:")
        for e in schedule:
            days = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"]
            on_off = "EIN " if e["charge_mode"] == 1 else "AUS "
            print(f"  {days[e['weekday']]} {e['start_hour']:02d}:{e['start_minute']:02d} -> {on_off}")

        resp = await session.put(
            f"http://{HOST}/api/e-mobility/timebasedCharge",
            headers=headers,
            json=schedule,
        )
        print(f"\nHTTP Status: {resp.status}")
        body = await resp.text()
        if body:
            try:
                print("Response:", _json.dumps(_json.loads(body), indent=2))
            except Exception:
                print("Response:", body)

        if resp.status in (200, 204):
            print("\nErfolgreich gesetzt!")
        else:
            print("\nFEHLER beim Setzen.")


asyncio.run(main())
