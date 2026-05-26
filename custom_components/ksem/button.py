"""Button-Entities für KSEM/ENECTOR Wallbox."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .helper import first_evse_from_coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    device_info: DeviceInfo = data["device_info"]

    # Wallbox-DeviceInfo unabhängig ermitteln (kein Race-Condition-Risiko
    # gegenüber sensor.py, das es ebenfalls setzt).
    target_device = device_info
    wallbox_coordinator = data.get("wallbox_coordinator")
    if wallbox_coordinator:
        wb = first_evse_from_coordinator(wallbox_coordinator)
        if wb:
            uuid = wb.get("uuid")
            label = wb.get("label", "Wallbox")
            model = wb.get("model", "")
            details = wb.get("details") or {}
            wb_serial = details.get("serial", uuid)
            version = details.get("version", "")
            target_device = DeviceInfo(
                identifiers={(DOMAIN, f"wallbox-{uuid}")},
                name=f"Wallbox {label}",
                model=model,
                serial_number=wb_serial,
                sw_version=version,
                manufacturer="Kostal",
            )

    async_add_entities(
        [
            KsemCheapestChargeButton(hass, entry.entry_id, target_device),
            KsemClearChargeButton(hass, entry.entry_id, target_device),
        ]
    )


class KsemCheapestChargeButton(ButtonEntity):
    """Schaltfläche: günstigste Ladefenster aus evcc-Preisdaten setzen."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, device_info: DeviceInfo
    ) -> None:
        self.hass = hass
        self._attr_name = "Günstig laden planen"
        self._attr_unique_id = f"ksem_{entry_id}_cheapest_charge"
        self._attr_device_info = device_info
        self._attr_icon = "mdi:car-clock"

    async def async_press(self) -> None:
        await self.hass.services.async_call(
            DOMAIN, "set_cheapest_charge_windows", {}
        )


class KsemClearChargeButton(ButtonEntity):
    """Schaltfläche: Ladeplan zurücksetzen und Lock-Mode aktivieren."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, device_info: DeviceInfo
    ) -> None:
        self.hass = hass
        self._attr_name = "Zeitplan löschen"
        self._attr_unique_id = f"ksem_{entry_id}_clear_charge"
        self._attr_device_info = device_info
        self._attr_icon = "mdi:car-off"

    async def async_press(self) -> None:
        await self.hass.services.async_call(DOMAIN, "clear_timebased_charge", {})
