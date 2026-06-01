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
from homeassistant.components.persistent_notification import (
    async_create as pn_create,
    async_dismiss as pn_dismiss,
)
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


# Robuste Zeitfenster-Parsing- und Validierungslogik (ersetzt helper.py-Variante)
def _build_timebased_schedule(windows: list, logger=None) -> list:
    """
    Konvertiert und validiert Zeitfenster (Mensch-freundlich) in das KSEM-Kantenformat.
    Erkennt 15-Minuten- und 1-Stunden-Takte, loggt/skippt fehlerhafte Fenster, unterstützt Überläufe über Mitternacht.
    """
    if logger is None:
        logger = _LOGGER
    edges: dict[tuple, int] = {}
    for wd in range(7):
        edges[(wd, 0, 0)] = 0  # Default: nicht laden

    for w in windows:
        try:
            wd = int(w["weekday"])
            sh, sm = map(int, w["start"].split(":"))
            eh, em = map(int, w["end"].split(":"))
            mode_int = _CHARGE_MODE_INT.get(w.get("mode", "grid"), 1)
            start = datetime.time(sh, sm)
            end = datetime.time(eh, em)
            # Takt berechnen (z.B. 15min, 1h)
            delta = (datetime.datetime.combine(datetime.date.today(), end) -
                     datetime.datetime.combine(datetime.date.today(), start))
            if delta.total_seconds() < 0:
                # Über Mitternacht
                delta = (datetime.datetime.combine(datetime.date.today(), datetime.time(23,59,59)) -
                         datetime.datetime.combine(datetime.date.today(), start)) + \
                        (datetime.datetime.combine(datetime.date.today(), end) -
                         datetime.datetime.combine(datetime.date.today(), datetime.time(0,0))) + \
                        datetime.timedelta(seconds=1)
            minutes = int(delta.total_seconds() // 60)
            if minutes % 15 == 0:
                takt = 15
            elif minutes % 60 == 0:
                takt = 60
            else:
                takt = None
            if takt is None:
                logger.warning(f"Ungültiges Zeitfenster (kein 15min/1h-Takt): {w}")
                continue
            edges[(wd, sh, sm)] = mode_int  # Ladestart mit gewähltem Modus
            if not (eh == 0 and em == 0):
                edges[(wd, eh, em)] = 0  # Ladestop
        except Exception as e:
            logger.error(f"Fehler beim Parsen/Validieren von Zeitfenster {w}: {e}")
            continue

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
        # Unterstütze beide Formate: {"rates": [...]} und {"result": {"rates": [...]}}
        return payload.get("result", {}).get("rates", []) or payload.get("rates", [])
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

    candidates: list = []
    _LOGGER.info("_select_cheapest_slots: Eingabeparameter: search_from_h=%s, search_until_h=%s, hours_needed=%s", search_from_h, search_until_h, hours_needed)
    _LOGGER.info("_select_cheapest_slots: Anzahl geladener Preisdaten: %d", len(rates))
    filtered = 0
    skipped = 0
    for rate in rates:
        try:
            start_s = (rate.get("start") or "").replace("Z", "+00:00")
            end_s = (rate.get("end") or "").replace("Z", "+00:00")
            price = rate.get("value")
            if price is None:
                price = rate.get("price")
            if price is None:
                skipped += 1
                _LOGGER.warning("_select_cheapest_slots: Preis fehlt in Rate: %s", rate)
                continue
            start_dt = datetime.fromisoformat(start_s)
            end_dt = datetime.fromisoformat(end_s)
        except (KeyError, ValueError, TypeError) as e:
            skipped += 1
            _LOGGER.warning("_select_cheapest_slots: Fehler beim Parsen einer Rate (%s): %s", rate, e)
            continue
        if start_dt >= window_start_utc and end_dt <= window_end_utc:
            candidates.append({"start": start_dt, "end": end_dt, "price": float(price)})
            filtered += 1
    _LOGGER.info("_select_cheapest_slots: %d Rates übersprungen, %d Kandidaten im Zeitfenster gefunden", skipped, filtered)
    if not candidates:
        _LOGGER.warning("_select_cheapest_slots: Keine Kandidaten im Zeitfenster gefunden. (Preisdaten: %d, übersprungen: %d)", len(rates), skipped)
        return []

    # Granularität der Eingabedaten ermitteln und loggen
    from collections import defaultdict
    sample_min = int((candidates[0]["end"] - candidates[0]["start"]).total_seconds() // 60)
    _LOGGER.info(
        "_select_cheapest_slots: Erkannte Datengranularität: %d Minuten/Slot (%d Kandidaten)",
        sample_min,
        len(candidates),
    )

    # Gruppiere nach voller Stunde – duration-gewichtet, korrekt für 15-min- UND 60-min-Anbieter.
    # Ein 60-min-Slot (1 Eintrag) und vier 15-min-Slots (4 Einträge à 15 min) werden
    # identisch behandelt: Preis × Dauer / Gesamtdauer der Stunde.
    hour_buckets: dict = defaultdict(lambda: {"price_min": 0.0, "total_min": 0})
    for rate in candidates:
        duration_min = max(1, int((rate["end"] - rate["start"]).total_seconds() // 60))
        hour = rate["start"].replace(minute=0, second=0, microsecond=0)
        hour_buckets[hour]["price_min"] += rate["price"] * duration_min
        hour_buckets[hour]["total_min"] += duration_min

    # Gewichteten Durchschnittspreis pro Stunde berechnen und sortieren
    hour_avg = [
        (hour, data["price_min"] / data["total_min"])
        for hour, data in hour_buckets.items()
        if data["total_min"] > 0
    ]
    hour_avg.sort(key=lambda x: x[1])
    best_hours = [h for h, _ in hour_avg[:hours_needed]]

    slots = []
    for hour in best_hours:
        data = hour_buckets[hour]
        slots.append({
            "start": hour,
            "end": hour + timedelta(hours=1),
            "price": data["price_min"] / data["total_min"],
        })
    if len(slots) < hours_needed:
        _LOGGER.info(
            "_select_cheapest_slots: Zu wenige Stundenblöcke (%d) für benötigte Stunden (%d)",
            len(slots),
            hours_needed,
        )
    return slots


def _slots_to_windows(slots: list, mode: str) -> tuple[list, list]:
    """Konvertiert evcc-Slots zu ksem-windows-Format.

    Returns: (windows_list, human_readable_list)
    """
    from homeassistant.util import dt as dt_util

    # 1. Slots nach Tag gruppieren
    from collections import defaultdict
    slots_by_day = defaultdict(list)
    for slot in slots:
        local_start = dt_util.as_local(slot["start"])
        local_end = dt_util.as_local(slot["end"])
        ksem_wd = (local_start.weekday() + 1) % 7
        slots_by_day[ksem_wd].append((local_start, local_end, slot["price"]))

    windows = []
    readable = []
    for wd, slotlist in slots_by_day.items():
        # 2. Slots sortieren und zu Stundenblöcken zusammenfassen
        slotlist.sort()
        hour_blocks = []
        block_start = None
        block_end = None
        block_prices = []
        for start, end, price in slotlist:
            # Runde Start auf volle Stunde ab, Ende auf volle Stunde auf
            start_hour = start.replace(minute=0, second=0, microsecond=0)
            if end.minute > 0 or end.second > 0 or end.microsecond > 0:
                end_hour = (end + datetime.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            else:
                end_hour = end
            # Starte neuen Block oder erweitere
            if block_start is None:
                block_start = start_hour
                block_end = end_hour
                block_prices = [price]
            elif start_hour <= block_end:
                # Überlappend/zusammenhängend
                block_end = max(block_end, end_hour)
                block_prices.append(price)
            else:
                hour_blocks.append((block_start, block_end, block_prices))
                block_start = start_hour
                block_end = end_hour
                block_prices = [price]
        if block_start is not None:
            hour_blocks.append((block_start, block_end, block_prices))

        for block_start, block_end, prices in hour_blocks:
            if block_end <= block_start:
                _LOGGER.warning("Verworfenes Zeitfenster (Ende <= Start): %s–%s", block_start, block_end)
                continue
            start_str = f"{block_start.hour:02d}:00"
            end_str = f"{block_end.hour:02d}:00"
            windows.append({"weekday": wd, "start": start_str, "end": end_str, "mode": mode})
            day_name = _WEEKDAY_KSEM_NAME.get(wd, str(wd))
            avg_price = sum(prices) / len(prices) if prices else 0.0
            readable.append(f"{day_name} {start_str}\u2013{end_str} ({mode}, {avg_price:.4f} \u20ac/kWh)")
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
        int_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not int_data:
            _LOGGER.error("set_timebased_charge: keine aktive KSEM-Integration gefunden")
            return
        windows = call.data["windows"]
        schedule = _build_timebased_schedule(windows)
        if not schedule:
            _LOGGER.warning("set_timebased_charge: Kein Zeitfenster generiert – Time Mode wird NICHT aktiviert!")
            return
        readable = _windows_to_readable(windows)
        await int_data["client"].set_timebased_charge(schedule)
        for r in readable:
            _LOGGER.info("KSEM Ladefenster gesetzt: %s", r)
        pn_create(
            hass,
            message="\n".join(readable),
            title="KSEM: Ladeplan gesetzt",
            notification_id="ksem_schedule",
        )
        async_dispatcher_send(hass, SIGNAL_SCHEDULE_UPDATED, windows, readable)

    async def _handle_clear_timebased_charge(call):
        """Service ksem.clear_timebased_charge – setzt Ladeplan auf 'alles aus' und wechselt zurück auf Lock-Mode."""
        int_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not int_data:
            _LOGGER.error("clear_timebased_charge: keine aktive KSEM-Integration gefunden")
            return
        _LOGGER.info("clear_timebased_charge: setze kompletten Zeitplan zurück")
        await int_data["client"].clear_timebased_charge()
        # Explizit auf Lock-Mode schalten, damit HA den Zustand korrekt widerspiegelt.
        # Ohne diesen Aufruf bleibt die Wallbox intern im Time-Mode (auch wenn alle
        # Slots auf 0 stehen), und HA zeigt weiterhin den alten Modus.
        await int_data["client"].set_charge_mode(mode="lock")
        pn_dismiss(hass, notification_id="ksem_schedule")
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
                "set_cheapest_charge_windows: Kein Zeitfenster gefunden. Time Mode wird NICHT aktiviert! Details siehe vorherige Debug-/Info-Logs (_select_cheapest_slots). "
                "Parameter: search_from=%s, search_until=%s, hours_needed=%s, evcc_url=%s, mode=%s, Preisdaten=%d",
                search_from,
                search_until,
                hours_needed,
                evcc_url,
                mode,
                len(rates),
            )
            return

        windows, readable = _slots_to_windows(slots, mode)
        _LOGGER.info("set_cheapest_charge_windows: generierte Fenster: %s", windows)
        schedule = _build_timebased_schedule(windows)
        if not schedule:
            _LOGGER.warning("set_cheapest_charge_windows: Kein gültiger Zeitplan generiert – Time Mode wird NICHT aktiviert! Fenster: %s", windows)
            return
        await int_data["client"].set_timebased_charge(schedule)
        for r in readable:
            _LOGGER.info("KSEM Ladefenster gesetzt: %s", r)
        pn_create(
            hass,
            message="\n".join(readable),
            title="KSEM: Ladeplan gesetzt",
            notification_id="ksem_schedule",
        )
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
