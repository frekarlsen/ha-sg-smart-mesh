"""Node configuration and discovery services (SG Smart work, plan B).

These let Home Assistant do what nRF Mesh was used for: read what a node is,
give it our AppKey, bind its models, point its publication at HA, and send any
raw access message while reverse-engineering a vendor protocol.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .btmesh.sg_smart import SgSwitchCommand
from .const import DOMAIN

# Service option -> what the paired node does.
SWITCH_COMMANDS = {
    "none": SgSwitchCommand.NONE,
    "toggle_on_off": SgSwitchCommand.TOGGLE_ON_OFF,
    "on": SgSwitchCommand.ON,
    "off": SgSwitchCommand.OFF,
    "dim": SgSwitchCommand.TOGGLE_DIM,
    "dim_up": SgSwitchCommand.DIM_UP,
    "dim_down": SgSwitchCommand.DIM_DOWN,
}
_COMMAND = vol.In(list(SWITCH_COMMANDS))


def _addr(value) -> int:
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    return int(text, 16) if text.startswith("0x") or any(c in text for c in "abcdef") else int(text)


_UNICAST = vol.All(_addr, vol.Range(min=1, max=0xFFFF))


def _coordinator(hass: HomeAssistant):
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry.runtime_data
    raise HomeAssistantError("Bluetooth Mesh is not loaded")


def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "configure_node"):
        return

    async def get_composition(call: ServiceCall):
        coord = _coordinator(hass)
        comp = await coord.async_get_composition_retry(
            call.data["unicast"], call.data["seconds"]
        )
        if comp is None:
            raise HomeAssistantError("No Composition Data reply")
        return {
            "cid": f"0x{comp.cid:04x}",
            "pid": f"0x{comp.pid:04x}",
            "vid": f"0x{comp.vid:04x}",
            "features": f"0x{comp.features:04x}",
            "description": comp.describe(),
        }

    async def configure_node(call: ServiceCall):
        coord = _coordinator(hass)
        publish = call.data.get("publish_address")
        return await coord.async_configure_node(
            call.data["unicast"], seconds=call.data["seconds"], publish=publish
        )

    async def send_raw(call: ServiceCall):
        coord = _coordinator(hass)
        payload = bytes.fromhex(call.data["payload"].replace(" ", ""))
        ok = await coord.async_send_raw(
            call.data["destination"], payload, call.data["device_key"]
        )
        return {"sent": ok}

    async def pair_switch(call: ServiceCall):
        coord = _coordinator(hass)
        ok = await coord.async_sg_pair_switch(
            call.data["node"],
            call.data["switch"],
            call.data["button"],
            press=SWITCH_COMMANDS[call.data["press"]],
            hold=SWITCH_COMMANDS[call.data["hold"]],
            rotate=SWITCH_COMMANDS[call.data["rotate"]],
        )
        if not ok:
            raise HomeAssistantError("The pairing command could not be sent")

    async def unpair_switch(call: ServiceCall):
        coord = _coordinator(hass)
        none = SgSwitchCommand.NONE
        ok = await coord.async_sg_pair_switch(
            call.data["node"], call.data["switch"], call.data["button"],
            press=none, hold=none, rotate=none,
        )
        if not ok:
            raise HomeAssistantError("The unpair command could not be sent")

    async def set_min_level(call: ServiceCall):
        _coordinator(hass).async_set_sg_min_level(
            call.data["unicast"], call.data["min_level"]
        )

    hass.services.async_register(
        DOMAIN, "set_min_level", set_min_level,
        schema=vol.Schema({
            vol.Required("unicast"): _UNICAST,
            vol.Required("min_level"): vol.All(vol.Coerce(int), vol.Range(0, 90)),
        }),
    )

    _pair_base = {
        vol.Required("node"): _UNICAST,
        vol.Required("switch"): _UNICAST,
        vol.Optional("button", default=4): vol.All(vol.Coerce(int), vol.Range(0, 255)),
    }
    hass.services.async_register(
        DOMAIN, "pair_switch", pair_switch,
        schema=vol.Schema({
            **_pair_base,
            vol.Optional("press", default="toggle_on_off"): _COMMAND,
            vol.Optional("hold", default="none"): _COMMAND,
            vol.Optional("rotate", default="dim"): _COMMAND,
        }),
    )
    hass.services.async_register(
        DOMAIN, "unpair_switch", unpair_switch, schema=vol.Schema(_pair_base),
    )
    hass.services.async_register(
        DOMAIN, "get_composition", get_composition,
        schema=vol.Schema({
            vol.Required("unicast"): _UNICAST,
            vol.Optional("seconds", default=30): vol.All(int, vol.Range(5, 300)),
        }),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, "configure_node", configure_node,
        schema=vol.Schema({
            vol.Required("unicast"): _UNICAST,
            vol.Optional("seconds", default=60): vol.All(int, vol.Range(10, 600)),
            vol.Optional("publish_address"): _UNICAST,
        }),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, "send_raw", send_raw,
        schema=vol.Schema({
            vol.Required("destination"): _UNICAST,
            vol.Required("payload"): cv.string,
            vol.Optional("device_key", default=False): cv.boolean,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
