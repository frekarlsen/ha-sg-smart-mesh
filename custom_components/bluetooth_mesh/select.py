"""SG Smart 3.0 settings with a fixed set of choices, as select entities.

``Push-button input``: what the dimmer's P terminal expects — the momentary
push button of the manual (Normal), or a maintained contact (Sensor).

``Fade-in``: how long the dimmer takes to fade up when switched on.

The dimmer stores both, but SG's protocol has no command to read either back,
so the entity shows the value Home Assistant last wrote and stays unknown
until then (whatever the SG app may have set before is not visible).
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BluetoothMeshConfigEntry
from .btmesh.sg_smart import SG_FADING_TENTHS, SgPushSwitchMode
from .const import DOMAIN
from .coordinator import MeshCoordinator
from .sg_settings import sg_nodes, sg_unicast

_PUSH_OPTIONS = {
    "normal": SgPushSwitchMode.NORMAL,
    "sensor": SgPushSwitchMode.SENSOR,
}
# Option keys for the fade-in times (tenths of a second): "fade_5" = 0.5 s.
_FADING_OPTIONS = {f"fade_{tenths}": tenths for tenths in SG_FADING_TENTHS}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluetoothMeshConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = []
    for node, product in sg_nodes(coordinator):
        if product.push_input:
            entities.append(SgPushSwitchSelect(coordinator, node))
        if product.dimmable:
            entities.append(SgFadingSelect(coordinator, node))
    async_add_entities(entities)


class _SgSettingSelect(SelectEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False
    _key: str

    def __init__(self, coordinator: MeshCoordinator, node) -> None:
        self._coordinator = coordinator
        self._unicast = sg_unicast(node)
        device_key = f"{coordinator.network.identifier}_{node.unicast:04x}"
        self._attr_unique_id = f"{device_key}_{self._key}"
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

    def _not_confirmed(self) -> HomeAssistantError:
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="sg_setting_not_confirmed",
            translation_placeholders={"unicast": f"0x{self._unicast:04x}"},
        )


class SgPushSwitchSelect(_SgSettingSelect):
    """What the dimmer's push-button input expects."""

    _key = "push_switch"
    _attr_translation_key = "sg_push_switch"
    _attr_options = list(_PUSH_OPTIONS)

    @property
    def current_option(self) -> str | None:
        mode = self._coordinator.sg_push_switch(self._unicast)
        for option, value in _PUSH_OPTIONS.items():
            if value == mode:
                return option
        return None

    async def async_select_option(self, option: str) -> None:
        if not await self._coordinator.async_sg_set_push_switch(
            self._unicast, _PUSH_OPTIONS[option]
        ):
            raise self._not_confirmed()
        self.async_write_ha_state()


class SgFadingSelect(_SgSettingSelect):
    """How long the dimmer fades up when switched on."""

    _key = "fading"
    _attr_translation_key = "sg_fading"
    _attr_options = list(_FADING_OPTIONS)

    @property
    def current_option(self) -> str | None:
        tenths = self._coordinator.sg_fading(self._unicast)
        for option, value in _FADING_OPTIONS.items():
            if value == tenths:
                return option
        return None

    async def async_select_option(self, option: str) -> None:
        if not await self._coordinator.async_sg_set_fading(
            self._unicast, _FADING_OPTIONS[option]
        ):
            raise self._not_confirmed()
        self.async_write_ha_state()
