import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN
from .helper import first_evse_from_coordinator
from .modbus_map import SENSOR_DEFINITIONS

_LOGGER = logging.getLogger(__name__)

# ENTFERNT (debug/solar-mode-blocking): SENSOR_TYPES (CPU/RAM/Flash-Diagnostics)
# ENTFERNT: KsemSmartmeterSensor, KsemEvParameterSensor, KsemEvseAvailablePowerSensor


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    wallbox = data.get("wallbox_coordinator")
    modbus = data["modbus_coordinator"]
    device_info = data["device_info"]
    serial = data["serial"]

    # Wallbox-Device-Info (für korrekte Gerätezuordnung der Enector-Sensoren)
    wallbox_device_info: DeviceInfo | None = None
    wb = first_evse_from_coordinator(wallbox) if wallbox else None
    if wb:
        uuid = wb.get("uuid")
        label = wb.get("label", "Wallbox")
        model = wb.get("model", "")
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
    hass.data[DOMAIN][entry.entry_id]["wallbox_device_info"] = wallbox_device_info

    # Modbus-Sensoren (Grid, PV, Batterie, WB-Ladeleistung, Enector-Status)
    obis_entities = []
    for addr, spec in SENSOR_DEFINITIONS.items():
        info = (
            wallbox_device_info
            if spec.get("device") == "wallbox" and wallbox_device_info
            else device_info
        )
        obis_entities.append(KsemObisModbusSensor(modbus, addr, spec, info))

    async_add_entities(obis_entities)


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

