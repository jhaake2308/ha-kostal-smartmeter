import logging
import datetime
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from datetime import timedelta
from .const import DOMAIN
from .api import KsemClient
from homeassistant.helpers.dispatcher import async_dispatcher_send
from .const import SIGNAL_CHARGEMODE_UPDATE
import asyncio

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "number", "select", "switch"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.info("Setup entry %s", entry.entry_id)
    hass.data.setdefault(DOMAIN, {})

    host = entry.data["host"]
    password = entry.data["password"]
    client = KsemClient(hass, host, password)

    # Geteiltes Dict, das vom WS-Task laufend befüllt wird und von
    # select.py / number.py gelesen werden kann.
    chargemode_data: dict = {}

    async def _update_smartmeter():
        try:
            return await client.get_device_status()
        except Exception as err:
            raise UpdateFailed(f"Smartmeter-Fehler: {err}")

    async def _update_wallbox():
        """Robuster WB-Update:
        - /evselist ist 'kritisch' (ohne Liste -> UpdateFailed)
        - /details ist 'best effort' (Fehler/Timeout -> WB marked unavailable, kein UpdateFailed)
        """
        try:
            evse_list = await client.get_evse_list()
        except Exception as err:
            raise UpdateFailed(
                f"EVSE-Liste konnte nicht geladen werden: {err}"
            ) from err

        result = []
        for evse in evse_list or []:
            # Kopie und Basisfelder
            wb = dict(evse)
            uuid = wb.get("uuid")
            state = (wb.get("state") or "").lower()

            # Wenn evselist bereits einen Kommunikationsfehler signalisiert, Details überspringen
            if "commerror" in state or "error" in state or "offline" in state:
                wb["available"] = False
                wb["details"] = None
                result.append(wb)
                continue

            # Details mit Timeout 'best effort'
            try:
                details = await asyncio.wait_for(
                    client.get_evse_details(uuid), timeout=5.0
                )
                wb["available"] = True
                wb["details"] = details
                wb.update(details or {})
            except Exception as err:
                _LOGGER.warning(
                    "Wallbox-Details für %s nicht erreichbar: %s", uuid, err
                )
                wb["available"] = False
                wb["details"] = None
            result.append(wb)

        # optionale Zusatzinfos sind nicht kritisch
        try:
            res = await client.get_phase_switching()
            phase_usage = res.get("phase_usage", 0)
        except Exception as err:
            _LOGGER.warning("Phasenumschaltung konnte nicht geladen werden: %s", err)
            phase_usage = 0
        try:
            config = await client.get_energyflow_config()
        except Exception as err:
            _LOGGER.warning(
                "Energiefluss-Konfiguration konnte nicht geladen werden: %s", err
            )
            config = {}
        try:
            evse_state = await client.get_evse_state()
        except Exception as err:
            _LOGGER.warning("EVSE-Status konnte nicht geladen werden: %s", err)
            evse_state = {}
        try:
            ev_params = await client.get_ev_parameters()
        except Exception as err:
            _LOGGER.warning("EV-Parameter konnten nicht geladen werden: %s", err)
            ev_params = {}

        return {
            "evse": result,
            "phase_usage": phase_usage,
            "energyflow_config": config,
            "evse_state": evse_state,
            "ev_params": ev_params,
        }

    smart_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="ksem_smartmeter",
        update_method=_update_smartmeter,
        update_interval=datetime.timedelta(seconds=30),
    )
    wallbox_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="ksem_wallbox",
        update_method=_update_wallbox,
        update_interval=datetime.timedelta(seconds=10),
    )

    await smart_coordinator.async_refresh()
    await wallbox_coordinator.async_refresh()

    # WebSocket-Task: lauscht auf Lademodus-Änderungen und befüllt chargemode_data
    async def _on_chargemode(msg: dict):
        """Callback für jede eingehende WS-Nachricht."""
        chargemode_data.update(msg)
        async_dispatcher_send(
            hass, SIGNAL_CHARGEMODE_UPDATE.format(entry.entry_id)
        )

    async def _ws_listener_loop():
        delay = 5
        while True:
            try:
                await client.async_listen_ws(_on_chargemode)
                # Sauberes Ende (CLOSE-Frame) → kurz warten, dann neu verbinden
                _LOGGER.info("WS-Verbindung sauber geschlossen – reconnect in %ss", delay)
            except asyncio.CancelledError:
                _LOGGER.debug("WS-Task abgebrochen")
                return
            except Exception as err:
                _LOGGER.warning(
                    "WS-Verbindung unterbrochen: %s – reconnect in %ss", err, delay
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)  # 5s → 10s → 20s → … max 5min

    ws_task = hass.async_create_background_task(
        _ws_listener_loop(), "ksem_chargemode_ws"
    )

    info = await client.get_device_info()
    mac = info.get("Mac")
    serial = info.get("Serial")
    model = info.get("ProductName")
    fw = info.get("FirmwareVersion")
    hw = info.get("DeviceType")

    device_info = DeviceInfo(
        identifiers={(DOMAIN, serial)},
        connections={(CONNECTION_NETWORK_MAC, mac)},
        name="Smartmeter",
        manufacturer="Kostal",
        model=model,
        hw_version=hw,
        sw_version=fw,
        configuration_url=f"http://{host}",
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "smart_coordinator": smart_coordinator,
        "wallbox_coordinator": wallbox_coordinator,
        "device_info": device_info,
        "serial": serial,
        "chargemode_data": chargemode_data,
        "ws_task": ws_task,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # WS-Task sauber beenden
    ws_task = hass.data[DOMAIN].get(entry.entry_id, {}).get("ws_task")
    if ws_task:
        ws_task.cancel()

    unload_ok = all(
        [
            await hass.config_entries.async_forward_entry_unload(entry, platform)
            for platform in PLATFORMS
        ]
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
