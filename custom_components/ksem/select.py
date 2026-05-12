import logging
from typing import Optional

from homeassistant.core import callback
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SIGNAL_CHARGEMODE_UPDATE
from .helper import first_evse_from_coordinator  # Single-WB Helfer

_LOGGER = logging.getLogger(__name__)

MODE_MAP = {
    "lock": "Lock Mode",
    "grid": "Power Mode",
    "pv": "Solar Pure Mode",
    "hybrid": "Solar Plus Mode",
}
REVERSE_MODE_MAP = {v: k for k, v in MODE_MAP.items()}

PHASE_MAP = {0: "3 Phasen", 1: "1 Phase", 2: "Automatisch"}
REVERSE_PHASE_MAP = {v: k for k, v in PHASE_MAP.items()}


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    coordinator = data.get("wallbox_coordinator")  # kann None sein

    entities = []

    # --- Nur anlegen, wenn eine Wallbox erkennbar ist (läuft aber auch ohne WB) ---
    wb = first_evse_from_coordinator(coordinator) if coordinator else None
    if wb:
        entities.append(
            KsemChargeModeSelect(
                hass=hass,
                entry_id=entry.entry_id,
                client=client,
                coordinator=coordinator,
            )
        )
        entities.append(
            KsemPhaseSwitchSelect(
                hass=hass,
                entry_id=entry.entry_id,
                coordinator=coordinator,
                client=client,
            )
        )

    if entities:
        async_add_entities(entities, update_before_add=False)

    # --- Später automatisch nachziehen, falls beim Start noch keine WB vorhanden war ---
    if coordinator and not wb:

        async def _maybe_add_single_wb():
            wb_now = first_evse_from_coordinator(coordinator)
            if not wb_now:
                return
            new_entities = [
                KsemChargeModeSelect(
                    hass=hass,
                    entry_id=entry.entry_id,
                    client=client,
                    coordinator=coordinator,
                ),
                KsemPhaseSwitchSelect(
                    hass=hass,
                    entry_id=entry.entry_id,
                    coordinator=coordinator,
                    client=client,
                ),
            ]
            async_add_entities(new_entities, update_before_add=False)

        def _wb_listener():
            hass.async_create_task(_maybe_add_single_wb())

        coordinator.async_add_listener(_wb_listener)
        await _maybe_add_single_wb()


class KsemPhaseSwitchSelect(CoordinatorEntity, SelectEntity):
    """Phasenumschaltung (nur eine WB)."""

    def __init__(self, hass, entry_id, coordinator, client):
        super().__init__(coordinator)
        self._hass = hass
        self._entry_id = entry_id
        self._client = client
        self._attr_name = "Phasenumschaltung"
        self._attr_unique_id = f"{entry_id}_ksem_phase_switch"
        self._attr_options = list(PHASE_MAP.values())

        wb = first_evse_from_coordinator(coordinator)
        if wb:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"wallbox-{wb.get('uuid')}")},
                name=f"Wallbox {wb.get('label', 'Wallbox')}",
                model=wb.get("model", ""),
                manufacturer="Kostal",
            )

    @property
    def current_option(self) -> str | None:
        phase_usage = (self.coordinator.data or {}).get("phase_usage")
        return PHASE_MAP.get(phase_usage)

    async def async_select_option(self, option: str):
        if option not in REVERSE_PHASE_MAP:
            _LOGGER.warning("Ungültige Auswahl: %s", option)
            return
        new_value = REVERSE_PHASE_MAP[option]
        await self._client.set_phase_switching(new_value)
        await self.coordinator.async_request_refresh()


class KsemChargeModeSelect(CoordinatorEntity, SelectEntity):
    """Auswahl des Lademodus für die Wallbox."""

    def __init__(self, hass, entry_id, client, coordinator):
        super().__init__(coordinator)
        self._hass = hass
        self._entry_id = entry_id
        self._client = client
        self._attr_name = "Lademodus"
        self._attr_unique_id = f"{entry_id}_ksem_charge_mode"
        self._attr_options = list(MODE_MAP.values())

        # Device-Info vom Coordinator holen (sobald verfügbar)
        wb = first_evse_from_coordinator(self.coordinator)
        if wb:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"wallbox-{wb.get('uuid')}")},
                name=f"Wallbox {wb.get('label', 'Wallbox')}",
                model=wb.get("model", ""),
                manufacturer="Kostal",
            )

    @property
    def current_option(self) -> Optional[str]:
        """Aktuellen Lademodus aus den via WebSocket empfangenen Daten lesen."""
        chargemode = (
            self._hass.data.get(DOMAIN, {})
            .get(self._entry_id, {})
            .get("chargemode_data", {})
        )
        mode = chargemode.get("mode")
        return MODE_MAP.get(mode)

    async def async_added_to_hass(self) -> None:
        """Dispatcher-Listener registrieren, sobald Entity in HA eingebunden ist."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CHARGEMODE_UPDATE.format(self._entry_id),
                self._handle_chargemode_push,
            )
        )

    @callback
    def _handle_chargemode_push(self) -> None:
        """Wird direkt vom WS-Push aufgerufen – HA-State sofort aktualisieren."""
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        mode = REVERSE_MODE_MAP.get(option)
        if mode is None:
            _LOGGER.error("Unbekannter Lademodus: %s", option)
            return

        try:
            await self._client.set_charge_mode(mode=mode)
            # After setting, request a refresh to update all related states
            await self.coordinator.async_request_refresh()
        except Exception as e:
            _LOGGER.error("Fehler beim Setzen des Lademodus: %s", e)

