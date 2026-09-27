"""SG Smart switches and dimmer wheels as Home Assistant event entities.

The wheel still drives its paired dimmer directly (the pairing lives in the
dimmer); these entities only let Home Assistant see the same button events, so
an automation can react to a press, a hold or a turn. A turn arrives as a
series of ``rotate_up`` / ``rotate_down`` events while the wheel moves, each
with ``steps`` = detents turned since the previous one.
"""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BluetoothMeshConfigEntry
from .btmesh.sg_smart import SgButtonEvent
from .coordinator import SIGNAL_NEW_SG_SWITCH, MeshCoordinator
from .sg_switch import EVENT_TYPES, event_for, switch_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluetoothMeshConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        SgSwitchEvent(coordinator, unicast) for unicast in coordinator.sg_switches
    )

    @callback
    def _new_switch(unicast: int, first: SgButtonEvent) -> None:
        async_add_entities([SgSwitchEvent(coordinator, unicast, first)])

    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_NEW_SG_SWITCH.format(entry.entry_id), _new_switch
        )
    )


class SgSwitchEvent(EventEntity):
    """Button events from one SG switch or dimmer wheel."""

    _attr_has_entity_name = True
    _attr_translation_key = "sg_switch"
    _attr_should_poll = False
    _attr_event_types = EVENT_TYPES

    def __init__(
        self,
        coordinator: MeshCoordinator,
        unicast: int,
        first: SgButtonEvent | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._unicast = unicast
        self._first = first
        self._attr_unique_id = f"{coordinator.network.identifier}_{unicast:04x}_event"
        self._attr_device_info = switch_device_info(coordinator, unicast)
        self._open_holds: set[int] = set()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.async_add_sg_button_listener(
                self._unicast, self._handle_event
            )
        )
        if self._first is not None:
            self._handle_event(self._first)
            self._first = None

    @callback
    def _handle_event(self, event: SgButtonEvent) -> None:
        fired = event_for(event, self._open_holds)
        if fired is None:
            return
        self._trigger_event(*fired)
        self.async_write_ha_state()
