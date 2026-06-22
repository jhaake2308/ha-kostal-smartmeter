import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN, SIGNAL_SCHEDULE_UPDATED
from homeassistant.helpers.entity import EntityCategory
from .helper import first_evse_from_coordinator
from .modbus_map import SENSOR_DEFINITIONS

_LOGGER = logging.getLogger(__name__)

SENSOR_TYPES = {
    "CpuLoad": ("CPU Load", "%"),
    "CpuTemp": ("CPU Temperature", "°C"),
    "RamFree": ("RAM Free", "kB"),
    "RamTotal": ("RAM Total", "kB"),
    "FlashAppFree": ("Flash App Free", "B"),
    "FlashAppTotal": ("Flash App Total", "B"),
    "FlashDataFree": ("Flash Data Free", "B"),
    "FlashDataTotal": ("Flash Data Total", "B"),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    smart = data["smart_coordinator"]
    wallbox = data.get("wallbox_coordinator")
    modbus = data["modbus_coordinator"]
    device_info = data["device_info"]
    serial = data["serial"]

    # 1) Smartmeter-Entities immer
    smartmeter_entities = [
        KsemSmartmeterSensor(smart, key, name, unit, device_info, serial)
        for key, (name, unit) in SENSOR_TYPES.items()
    ]

    # 2) Genau EINE Wallbox (falls vorhanden)
    wallbox_entities: list = []
    wallbox_device_info: DeviceInfo | None = None
    wb_entities_created = False  # Flag: wir haben schon WB-Entities erzeugt?

    wb = first_evse_from_coordinator(wallbox) if wallbox else None
    if wb:
        uuid = wb.get("uuid")
        label = wb.get("label", "Wallbox")
        model = wb.get("model", "")
        state = wb.get("state", "unbekannt")
        details = wb.get("details") or {}
        wb_serial = details.get("serial", uuid)
        version = details.get("version", "")

        wallbox_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"wallbox-{uuid}")},
            name=f"Wallbox {label}",
            model=model,
            serial_number=wb_serial,
            sw_version=version,
            manufacturer="Kostal",
        )

        wb_entities_created = True

    # 3) Optionaler WB-Leistungssensor: nur, wenn Coordinator existiert
    more_entities = []
    if wallbox:
        evse_power_entity = KsemEvseAvailablePowerSensor(
            wallbox, wallbox_device_info or device_info
        )
        more_entities.append(evse_power_entity)

        # Add new sensors for EV parameters
        ev_param_sensors = [
            KsemEvParameterSensor(
                wallbox,
                wallbox_device_info or device_info,
                "Min Current",
                "min_current",
                "A",
                1000,
            ),
            KsemEvParameterSensor(
                wallbox,
                wallbox_device_info or device_info,
                "Max Current",
                "max_current",
                "A",
                1000,
            ),
            KsemEvParameterSensor(
                wallbox,
                wallbox_device_info or device_info,
                "Phases Used",
                "phases_used",
                None,
                1,
                is_dict=True,
            ),
            KsemEvParameterSensor(
                wallbox,
                wallbox_device_info or device_info,
                "Probing Successful",
                "probing_successful",
                None,
                1,
            ),
        ]
        more_entities.extend(ev_param_sensors)

    hass.data[DOMAIN][entry.entry_id]["wallbox_device_info"] = wallbox_device_info
    # 4) OBIS/Modbus-Entities
    obis_entities = []
    for addr, spec in SENSOR_DEFINITIONS.items():
        info = (
            wallbox_device_info
            if spec.get("device") == "wallbox" and wallbox_device_info
            else device_info
        )
        obis_entities.append(KsemObisModbusSensor(modbus, addr, spec, info))

    # 5) Aktiver-Zeitplan-Sensor
    schedule_sensor = KsemActiveScheduleSensor(
        hass, entry.entry_id, wallbox_device_info or device_info
    )

    # 6) jetzt alles hinzufügen
    async_add_entities(
        smartmeter_entities + wallbox_entities + more_entities + obis_entities + [schedule_sensor]
    )

    # 6) Falls beim Start noch keine WB da war: später automatisch nachziehen
    if wallbox and not wb_entities_created:

        async def _maybe_add_single_wb():
            nonlocal wallbox_device_info, wb_entities_created
            wb_now = first_evse_from_coordinator(wallbox)
            if not wb_now or wb_entities_created:
                return
            uuid = wb_now.get("uuid")
            label = wb_now.get("label", "Wallbox")
            model = wb_now.get("model", "")
            state = wb_now.get("state", "unbekannt")
            details = wb_now.get("details") or {}
            wb_serial = details.get("serial", uuid)
            version = details.get("version", "")

            wallbox_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"wallbox-{uuid}")},
                name=f"Wallbox {label}",
                model=model,
                serial_number=wb_serial,
                sw_version=version,
                manufacturer="Kostal",
            )
            hass.data[DOMAIN][entry.entry_id]["wallbox_device_info"] = (
                wallbox_device_info
            )
            wb_entities_created = True

        def _wb_listener():
            hass.async_create_task(_maybe_add_single_wb())

        wallbox.async_add_listener(_wb_listener)
        await _maybe_add_single_wb()  # gleich einmal versuchen


class KsemEvseAvailablePowerSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, device_info):
        super().__init__(coordinator)
        self._attr_name = "Verfügbare Ladeleistung"
        self._attr_unique_id = "ksem_evse_available_power"
        self._attr_native_unit_of_measurement = "W"
        self._attr_device_info = device_info
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        state = (self.coordinator.data or {}).get("evse_state", {})
        try:
            curtail = state.get("CurtailmentSetpoint", {})
            total_ma = (
                curtail.get("l1", 0) + curtail.get("l2", 0) + curtail.get("l3", 0)
            )
            return round((total_ma / 1000) * 230)
        except Exception as e:
            _LOGGER.warning("Fehler bei Umrechnung der Ladeleistung: %s", e)
            return None

    @property
    def extra_state_attributes(self):
        state = (self.coordinator.data or {}).get("evse_state", {})
        attrs = {
            "Curtailment_L2": state.get("CurtailmentSetpoint", {}).get("l2", 0),
            "Curtailment_L3": state.get("CurtailmentSetpoint", {}).get("l3", 0),
            "Curtailment_total": state.get("CurtailmentSetpoint", {}).get("total", 0),
            "ChargingPower_L1": state.get("EvChargingPower", {}).get("l1", 0),
            "ChargingPower_L2": state.get("EvChargingPower", {}).get("l2", 0),
            "ChargingPower_L3": state.get("EvChargingPower", {}).get("l3", 0),
            "ChargingPower_total": state.get("EvChargingPower", {}).get("total", 0),
            "OverloadProtectionActive": state.get("OverloadProtectionActive"),
            "GridPowerLimit": state.get("GridPowerLimit", {}).get("Power"),
            "GridLimitActive": state.get("GridPowerLimit", {}).get("Active"),
            "PVPowerLimit": state.get("PVPowerLimit", {}).get("Power"),
            "PVLimitActive": state.get("PVPowerLimit", {}).get("Active"),
        }
        return attrs


class KsemEvParameterSensor(CoordinatorEntity, SensorEntity):
    """Sensor for EV parameters from the /api/e-mobility/evparameterlist endpoint."""

    def __init__(
        self,
        coordinator,
        device_info,
        name: str,
        data_key: str,
        unit: str | None,
        divider: int = 1,
        is_dict: bool = False,
    ):
        super().__init__(coordinator)
        self._attr_name = f"EV {name}"
        self._attr_unique_id = f"ksem_ev_{data_key.lower()}"
        self._attr_device_info = device_info
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = SensorStateClass.MEASUREMENT if unit else None

        self._data_key = data_key
        self._divider = divider
        self._is_dict = is_dict

    @property
    def native_value(self):
        ev_params = (self.coordinator.data or {}).get("ev_params", {})
        if not ev_params:
            return None

        # The key is the EVSE ID, get the first one
        first_ev_id = next(iter(ev_params), None)
        if not first_ev_id:
            return None

        value = ev_params[first_ev_id].get(self._data_key)

        if value is None:
            return None

        if self._is_dict:
            return str(value)

        if isinstance(value, (int, float)) and self._divider != 1:
            return value / self._divider

        return value


class KsemSmartmeterSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, key, name, unit, device_info, serial):
        super().__init__(coordinator)
        self._sensor_key = key
        self._attr_name = f"{name}"
        self._attr_native_unit_of_measurement = unit
        self._attr_unique_id = f"{serial}_{key.lower()}"
        self._attr_device_info = device_info
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return self.coordinator.data.get(self._sensor_key)


class KsemObisModbusSensor(CoordinatorEntity, SensorEntity):
    """Sensor für einen Modbus-Register-Wert aus SENSOR_DEFINITIONS."""

    def __init__(self, coordinator, address: int, spec: dict, device_info: DeviceInfo):
        super().__init__(coordinator)
        self._address = address
        self._key = spec["name"]
        self._mapping = spec.get("map")
        self._attr_name = spec["name"]
        self._attr_native_unit_of_measurement = spec.get("unit") or None

        if spec.get("device_class") == "enum":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(self._mapping.values()) if self._mapping else []
            self._attr_native_unit_of_measurement = None
            self._attr_state_class = None
        else:
            dc = spec.get("device_class")
            self._attr_device_class = dc
            sc = spec.get("state_class")
            if sc == "total_increasing":
                self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            elif sc == "measurement" or dc in (
                "power", "voltage", "current", "battery", "temperature", "frequency"
            ):
                self._attr_state_class = SensorStateClass.MEASUREMENT
            else:
                self._attr_state_class = None

        ident = next(iter(device_info["identifiers"]))[1]
        self._attr_unique_id = f"{ident}_obis_{address}"
        self._attr_device_info = device_info

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.get(self._key)
        if val is None:
            return None
        if self._mapping:
            return self._mapping.get(int(val), f"Unbekannt ({val})")
        return val


class KsemActiveScheduleSensor(RestoreEntity, SensorEntity):
    """Zeigt die aktuell gesetzten Ladefenster an und persistiert sie über HA-Neustarts."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self.hass = hass
        self._attr_name = "Aktive Ladefenster"
        self._attr_unique_id = f"ksem_{entry_id}_active_schedule"
        self._attr_device_info = device_info
        self._attr_icon = "mdi:calendar-clock"
        self._attr_device_class = SensorDeviceClass.ENUM
        self._attr_options = ["kein Zeitplan", "aktiv"]
        self._state: str = "kein Zeitplan"
        self._windows: list = []
        self._readable: list = []
        self._last_set: str | None = None
        self._unsub = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Letzten Zustand nach HA-Neustart wiederherstellen
        last = await self.async_get_last_state()
        if last and last.state not in ("unknown", "unavailable", None):
            self._state = last.state
            attrs = last.attributes or {}
            self._windows = list(attrs.get("fenster", []))
            self._readable = list(attrs.get("fenster_lesbar", []))
            self._last_set = attrs.get("zuletzt_gesetzt")
        # Dispatcher-Signal abonnieren
        self._unsub = async_dispatcher_connect(
            self.hass, SIGNAL_SCHEDULE_UPDATED, self._handle_update
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()

    @callback
    def _handle_update(self, windows: list | None, readable: list | None) -> None:
        if windows:
            self._state = "aktiv"
            self._windows = list(windows)
            self._readable = list(readable) if readable else []
            from homeassistant.util import dt as dt_util
            self._last_set = dt_util.now().isoformat()
        else:
            self._state = "kein Zeitplan"
            self._windows = []
            self._readable = []
            self._last_set = None
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        return self._state

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "fenster": self._windows,
            "fenster_lesbar": self._readable,
            "zuletzt_gesetzt": self._last_set,
        }

