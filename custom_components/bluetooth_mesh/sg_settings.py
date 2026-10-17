"""Which SG Smart 3.0 nodes get which settings entities, and the pairings sent.

Shared by the number, select and sensor platforms and the options flow. A node
is an SG node when it hosts SG's vendor model; its product ID (composition
data) says what it is, and so which of SG's settings its own page in the SG app
offers (``sg_product``). Switches and wheels get none: they are buttons.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import NamedTuple

from homeassistant.helpers import device_registry as dr

from .btmesh.sg_smart import SgProduct, SgSwitchCommand, sg_product
from .const import DOMAIN, MODEL_SG_VENDOR


def sg_unicast(node) -> int:
    """The address SG commands for ``node`` go to (its vendor model's element)."""
    element = node.element_for_model(MODEL_SG_VENDOR)
    return element.unicast if element is not None else node.unicast


def sg_nodes(coordinator) -> Iterator[tuple[object, SgProduct]]:
    """Every SG output node with its product, switches and wheels left out.

    A switch is known by having been heard, or by its product ID before it
    has been used.
    """
    switches = set(getattr(coordinator, "sg_switches", ()))
    for node in coordinator.network.nodes:
        if not node.has_model(MODEL_SG_VENDOR) or node.unicast in switches:
            continue
        product = sg_product(getattr(node, "pid", 0))
        if not product.is_switch:
            yield node, product


class SgPairing(NamedTuple):
    """One switch button paired to a dimmer, as Home Assistant sent it.

    ``node`` is the address the pairing was sent to: the one the options flow
    and the ``pair_switch`` action take, the node's own unicast.
    """

    node: int
    switch: int
    button: int
    press: int
    hold: int
    rotate: int

    @classmethod
    def from_option(cls, raw: dict) -> SgPairing | None:
        try:
            return cls(*(int(raw[field]) for field in cls._fields))
        except (KeyError, TypeError, ValueError):
            return None  # hand-edited or from a future version: skip it

    def as_option(self) -> dict:
        return self._asdict()

    @property
    def unpairs(self) -> bool:
        return {self.press, self.hold, self.rotate} == {SgSwitchCommand.NONE}


def load_pairings(raw: Iterable) -> list[SgPairing]:
    pairings = (SgPairing.from_option(r) for r in raw if isinstance(r, dict))
    return [p for p in pairings if p is not None]


def command_name(command: int) -> str:
    """A wire command as the options flow and the ``pair_switch`` action name it."""
    try:
        value = SgSwitchCommand(command)
    except ValueError:
        return f"0x{command:02x}"
    return "dim" if value is SgSwitchCommand.TOGGLE_DIM else value.name.lower()


def device_name(hass, coordinator, unicast: int) -> str:
    """What the device page calls ``unicast``, a rename in Home Assistant included.

    Looked up through the entry's devices rather than by identifier: the
    identifier lookup is deprecated on newer cores (see ``MeshProxySensor``).
    """
    identifier = f"{coordinator.network.identifier}_{unicast:04x}"
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(
        registry, coordinator.entry.entry_id
    ):
        if (DOMAIN, identifier) in device.identifiers:
            return device.name_by_user or device.name or f"0x{unicast:04x}"
    for node in coordinator.network.nodes:
        if node.unicast == unicast and node.name:
            return node.name
    return f"0x{unicast:04x}"
