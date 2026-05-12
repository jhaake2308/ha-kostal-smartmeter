# Kostal KSEM Home Assistant Integration

This is a custom component for Home Assistant to integrate the Kostal Smart Energy Meter (KSEM) and a connected ENECTOR wallbox.

## Status (Version 1.x)

This version is a complete rewrite to support the new KSEM firmware and its updated, HTTP-based API. The integration is currently in active development.

## Features

*   Sensors for KSEM device status (CPU, RAM).
*   Sensors for live charging data from the wallbox (Current, Phases, etc.).
*   Control for the charging mode (e.g., Solar Plus, Power Mode).
*   Switch to control battery usage.
*   Selector for phase switching.

## Installation

1.  Copy the `custom_components/ksem` directory into your Home Assistant `<config>/custom_components/` folder.
2.  Restart Home Assistant.

## Configuration

1.  Go to **Settings > Devices & Services**.
2.  Click **Add Integration** and search for **Kostal Smartmeter**.
3.  Enter the IP-Address of your KSEM and your device password.
