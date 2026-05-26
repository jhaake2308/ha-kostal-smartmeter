import logging
import datetime
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from datetime import timedelta
from .const import DOMAIN
from .api import KsemClient
from .modbus_helper import KsemModbusClient
import asyncio

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "number", "select", "switch"]

# --- Zeitbasiertes Laden: Hilfsfunktionen ---

# Wochentag-Konvention: 0=Sonntag, 1=Montag … 6=Samstag
_WEEKDAY_NAME_MAP: dict[str, int] = {
    "montag": 1, "mo": 1,
    "dienstag": 2, "di": 2,
    "mittwoch": 3, "mi": 3,
    "donnerstag": 4, "do": 4,
    "freitag": 5, "fr": 5,
    "samstag": 6, "sa": 6,
    "sonntag": 0, "so": 0,
}


def _coerce_weekday(value) -> int:
    """Akzeptiert Ganzzahlen 0–6 sowie deutsche Wochentagsnamen (auch abgekürzt)."""
    if isinstance(value, int):
        if not 0 <= value <= 6:
            raise vol.Invalid(f"weekday muss zwischen 0 und 6 liegen, nicht {value}")
        return value
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _WEEKDAY_NAME_MAP:
            return _WEEKDAY_NAME_MAP[key]
        raise vol.Invalid(
            f"Unbekannter Wochentag '{value}'. "
            "Erlaubt: Ganzzahl 0–6 oder Wochentagsname "
            "(z. B. Montag, Di, Mittwoch …)"
        )
    raise vol.Invalid(f"weekday muss eine Zahl oder ein Wochentagsname sein, nicht {type(value).__name__}")


# Lademodus-Mapping für zeitbasiertes Laden.
# Bestätigte Werte aus HAR: 0=nicht laden, 1=grid.
# Werte 2 (pv) und 3 (hybrid) sind plausibel, aber noch unbestätigt – bitte testen.
_CHARGE_MODE_INT: dict[str, int] = {
    "grid": 1,
    "pv": 2,
    "hybrid": 3,
}


_WINDOW_SCHEMA = vol.Schema(
    {
        vol.Required("weekday"): _coerce_weekday,
        vol.Required("start"): str,
        vol.Required("end"): str,
        vol.Optional("mode", default="grid"): vol.All(
            str, vol.In(list(_CHARGE_MODE_INT.keys()))
        ),
    }
)

SET_TIMEBASED_SCHEMA = vol.Schema(
    {vol.Required("windows"): vol.All([_WINDOW_SCHEMA], vol.Length(min=1))}
)


def _parse_hhmm(time_str: str) -> tuple[int, int]:
    """Parst 'HH:MM' zu (hour, minute)."""
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1])


def _build_timebased_schedule(windows: list) -> list:
    """Konvertiert Zeitfenster (Mensch-freundlich) in das KSEM-Kantenformat.

    Wochentag (KSEM-Konvention): 1=Montag, 2=Dienstag, 3=Mittwoch, 4=Donnerstag,
    5=Freitag, 6=Samstag, 0=Sonntag.
    Jeder Tag bekommt automatisch eine Default-Kante 00:00=0 (nicht laden).
    Für jedes Fenster wird eine Ein-Kante (start) und, wenn end != 00:00,
    eine Aus-Kante (end) gesetzt. Überschneidende Kanten: letzter Wert gewinnt.
    Fenster über Mitternacht sind nicht unterstützt.
    """
    edges: dict[tuple, int] = {}
    for wd in range(7):
        edges[(wd, 0, 0)] = 0  # Default: nicht laden

    for w in windows:
        wd = int(w["weekday"])
        sh, sm = _parse_hhmm(w["start"])
        eh, em = _parse_hhmm(w["end"])
        mode_int = _CHARGE_MODE_INT.get(w.get("mode", "grid"), 1)
        edges[(wd, sh, sm)] = mode_int  # Ladestart mit gewähltem Modus
        if not (eh == 0 and em == 0):
            # 00:00 als Ende würde die Default-Kante redundant überschreiben
            edges[(wd, eh, em)] = 0  # Ladestop

    return [
        {"weekday": wd, "start_hour": h, "start_minute": m, "charge_mode": mode}
        for (wd, h, m), mode in sorted(edges.items())
    ]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.info("Setup entry %s", entry.entry_id)
    hass.data.setdefault(DOMAIN, {})

    host = entry.data["host"]
    password = entry.data["password"]
    client = KsemClient(hass, host, password)
    modbus_client = KsemModbusClient(host)

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

        # Chargemode: kurze WS-Snapshot-Verbindung (connect → 1 Nachricht → disconnect).
        # Eine dauerhafte WS-Verbindung würde das KSEM als externen Controller sperren
        # und das Laden blockieren – der Snapshot hält nie länger als ~1 Sekunde.
        try:
            chargemode = await asyncio.wait_for(
                client.get_chargemode_snapshot(), timeout=5.0
            )
        except Exception as err:
            _LOGGER.warning("Chargemode-Snapshot fehlgeschlagen: %s", err)
            chargemode = {}

        return {
            "evse": result,
            "phase_usage": phase_usage,
            "energyflow_config": config,
            "evse_state": evse_state,
            "ev_params": ev_params,
            "chargemode": chargemode,
        }

    async def _update_modbus():
        try:
            return await modbus_client.read_all()
        except Exception as err:
            raise UpdateFailed(f"Modbus-Fehler: {err}") from err

    smart_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="ksem_smartmeter",
        update_method=_update_smartmeter,
        update_interval=datetime.timedelta(seconds=30),
    )
    modbus_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="ksem_modbus_all",
        update_method=_update_modbus,
        update_interval=datetime.timedelta(seconds=10),
    )
    wallbox_coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="ksem_wallbox",
        update_method=_update_wallbox,
        update_interval=datetime.timedelta(seconds=60),
    )

    await smart_coordinator.async_refresh()
    await wallbox_coordinator.async_refresh()
    await modbus_coordinator.async_refresh()


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
        "modbus_coordinator": modbus_coordinator,
        "device_info": device_info,
        "serial": serial,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # --- HA-Services für zeitbasiertes Laden ---
    # Wird nur einmal registriert (erster Entry gewinnt; typischerweise ein KSEM je Installation).

    async def _handle_set_timebased_charge(call):
        """Service ksem.set_timebased_charge – setzt Ladefenster im KSEM."""
        data = next(iter(hass.data.get(DOMAIN, {}).values()), None)
        if not data:
            _LOGGER.error("set_timebased_charge: keine aktive KSEM-Integration gefunden")
            return
        schedule = _build_timebased_schedule(call.data["windows"])
        _LOGGER.info("set_timebased_charge: sende %d Kanten ans KSEM", len(schedule))
        await data["client"].set_timebased_charge(schedule)

    async def _handle_clear_timebased_charge(call):
        """Service ksem.clear_timebased_charge – setzt Ladeplan auf 'alles aus' und wechselt zurück auf Lock-Mode."""
        data = next(iter(hass.data.get(DOMAIN, {}).values()), None)
        if not data:
            _LOGGER.error("clear_timebased_charge: keine aktive KSEM-Integration gefunden")
            return
        _LOGGER.info("clear_timebased_charge: setze kompletten Zeitplan zurück")
        await data["client"].clear_timebased_charge()
        # Explizit auf Lock-Mode schalten, damit HA den Zustand korrekt widerspiegelt.
        # Ohne diesen Aufruf bleibt die Wallbox intern im Time-Mode (auch wenn alle
        # Slots auf 0 stehen), und HA zeigt weiterhin den alten Modus.
        await data["client"].set_charge_mode(mode="lock")

    if not hass.services.has_service(DOMAIN, "set_timebased_charge"):
        hass.services.async_register(
            DOMAIN,
            "set_timebased_charge",
            _handle_set_timebased_charge,
            schema=SET_TIMEBASED_SCHEMA,
        )
        hass.services.async_register(
            DOMAIN,
            "clear_timebased_charge",
            _handle_clear_timebased_charge,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = all(
        [
            await hass.config_entries.async_forward_entry_unload(entry, platform)
            for platform in PLATFORMS
        ]
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
