import logging
import voluptuous as vol
from homeassistant import config_entries

from .const import DOMAIN
from .api import KsemClient, InvalidAuth

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({
    vol.Required("host"): str,
    vol.Required("password"): str,
})

EVCC_SCHEMA = vol.Schema({
    vol.Optional("evcc_url", default=""): str,
    vol.Optional("evcc_hours_needed", default=3): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=8)
    ),
    vol.Optional("evcc_search_from", default="22:00"): str,
    vol.Optional("evcc_search_until", default="06:00"): str,
    vol.Optional("evcc_mode", default="grid"): vol.In(["grid", "pv", "hybrid"]),
})


class KsemConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for KSEM."""
    VERSION = 1

    def __init__(self):
        self._host = None
        self._password = None

    async def async_step_user(self, user_input=None):
        errors = {}
        _LOGGER.debug("Starte Config-Flow mit Input: %s", user_input)
        if user_input:
            client = KsemClient(self.hass, user_input["host"], user_input["password"])
            try:
                await client.get_device_info()
            except InvalidAuth:
                errors["base"] = "auth"
            except Exception as e:
                _LOGGER.error("API Fehler: %s", e)
                errors["base"] = "unknown"
            else:
                self._host = user_input["host"]
                self._password = user_input["password"]
                return await self.async_step_evcc()
        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)

    async def async_step_evcc(self, user_input=None):
        """Optionaler Schritt: evcc-Integration konfigurieren.

        evcc_url leer lassen (oder löschen), um diesen Schritt zu überspringen.
        """
        if user_input is not None:
            data: dict = {"host": self._host, "password": self._password}
            evcc_url = (user_input.get("evcc_url") or "").strip().rstrip("/")
            if evcc_url:
                data["evcc_url"] = evcc_url
                data["evcc_hours_needed"] = int(user_input.get("evcc_hours_needed", 3))
                data["evcc_search_from"] = user_input.get("evcc_search_from", "22:00")
                data["evcc_search_until"] = user_input.get("evcc_search_until", "06:00")
                data["evcc_mode"] = user_input.get("evcc_mode", "grid")
            return self.async_create_entry(title=self._host, data=data)
        return self.async_show_form(
            step_id="evcc",
            data_schema=EVCC_SCHEMA,
            errors={},
            last_step=True,
        )

    # ------------------------------------------------------------------
    # Neu konfigurieren (erscheint im ⋮-Menü der Integration als
    # "Neu konfigurieren" / "Reconfigure")
    # ------------------------------------------------------------------

    async def async_step_reconfigure(self, user_input=None):
        """Schritt 1 (Neu konfigurieren): host + password prüfen."""
        reconfigure_entry = self._get_reconfigure_entry()
        errors = {}

        current_host = reconfigure_entry.data.get("host", "")
        current_password = reconfigure_entry.data.get("password", "")

        if user_input is not None:
            client = KsemClient(self.hass, user_input["host"], user_input["password"])
            try:
                await client.get_device_info()
            except InvalidAuth:
                errors["base"] = "auth"
            except Exception as e:
                _LOGGER.error("Reconfigure – API Fehler: %s", e)
                errors["base"] = "unknown"
            else:
                self._host = user_input["host"]
                self._password = user_input["password"]
                return await self.async_step_reconfigure_evcc()

        schema = vol.Schema({
            vol.Required("host", default=current_host): str,
            vol.Required("password", default=current_password): str,
        })
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reconfigure_evcc(self, user_input=None):
        """Schritt 2 (Neu konfigurieren): evcc-Einstellungen anpassen."""
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            new_data: dict = {"host": self._host, "password": self._password}
            evcc_url = (user_input.get("evcc_url") or "").strip().rstrip("/")
            if evcc_url:
                new_data["evcc_url"] = evcc_url
                new_data["evcc_hours_needed"] = int(user_input.get("evcc_hours_needed", 3))
                new_data["evcc_search_from"] = user_input.get("evcc_search_from", "22:00")
                new_data["evcc_search_until"] = user_input.get("evcc_search_until", "06:00")
                new_data["evcc_mode"] = user_input.get("evcc_mode", "grid")
            return self.async_update_reload_and_abort(
                reconfigure_entry,
                data=new_data,
            )

        evcc_defaults = {
            "evcc_url": reconfigure_entry.data.get("evcc_url", ""),
            "evcc_hours_needed": reconfigure_entry.data.get("evcc_hours_needed", 3),
            "evcc_search_from": reconfigure_entry.data.get("evcc_search_from", "22:00"),
            "evcc_search_until": reconfigure_entry.data.get("evcc_search_until", "06:00"),
            "evcc_mode": reconfigure_entry.data.get("evcc_mode", "grid"),
        }
        schema = vol.Schema({
            vol.Optional("evcc_url", default=evcc_defaults["evcc_url"]): str,
            vol.Optional("evcc_hours_needed", default=evcc_defaults["evcc_hours_needed"]): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=8)
            ),
            vol.Optional("evcc_search_from", default=evcc_defaults["evcc_search_from"]): str,
            vol.Optional("evcc_search_until", default=evcc_defaults["evcc_search_until"]): str,
            vol.Optional("evcc_mode", default=evcc_defaults["evcc_mode"]): vol.In(["grid", "pv", "hybrid"]),
        })
        return self.async_show_form(
            step_id="reconfigure_evcc",
            data_schema=schema,
            errors={},
            last_step=True,
        )
