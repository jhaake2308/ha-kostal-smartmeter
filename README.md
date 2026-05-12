# Kostal KSEM Home Assistant Integration

This is a custom component for Home Assistant to integrate the Kostal Smart Energy Meter (KSEM) and a connected ENECTOR wallbox.

## Status (v1.0.0-alpha.4)

This version uses a combination of REST polling and a persistent WebSocket connection for real-time push updates of the charging mode.

## Features

*   Sensors for KSEM device status (CPU, RAM).
*   Sensors for live charging data from the wallbox (Available Power, EV Current, Phases, etc.).
*   Control for the charging mode (Lock, Power, Solar Pure, Solar Plus) — **live push from device**.
*   Control for Min PV Power and Min Charging Power quotas — **live push from device**.
*   Switch to control battery usage during Solar Pure mode.
*   Selector for phase switching (1 Phase / 3 Phases / Automatic).

## Installation

1.  Copy the `custom_components/ksem` directory into your Home Assistant `<config>/custom_components/` folder.
2.  Restart Home Assistant.

## Configuration

1.  Go to **Settings > Devices & Services**.
2.  Click **Add Integration** and search for **Kostal Smartmeter**.
3.  Enter the IP-Address of your KSEM and your device password.
