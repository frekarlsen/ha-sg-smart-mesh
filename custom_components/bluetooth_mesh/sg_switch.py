"""Shared bits for SG Smart switches and dimmer wheels (event + battery).

A switch is not in any network export as such, and its composition says little,
so it is recognised by what it does: the first button event (status kind 0x2A)
from a unicast makes the coordinator remember that unicast, and the event and
sensor platforms add its entities from then on.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .btmesh.sg_smart import (
    SG_ACTION_HOLD,
    SG_ACTION_PRESS,
    SG_ACTION_ROTATE,
    SgButtonEvent,
)
from .const import DOMAIN

EVENT_TYPES = ["press", "hold", "rotate_up", "rotate_down", "unknown"]


def event_for(event: SgButtonEvent, open_holds: set[int]) -> tuple[str, dict] | None:
    """HA event (type, attributes) for one new frame; None to stay quiet.

    ``open_holds`` holds the TIDs of holds already reported, so a hold fires
    once however many frames it lasts (and even if its first frame was lost).
    """
    attrs: dict = {"button": event.button}
    if event.action == SG_ACTION_PRESS:
        return "press", attrs
    if event.action == SG_ACTION_HOLD:
        if event.tid in open_holds:
            return None
        open_holds.clear()
        open_holds.add(event.tid)
        return "hold", attrs
    if event.action == SG_ACTION_ROTATE:
        steps = event.value
        if steps == 0:
            return None
        attrs["steps"] = abs(steps)
        return ("rotate_up" if steps > 0 else "rotate_down"), attrs
    attrs.update(action=f"0x{event.action:02x}", values=event.values.hex())
    return "unknown", attrs


def battery_voltage(event: SgButtonEvent) -> float | None:
    """Battery byte as volts (0.1 V units); None when implausible."""
    volts = event.battery / 10
    return volts if 1.5 <= volts <= 4.0 else None


def switch_device_info(coordinator, unicast: int) -> DeviceInfo:
    identifier = f"{coordinator.network.identifier}_{unicast:04x}"
    name = None
    for node in coordinator.network.nodes:
        if node.unicast == unicast:
            name = node.name or None
            break
    return DeviceInfo(
        identifiers={(DOMAIN, identifier)},
        name=name or f"SG Smart switch {unicast:04x}",
        manufacturer="SG Armaturen",
        model="SG Smart 3.0 switch / dimmer wheel",
    )
