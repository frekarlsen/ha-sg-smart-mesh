"""Per-dimmer settings as number entities (on the device page, no YAML).

``Minimum level``: the SG level (%) at which the dimmer's load starts to
light. Home Assistant's 1..100 % is spread over this level to 100 %, so the
bottom of the slider is not dead. Changing it is live: set the lamp to 1 %
and raise the minimum until the lamp just glows.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BluetoothMeshConfigEntry
from .const import DOMAIN, MODEL_SG_VENDOR
from .coordinator import SIGNAL_SG_MIN_LEVEL, MeshCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluetoothMeshConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        SgMinLevelNumber(coordinator, node)
        for node in coordinator.network.nodes
        if node.has_model(MODEL_SG_VENDOR)
    )


class SgMinLevelNumber(NumberEntity):
    """The SG dimmer's minimum level (dead zone at the bottom), in percent."""

    _attr_has_entity_name = True
    _attr_translation_key = "sg_min_level"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False
    _attr_native_min_value = 0
    _attr_native_max_value = 90
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: MeshCoordinator, node) -> None:
        self._coordinator = coordinator
        element = node.element_for_model(MODEL_SG_VENDOR)
        # The same address the light entity drives and keys the level on.
        self._unicast = element.unicast if element is not None else node.unicast
        device_key = f"{coordinator.network.identifier}_{node.unicast:04x}"
        self._attr_unique_id = f"{device_key}_min_level"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_key)})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_SG_MIN_LEVEL.format(
                    self._coordinator.entry.entry_id, self._unicast
                ),
                self._changed,
            )
        )

    @callback
    def _changed(self, _old: int, _new: int) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        return self._coordinator.sg_min_level(self._unicast)

    async def async_set_native_value(self, value: float) -> None:
        self._coordinator.async_set_sg_min_level(self._unicast, int(round(value)))
