import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN, SIGNAL_SCHEDULE_UPDATED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    device_info = (
        hass.data[DOMAIN][entry.entry_id].get("wallbox_device_info")
        or hass.data[DOMAIN][entry.entry_id]["device_info"]
    )
    async_add_entities([KsemScheduleActiveBinarySensor(hass, entry.entry_id, device_info)])


class KsemScheduleActiveBinarySensor(RestoreEntity, BinarySensorEntity):
    """True wenn ein Ladeplan aktiv ist, False wenn kein Zeitplan gesetzt ist."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self.hass = hass
        self._attr_name = "Zeitplan aktiv"
        self._attr_unique_id = f"ksem_{entry_id}_schedule_active"
        self._attr_device_info = device_info
        self._attr_icon = "mdi:calendar-check"
        self._is_on: bool = False
        self._unsub = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state == "on":
            self._is_on = True
        self._unsub = async_dispatcher_connect(
            self.hass, SIGNAL_SCHEDULE_UPDATED, self._handle_update
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()

    @callback
    def _handle_update(self, windows: list | None, readable: list | None) -> None:
        self._is_on = bool(windows)
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._is_on
