import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN
from homeassistant.helpers.entity import EntityCategory
from .helper import first_evse_from_coordinator

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
    wallbox = data.get("wallbox_coordinator")  # kann None sein
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
    # 4) jetzt alles hinzufügen
    async_add_entities(
        smartmeter_entities + wallbox_entities + more_entities
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

