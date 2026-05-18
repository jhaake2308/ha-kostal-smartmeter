#!/usr/bin/env python3
"""
Schnelltest: Liest alle Register aus modbus_map.py direkt vom KSEM-Gerät.
Aufruf: python3 test_modbus.py <host> [port] [unit_id]
Beispiel: python3 test_modbus.py ksem.haake.io
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "custom_components", "ksem"))

from modbus_map import SENSOR_DEFINITIONS
from modbus_helper import KsemModbusClient

HOST = sys.argv[1] if len(sys.argv) > 1 else "ksem.haake.io"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 502
UNIT = int(sys.argv[3]) if len(sys.argv) > 3 else 1


async def main():
    client = KsemModbusClient(HOST, port=PORT, unit_id=UNIT)
    print(f"Verbinde mit {HOST}:{PORT} (Unit {UNIT}) ...")
    try:
        await client.connect()
    except Exception as e:
        print(f"FEHLER beim Verbinden: {e}")
        return

    print("Verbunden. Lese Register ...\n")
    data = await client.read_all()
    await client.disconnect()

    if not data:
        print("Keine Daten empfangen – prüfe Host/Port/Unit-ID.")
        return

    # Geordnet nach Register-Adresse ausgeben
    addr_map = {spec["name"]: addr for addr, spec in SENSOR_DEFINITIONS.items()}
    print(f"{'Register':>6}  {'Name':<45} {'Wert':>12}  Einheit")
    print("-" * 75)
    for name, val in data.items():
        addr = addr_map.get(name, "?")
        spec = next((s for s in SENSOR_DEFINITIONS.values() if s["name"] == name), {})
        unit = spec.get("unit", "")
        print(f"{addr:>6}  {name:<45} {str(val):>12}  {unit}")

    print(f"\n{len(data)}/{len(SENSOR_DEFINITIONS)} Register erfolgreich gelesen.")


asyncio.run(main())
