"""Per-dimmer settings as number entities (on the device page, no YAML).

Two kinds, both under the device's Configuration:

``Minimum level (Home Assistant)``: the SG level (%) at which the dimmer's
load starts to light. Home Assistant's 1..100 % is spread over this level to
100 %, so the bottom of the slider is not dead. Changing it is live: set the
lamp to 1 % and raise the minimum until the lamp just glows. It acts on what
Home Assistant sends only; a wheel or the wall button still dims the whole
range.

``Dimmer minimum / start-up / maximum level``: SG's own trim, stored IN the
dimmer, so it holds for a wheel and the wall button too. The dimmer is the
record: the values are read from it, and a write is read back. The dimmer
accepts a minimum up to 40 % only (what the SG app offers); a load that needs
more keeps the Home Assistant minimum on top.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BluetoothMeshConfigEntry
from .btmesh.sg_smart import SG_TRIM_RANGES, SgTrim
from .const import DOMAIN
from .coordinator import SIGNAL_SG_MIN_LEVEL, SIGNAL_SG_TRIM, MeshCoordinator
from .sg_settings import sg_nodes, sg_unicast


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluetoothMeshConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = []
    for node, product in sg_nodes(coordinator):
        if not product.dimmable:
            continue
        entities.append(SgMinLevelNumber(coordinator, node))
        entities.extend(SgTrimNumber(coordinator, node, trim) for trim in SgTrim)
    async_add_entities(entities)


def _device_key(coordinator: MeshCoordinator, node) -> str:
    return f"{coordinator.network.identifier}_{node.unicast:04x}"


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
        # The same address the light entity drives and keys the level on.
        self._unicast = sg_unicast(node)
        device_key = _device_key(coordinator, node)
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


_TRIM_KEYS = {
    SgTrim.MIN: "trim_min",
    SgTrim.START_UP: "trim_start_up",
    SgTrim.MAX: "trim_max",
}


class SgTrimNumber(NumberEntity):
    """One of the trim levels an SG dimmer stores itself, in percent."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: MeshCoordinator, node, trim: SgTrim) -> None:
        self._coordinator = coordinator
        self._trim = trim
        self._unicast = sg_unicast(node)
        device_key = _device_key(coordinator, node)
        key = _TRIM_KEYS[trim]
        self._attr_translation_key = f"sg_{key}"
        self._attr_unique_id = f"{device_key}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_key)})
        self._attr_native_min_value, self._attr_native_max_value = SG_TRIM_RANGES[
            trim
        ]
        # Min and start-up travel in half percent, max in whole percent.
        self._attr_native_step = 1 if trim is SgTrim.MAX else 0.5

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_SG_TRIM.format(self._coordinator.entry.entry_id, self._unicast),
                self.async_write_ha_state,
            )
        )
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_availability)
        )
        self._read_if_unknown()

    @callback
    def _handle_availability(self) -> None:
        self.async_write_ha_state()
        self._read_if_unknown()

    @callback
    def _read_if_unknown(self) -> None:
        # Read once per start: the dimmer is the record, and nothing but the
        # SG app (or this entity) changes it.
        if (
            self._coordinator.available
            and self._coordinator.sg_trim(self._unicast) is None
        ):
            self._coordinator.async_request_sg_trim(self._unicast)

    @property
    def available(self) -> bool:
        return self._coordinator.available

    @property
    def native_min_value(self) -> float:
        low = self._attr_native_min_value
        levels = self._coordinator.sg_trim(self._unicast)
        if self._trim is SgTrim.START_UP and levels is not None:
            # As in the SG app: the start-up kick never sits below the minimum.
            return max(low, levels.min)
        return low

    @property
    def native_value(self) -> float | None:
        levels = self._coordinator.sg_trim(self._unicast)
        if levels is None:
            return None
        return {
            SgTrim.MIN: levels.min,
            SgTrim.START_UP: levels.start_up,
            SgTrim.MAX: levels.max,
        }[self._trim]

    async def async_set_native_value(self, value: float) -> None:
        if self._trim is SgTrim.MAX:
            value = round(value)
        else:
            value = round(value * 2) / 2
        if not await self._coordinator.async_sg_set_trim(
            self._unicast, self._trim, value
        ):
            self.async_write_ha_state()
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="sg_setting_not_confirmed",
                translation_placeholders={"unicast": f"0x{self._unicast:04x}"},
            )
