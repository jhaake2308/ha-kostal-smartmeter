import logging
import datetime
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from datetime import timedelta
from .const import DOMAIN, SIGNAL_SCHEDULE_UPDATED
from .api import KsemClient
from .modbus_helper import KsemModbusClient
import asyncio

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "number", "select", "switch", "button"]

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
    """Parst 'HH:MM' zu (hour, minute).

    Wirft ValueError wenn Minuten != 0, da der KSEM nur volle Stunden unterstützt.
    Gilt auch für automatisierte Aufrufe (z. B. Strompreis-Automatisierung).
    """
    parts = time_str.split(":")
    hour = int(parts[0])
    minute = int(parts[1])
    if minute != 0:
        raise ValueError(
            f"Zeitangabe '{time_str}': Der KSEM unterstützt nur volle Stunden (z. B. '04:00'). "
            f"Minuten werden vom Gerät ignoriert und müssen 00 sein."
        )
    return hour, minute


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


# ---------------------------------------------------------------------------
# evcc-Hilfsfunktionen (Strompreis-Optimierung)
# ---------------------------------------------------------------------------

_WEEKDAY_KSEM_NAME = {0: "So", 1: "Mo", 2: "Di", 3: "Mi", 4: "Do", 5: "Fr", 6: "Sa"}


async def _fetch_evcc_tariff(hass: HomeAssistant, evcc_url: str) -> list:
    """Ruft /api/tariff/grid von evcc ab. Gibt Liste von Rate-Dicts zurück."""
    session = async_get_clientsession(hass)
    url = f"{evcc_url}/api/tariff/grid"
    try:
        async with asyncio.timeout(10.0):
            resp = await session.get(url)
        resp.raise_for_status()
        payload = await resp.json()
        return payload.get("result", {}).get("rates", [])
    except Exception as err:
        raise RuntimeError(f"evcc Tariff-Abruf fehlgeschlagen ({url}): {err}") from err


def _select_cheapest_slots(
    rates: list,
    search_from_h: int,
    search_until_h: int,
    hours_needed: int,
) -> list:
    """Wählt die günstigsten N Stunden aus den evcc-Rates für das nächste Nachtfenster."""
    from datetime import datetime, timedelta, timezone
    from homeassistant.util import dt as dt_util

    now = dt_util.now()
    today = now.date()

    # Nächstes Auftreten von search_from_h berechnen (ggf. morgen)
    window_start = datetime(
        today.year, today.month, today.day, search_from_h, 0, 0, tzinfo=now.tzinfo
    )
    if window_start <= now:
        window_start += timedelta(days=1)

    # Fensterende: Mitternacht überspannen falls search_until_h <= search_from_h
    if search_until_h <= search_from_h:
        window_end = window_start + timedelta(
            hours=(24 - search_from_h + search_until_h)
        )
    else:
        window_end = window_start + timedelta(
            hours=(search_until_h - search_from_h)
        )

    window_start_utc = window_start.astimezone(timezone.utc)
    window_end_utc = window_end.astimezone(timezone.utc)

    candidates = []
    for rate in rates:
        try:
            start_s = (rate.get("start") or "").replace("Z", "+00:00")
            end_s = (rate.get("end") or "").replace("Z", "+00:00")
            price = rate.get("price")
            if price is None:
                continue
            start_dt = datetime.fromisoformat(start_s)
            end_dt = datetime.fromisoformat(end_s)
        except (KeyError, ValueError, TypeError):
            continue
        if start_dt >= window_start_utc and end_dt <= window_end_utc:
            candidates.append({"start": start_dt, "end": end_dt, "price": float(price)})

    if not candidates:
        return []

    candidates.sort(key=lambda x: x["price"])
    return candidates[:hours_needed]


def _slots_to_windows(slots: list, mode: str) -> tuple[list, list]:
    """Konvertiert evcc-Slots zu ksem-windows-Format.

    Returns: (windows_list, human_readable_list)
    """
    from homeassistant.util import dt as dt_util

    windows: list = []
    readable: list = []
    for slot in slots:
        local_start = dt_util.as_local(slot["start"])
        local_end = dt_util.as_local(slot["end"])
        # Python weekday: 0=Mo … 6=So  →  KSEM: 0=So, 1=Mo … 6=Sa
        ksem_wd = (local_start.weekday() + 1) % 7
        start_str = f"{local_start.hour:02d}:00"
        end_str = f"{local_end.hour:02d}:00"
        windows.append(
            {"weekday": ksem_wd, "start": start_str, "end": end_str, "mode": mode}
        )
        day_name = _WEEKDAY_KSEM_NAME.get(ksem_wd, str(ksem_wd))
        readable.append(
            f"{day_name} {start_str}\u2013{end_str} ({mode}, {slot['price']:.4f} \u20ac/kWh)"
        )
    return windows, readable


def _windows_to_readable(windows: list) -> list:
    """Erzeugt lesbare Kurzdarstellung der Ladefenster."""
    result = []
    for w in windows:
        wd = int(w.get("weekday", 0))
        day = _WEEKDAY_KSEM_NAME.get(wd, str(wd))
        result.append(
            f"{day} {w['start']}\u2013{w['end']} ({w.get('mode', 'grid')})"
        )
    return result


SET_CHEAPEST_WINDOWS_SCHEMA = vol.Schema(
    {
        vol.Optional("evcc_url"): str,
        vol.Optional("hours_needed"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=8)
        ),
        vol.Optional("search_from"): str,
        vol.Optional("search_until"): str,
        vol.Optional("mode"): vol.All(
            str, vol.In(list(_CHARGE_MODE_INT.keys()))
        ),
    }
)


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
        windows = call.data["windows"]
        schedule = _build_timebased_schedule(windows)
        _LOGGER.info("set_timebased_charge: sende %d Kanten ans KSEM", len(schedule))
        await data["client"].set_timebased_charge(schedule)
        async_dispatcher_send(
            hass, SIGNAL_SCHEDULE_UPDATED, windows, _windows_to_readable(windows)
        )

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
        async_dispatcher_send(hass, SIGNAL_SCHEDULE_UPDATED, None, None)

    async def _handle_set_cheapest_charge_windows(call):
        """Service ksem.set_cheapest_charge_windows – wählt günstigste Slots via evcc."""
        int_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not int_data:
            _LOGGER.error("set_cheapest_charge_windows: keine aktive KSEM-Integration gefunden")
            return

        evcc_url = (
            (call.data.get("evcc_url") or entry.data.get("evcc_url") or "")
            .strip()
            .rstrip("/")
        )
        if not evcc_url:
            _LOGGER.warning(
                "set_cheapest_charge_windows: keine evcc_url konfiguriert – "
                "bitte in der KSEM-Integration einrichten oder als Service-Parameter übergeben"
            )
            return

        hours_needed = int(
            call.data.get("hours_needed") or entry.data.get("evcc_hours_needed") or 3
        )
        search_from = (
            call.data.get("search_from") or entry.data.get("evcc_search_from") or "22:00"
        )
        search_until = (
            call.data.get("search_until") or entry.data.get("evcc_search_until") or "06:00"
        )
        mode = call.data.get("mode") or entry.data.get("evcc_mode") or "grid"

        search_from_h = int(search_from.split(":")[0])
        search_until_h = int(search_until.split(":")[0])

        try:
            rates = await _fetch_evcc_tariff(hass, evcc_url)
        except RuntimeError as err:
            _LOGGER.error("set_cheapest_charge_windows: %s", err)
            return

        slots = _select_cheapest_slots(rates, search_from_h, search_until_h, hours_needed)
        if not slots:
            _LOGGER.warning(
                "set_cheapest_charge_windows: Keine Preisdaten für die kommende Nacht "
                "im Fenster %s–%s verfügbar. Kein Zeitplan gesetzt.",
                search_from,
                search_until,
            )
            return

        windows, readable = _slots_to_windows(slots, mode)
        _LOGGER.info(
            "set_cheapest_charge_windows: setze %d Ladefenster: %s",
            len(windows),
            ", ".join(readable),
        )

        schedule = _build_timebased_schedule(windows)
        await int_data["client"].set_timebased_charge(schedule)
        async_dispatcher_send(hass, SIGNAL_SCHEDULE_UPDATED, windows, readable)

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
        hass.services.async_register(
            DOMAIN,
            "set_cheapest_charge_windows",
            _handle_set_cheapest_charge_windows,
            schema=SET_CHEAPEST_WINDOWS_SCHEMA,
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
