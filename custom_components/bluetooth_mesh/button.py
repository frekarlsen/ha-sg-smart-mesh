"""Identify: blink an SG lamp so you can see which one it is.

Several pills of the same kind look the same in Home Assistant until you know
which ceiling each one sits in. Pressing *Identify* blinks the lamp three
times and puts it back as it was, with SG's own power commands — what the SG
app does to identify a device (``blinkForIdentification``). The standard
Bluetooth Mesh way (a provisioning Invite with an attention time) showed
nothing on a Pill Micro 3.0, firmware 0x0041, on 2026-10-19.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BluetoothMeshConfigEntry
from .const import DOMAIN
from .coordinator import MeshCoordinator
from .sg_settings import sg_nodes, sg_unicast


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluetoothMeshConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        SgIdentifyButton(coordinator, node) for node, _ in sg_nodes(coordinator)
    )


class SgIdentifyButton(ButtonEntity):
    """Blinks the lamp; named *Identify* by Home Assistant from its class."""

    _attr_has_entity_name = True
    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, coordinator: MeshCoordinator, node) -> None:
        self._coordinator = coordinator
        self._unicast = sg_unicast(node)
        device_key = f"{coordinator.network.identifier}_{node.unicast:04x}"
        self._attr_unique_id = f"{device_key}_identify"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_key)})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._coordinator.async_add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._coordinator.available

    async def async_press(self) -> None:
        if not await self._coordinator.async_sg_identify(self._unicast):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="sg_identify_failed",
                translation_placeholders={"unicast": f"0x{self._unicast:04x}"},
            )
