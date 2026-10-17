"""Which SG Smart 3.0 nodes get which settings entities.

Shared by the number and select platforms. A node is an SG node when it hosts
SG's vendor model; its product ID (composition data) says what it is, and so
which of SG's settings its own page in the SG app offers (``sg_product``).
Switches and wheels get none: they are buttons.
"""

from __future__ import annotations

from collections.abc import Iterator

from .btmesh.sg_smart import SgProduct, sg_product
from .const import MODEL_SG_VENDOR


def sg_unicast(node) -> int:
    """The address SG commands for ``node`` go to (its vendor model's element)."""
    element = node.element_for_model(MODEL_SG_VENDOR)
    return element.unicast if element is not None else node.unicast


def sg_nodes(coordinator) -> Iterator[tuple[object, SgProduct]]:
    """Every SG output node with its product, switches and wheels left out."""
    switches = set(getattr(coordinator, "sg_switches", ()))
    for node in coordinator.network.nodes:
        if not node.has_model(MODEL_SG_VENDOR) or node.unicast in switches:
            continue
        yield node, sg_product(getattr(node, "pid", 0))
