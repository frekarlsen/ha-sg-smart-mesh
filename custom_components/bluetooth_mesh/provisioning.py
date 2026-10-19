"""Adding devices to the mesh from Home Assistant (replaces nRF Mesh).

Three pieces, kept apart so each can be tested alone:

* **discovery** — unprovisioned devices advertise the Mesh Provisioning service
  (0x1827) with their Device UUID in the service data; Home Assistant's
  Bluetooth stack (through an ESPHome proxy) already sees those adverts.
* **provisioning** — PB-GATT to the chosen device through the same proxy,
  driven by :class:`btmesh.provisioner.Provisioner` (No OOB, as SG devices
  and nRF Mesh use). It yields the node's unicast address and device key.
* **the stored network** — the integration keeps the network as the JSON it
  was imported from (a Mesh Configuration Database document). A new node is
  written into it like nRF Mesh would, so the rest of the integration, a
  re-import and a backup all see the same thing.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Callable

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .btmesh.access import CompositionData
from .btmesh.bearer import PROV_SERVICE, GattBearer, parse_unprovisioned_service_data
from .btmesh.prov_pdu import Capabilities, Invite
from .btmesh.prov_pdu import decode as decode_prov_pdu
from .btmesh.provisioner import Provisioner
from .btmesh.proxy_pdu import MSG_TYPE_PROVISIONING_PDU
from .btmesh.pump import BearerPump
from .btmesh.sg_smart import SG_COMPANY_ID, sg_product
from .const import (
    CONF_CONNECT_JSON,
    CONF_RETIRED_UNICASTS,
    CONF_SG_MIN_LEVEL,
    CONF_SG_PAIRINGS,
    CONF_SG_SWITCHES,
    CONTROLLED_MODEL_IDS,
    DOMAIN,
    MODEL_SG_VENDOR,
)
from .coordinator import is_sg_switch

logger = logging.getLogger(__name__)

PROVISIONING_TIMEOUT = 60.0

# Addresses handed to new nodes start here; the integration's own source
# address lives at the top of the range, far away.
FIRST_NODE_ADDRESS = 0x0002
LAST_NODE_ADDRESS = 0x6FFF


class ProvisioningFailed(Exception):
    """Provisioning did not complete; the message says why."""


# ------------------------------------------------------------------ discovery


@dataclass(frozen=True)
class UnprovisionedBeacon:
    address: str
    uuid: bytes
    name: str
    rssi: int | None
    age: float = 0.0  # seconds since HA last heard it

    @property
    def label(self) -> str:
        name = self.name or "Unknown device"
        rssi = f"{self.rssi} dBm, " if self.rssi is not None else ""
        return f"{name} · {self.address} ({rssi}seen {_age_text(self.age)} ago)"


def _age_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds} s" if seconds < 120 else f"{seconds // 60} min"


def discovered_unprovisioned(hass: HomeAssistant) -> list[UnprovisionedBeacon]:
    """Every unprovisioned device HA has heard, most recently heard first.

    HA keeps an advert for several minutes after the device went quiet, so a
    battery device that just advertised shows a small age, and one whose
    battery was taken out keeps its entry with a growing age.
    """
    now = monotonic()
    found: dict[bytes, UnprovisionedBeacon] = {}
    for info in bluetooth.async_discovered_service_info(hass, connectable=False):
        data = info.service_data.get(PROV_SERVICE)
        if not data:
            continue
        parsed = parse_unprovisioned_service_data(data)
        if parsed is None:
            continue
        if not info.connectable:
            logger.debug(
                "unprovisioned %s (%s) heard only as non-connectable",
                info.address, parsed[0].hex(),
            )
            continue
        name = info.name or ""
        if name.replace("-", ":").upper() == info.address.upper():
            name = ""
        beacon = UnprovisionedBeacon(
            address=info.address,
            uuid=parsed[0],
            name=name,
            rssi=info.rssi,
            age=max(0.0, now - info.time),
        )
        known = found.get(beacon.uuid)
        if known is None or beacon.age < known.age:
            found[beacon.uuid] = beacon
    return sorted(found.values(), key=lambda b: b.age)


# --------------------------------------------------------------- provisioning


@dataclass(frozen=True)
class ProvisionResult:
    unicast: int
    device_key: bytes
    num_elements: int
    uuid: bytes


@asynccontextmanager
async def _prov_link(hass: HomeAssistant, beacon: UnprovisionedBeacon, on_pdu, on_error):
    """A PB-GATT link to ``beacon`` through a proxy, closed again on exit.

    ``on_pdu(payload)`` gets each Provisioning PDU; ``on_error(exc)`` a broken
    link. Yields the send function for Provisioning PDUs.
    """
    ble_device = bluetooth.async_ble_device_from_address(
        hass, beacon.address, connectable=True
    )
    if ble_device is None:
        raise ProvisioningFailed(
            "the device is no longer reachable through a Bluetooth proxy"
        )
    try:
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            f"btmesh-prov-{beacon.address}",
            max_attempts=3,
        )
    except Exception as exc:  # noqa: BLE001 - bleak raises many kinds
        raise ProvisioningFailed(f"could not connect: {exc}") from exc

    bearer = GattBearer(client, provisioning=True)
    pump = BearerPump(bearer, MSG_TYPE_PROVISIONING_PDU)
    pump.on_error = on_error

    def on_message(msg_type: int, payload: bytes) -> None:
        if msg_type == MSG_TYPE_PROVISIONING_PDU:
            on_pdu(payload)

    try:
        await bearer.start(on_message)
        pump.start()
        yield pump.put
    finally:
        await pump.stop()
        await bearer.stop()
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def async_provision(
    hass: HomeAssistant,
    beacon: UnprovisionedBeacon,
    *,
    net_key: bytes,
    net_key_index: int,
    iv_index: int,
    choose_address: Callable[[int], int],
) -> ProvisionResult:
    """PB-GATT provision ``beacon``; ``choose_address(num_elements)`` -> unicast."""
    done = asyncio.Event()
    errors: list[BaseException] = []
    prov: Provisioner | None = None

    def fail(exc: BaseException) -> None:
        errors.append(exc)
        done.set()

    def on_pdu(payload: bytes) -> None:
        try:
            prov.handle_pdu(payload)
        except BaseException as exc:  # noqa: BLE001 - surface to the waiter
            fail(exc)

    async with _prov_link(hass, beacon, on_pdu, fail) as send:
        prov = Provisioner(
            net_key, net_key_index, iv_index, FIRST_NODE_ADDRESS, send=send
        )

        def on_capabilities(caps) -> None:
            prov.unicast_addr = choose_address(max(1, caps.num_elements))

        prov.on_capabilities = on_capabilities
        prov.on_done = done.set
        prov.start()
        try:
            await asyncio.wait_for(done.wait(), PROVISIONING_TIMEOUT)
        except TimeoutError:
            raise ProvisioningFailed(
                f"the device stopped answering (step {prov.state.name})"
            ) from None
        if errors:
            raise ProvisioningFailed(f"{errors[0]} (step {prov.state.name})")

    if not prov.done or prov.device_key is None or prov.capabilities is None:
        raise ProvisioningFailed(f"ended without completing (step {prov.state.name})")
    return ProvisionResult(
        unicast=prov.unicast_addr,
        device_key=prov.device_key,
        num_elements=max(1, prov.capabilities.num_elements),
        uuid=beacon.uuid,
    )


# Long enough to look round a room for it; short enough to try the next one.
IDENTIFY_SECONDS = 10
# A device in reach answers the Invite at once; give a proxy link a moment.
IDENTIFY_ANSWER_TIMEOUT = 15.0


async def async_identify_unprovisioned(
    hass: HomeAssistant, beacon: UnprovisionedBeacon, seconds: int = IDENTIFY_SECONDS
) -> bool:
    """Ask a device that is not added yet to draw attention to itself.

    The standard way (nRF Mesh's *Identify*): open a provisioning link, send
    Invite with an attention time, and hold the link that long. Nothing is
    added; on disconnect the device is unprovisioned as before. True if the
    device answered (Capabilities) — whether it also blinks is up to its
    firmware: the SG app sends no attention time and identifies a device after
    adding it instead, which :func:`async_add_device` does too.
    """
    answered = asyncio.Event()

    def on_pdu(payload: bytes) -> None:
        try:
            if isinstance(decode_prov_pdu(payload), Capabilities):
                answered.set()
        except Exception:  # noqa: BLE001 - only the answer matters here
            return

    async with _prov_link(hass, beacon, on_pdu, lambda _exc: None) as send:
        # The Invite alone, not a Provisioner: that would go on to Start and
        # the key exchange the moment Capabilities came back.
        send(Invite(attention_duration=seconds).encode())
        try:
            await asyncio.wait_for(answered.wait(), IDENTIFY_ANSWER_TIMEOUT)
        except TimeoutError:
            return False
        # Hold the link: a device may end its attention when the link goes.
        await asyncio.sleep(seconds)
    return True


# --------------------------------------------------------- the stored network


def _node_span(node: dict) -> range:
    try:
        base = int(str(node.get("unicastAddress")), 16)
    except ValueError:
        return range(0)
    count = len(node.get("elements") or []) or 1
    return range(base, base + count)


def choose_unicast(data: dict, num_elements: int, reserved: set[int] = frozenset()) -> int:
    """Lowest address with ``num_elements`` free in a row, avoiding ``reserved``."""
    used: set[int] = set(reserved)
    for node in data.get("nodes", []):
        if isinstance(node, dict):
            used.update(_node_span(node))
    address = FIRST_NODE_ADDRESS
    while address + num_elements - 1 <= LAST_NODE_ADDRESS:
        span = range(address, address + num_elements)
        if not used.intersection(span):
            return address
        address += 1
    raise ProvisioningFailed("no free unicast address left")


def _uuid_str(uuid: bytes) -> str:
    h = uuid.hex().upper()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def add_node(data: dict, result: ProvisionResult, name: str, net_key_index: int) -> dict:
    """A copy of ``data`` with the freshly provisioned node in ``nodes``."""
    out = copy.deepcopy(data)
    out.setdefault("nodes", [])
    out["nodes"] = [
        n for n in out["nodes"]
        if not (isinstance(n, dict) and _node_span(n) and result.unicast in _node_span(n))
    ]
    out["nodes"].append({
        "UUID": _uuid_str(result.uuid),
        "name": name,
        "unicastAddress": f"{result.unicast:04X}",
        "deviceKey": result.device_key.hex().upper(),
        "security": "insecure",
        "configComplete": False,
        "excluded": False,
        "netKeys": [{"index": net_key_index, "updated": False}],
        "appKeys": [],
        "elements": [
            {"index": i, "location": "0000", "models": []}
            for i in range(result.num_elements)
        ],
    })
    return out


def set_node_composition(
    data: dict, unicast: int, comp: CompositionData, app_key_index: int
) -> dict:
    """A copy of ``data`` with ``unicast``'s composition and bindings filled in."""
    out = copy.deepcopy(data)
    for node in out.get("nodes", []):
        if not isinstance(node, dict) or unicast not in _node_span(node):
            continue
        node["cid"] = f"{comp.cid:04X}"
        node["pid"] = f"{comp.pid:04X}"
        node["vid"] = f"{comp.vid:04X}"
        node["crpl"] = f"{comp.crpl:04X}"
        node["configComplete"] = True
        node["appKeys"] = [{"index": app_key_index, "updated": False}]
        elements = []
        for index, element in enumerate(comp.elements):
            models = [
                {"modelId": f"{m:04X}", "bind": [] if m in (0x0000, 0x0002) else [app_key_index],
                 "subscribe": []}
                for m in element.sig_models
            ]
            models += [
                {"modelId": f"{cid:04X}{mid:04X}", "bind": [app_key_index], "subscribe": []}
                for cid, mid in element.vendor_models
            ]
            elements.append({"index": index, "location": f"{element.loc:04X}", "models": models})
        node["elements"] = elements
        break
    return out


# -------------------------------------------------------------- whole journey


async def _wait_available(hass: HomeAssistant, entry, seconds: float = 45.0):
    """The reloaded entry's coordinator, once its proxy link is up."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        coordinator = getattr(entry, "runtime_data", None)
        if coordinator is not None and coordinator.available:
            return coordinator
        await asyncio.sleep(0.5)
    raise ProvisioningFailed(
        "Home Assistant could not reconnect to the mesh after adding the node"
    )


def _store(hass: HomeAssistant, entry, data: dict) -> None:
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_CONNECT_JSON: json.dumps(data, indent=2)}
    )


def _default_name(beacon: UnprovisionedBeacon, unicast: int, comp=None) -> str:
    """What a new device is called until it is named: its product, if known."""
    if comp is not None and comp.cid == SG_COMPANY_ID:
        product = sg_product(comp.pid).name
    else:
        product = beacon.name or "Mesh device"
    return f"{product} {unicast:04x}"


async def async_add_device(
    hass: HomeAssistant, entry, beacon: UnprovisionedBeacon
) -> dict:
    """Provision, store and configure one device. Returns a summary.

    Each stage is written to the stored network before the next begins, so a
    device that is provisioned but fails to configure (a sleeping wheel) is
    not lost: its device key is kept and ``configure_node`` can finish it.

    The entry is reloaded with the node's composition by
    :func:`async_finish_add`, once the device has a name: the name is asked
    after the device has shown which one it is (it blinks), and the entities
    take their ids from it when they are created.
    """
    coordinator = entry.runtime_data
    network = coordinator.network
    data = json.loads(entry.data[CONF_CONNECT_JSON])
    reserved = {coordinator.src_addr, *entry.options.get(CONF_RETIRED_UNICASTS, [])}

    result = await async_provision(
        hass,
        beacon,
        net_key=network.net_key,
        net_key_index=network.net_key_index,
        iv_index=network.iv_index,
        choose_address=lambda count: choose_unicast(data, count, reserved),
    )
    logger.info(
        "provisioned %s as %#06x (%d element(s))",
        beacon.uuid.hex(), result.unicast, result.num_elements,
    )
    name = _default_name(beacon, result.unicast)
    data = add_node(data, result, name, network.net_key_index)
    _store(hass, entry, data)
    await hass.config_entries.async_reload(entry.entry_id)

    coordinator = await _wait_available(hass, entry)
    # Give the node a moment to leave PB-GATT and join as a mesh node.
    await asyncio.sleep(2.0)
    report, comp = await coordinator.async_configure_node_full(
        result.unicast, seconds=90
    )
    summary = {
        "name": name,
        "unicast": result.unicast,
        "configured": comp is not None,
        "can_blink": False,
    }
    if comp is None:
        summary["error"] = report.get("error", "no reply")
        return summary

    app_key_index = coordinator.network.app_key_for_models(
        CONTROLLED_MODEL_IDS
    ).index
    data = set_node_composition(data, result.unicast, comp, app_key_index)
    summary["name"] = _default_name(beacon, result.unicast, comp)
    data = rename_node(data, result.unicast, summary["name"])
    _store(hass, entry, data)
    switch = is_sg_switch(comp)
    summary["kind"] = "switch" if switch else "device"
    # An SG output answers SG's power commands at once: it can show itself.
    summary["can_blink"] = not switch and any(
        (SG_COMPANY_ID, 0x0000) in element.vendor_models for element in comp.elements
    )
    if switch:
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                CONF_SG_SWITCHES: sorted(
                    {*entry.options.get(CONF_SG_SWITCHES, []), result.unicast}
                ),
            },
        )
    return summary


def rename_node(data: dict, unicast: int, name: str) -> dict:
    """A copy of ``data`` with the node at ``unicast`` called ``name``."""
    out = copy.deepcopy(data)
    for node in out.get("nodes", []):
        if isinstance(node, dict) and unicast in _node_span(node):
            node["name"] = name
    return out


async def async_finish_add(hass: HomeAssistant, entry, unicast: int, name: str) -> None:
    """Name the node added by :func:`async_add_device`, and bring it into HA."""
    data = json.loads(entry.data[CONF_CONNECT_JSON])
    _store(hass, entry, rename_node(data, unicast, name))
    await hass.config_entries.async_reload(entry.entry_id)


# ------------------------------------------------------------------- remove


def remove_node(data: dict, unicast: int) -> tuple[dict, list[int]]:
    """A copy of ``data`` without the node owning ``unicast``, and its addresses."""
    out = copy.deepcopy(data)
    kept, removed = [], []
    for node in out.get("nodes", []):
        span = _node_span(node) if isinstance(node, dict) else range(0)
        if unicast in span:
            removed.extend(span)
        else:
            kept.append(node)
    out["nodes"] = kept
    return out, removed or [unicast]


async def async_remove_device(
    hass: HomeAssistant, entry, unicast: int, *, force: bool = False
) -> dict:
    """Factory reset a node over the mesh and drop it from HA. Returns a summary.

    Like removing a device in the SG app: the node gets Config Node Reset (it
    forgets keys and address and is unprovisioned again, ready to be added
    anywhere). A removed switch is first unpaired from every SG dimmer, so no
    dimmer keeps reacting to its address. Without ``force`` a node that does
    not confirm the reset is kept; with it, it is dropped from HA regardless
    (then reset it by hand).
    """
    from homeassistant.helpers import device_registry as dr

    from .btmesh.sg_smart import SgSwitchCommand

    coordinator = entry.runtime_data
    network = coordinator.network
    node = next((n for n in network.nodes if n.unicast == unicast), None)
    is_switch = unicast in coordinator.sg_switches or (
        node is not None and not node.has_model(MODEL_SG_VENDOR)
    )
    unpaired = []
    if is_switch:
        none = SgSwitchCommand.NONE
        for dimmer in network.nodes:
            if dimmer.unicast != unicast and dimmer.has_model(MODEL_SG_VENDOR):
                if await coordinator.async_sg_pair_switch(
                    dimmer.unicast, unicast, 4, press=none, hold=none, rotate=none
                ):
                    unpaired.append(f"0x{dimmer.unicast:04x}")

    reset = False
    if node is not None:
        reset = await coordinator.async_node_reset(unicast)
    if not reset and not force:
        raise ProvisioningFailed(
            "the device did not confirm the reset (asleep, out of range or "
            "already reset). Wake it and try again, or tick \"remove anyway\""
        )

    data = json.loads(entry.data[CONF_CONNECT_JSON])
    data, addresses = remove_node(data, unicast)
    options = dict(entry.options)
    options[CONF_SG_SWITCHES] = [u for u in options.get(CONF_SG_SWITCHES, []) if u != unicast]
    levels = dict(options.get(CONF_SG_MIN_LEVEL, {}))
    for address in addresses:
        levels.pop(str(address), None)
    options[CONF_SG_MIN_LEVEL] = levels
    # Every pairing to or from it: a removed switch was unpaired above (on
    # button 4 only, as the menu pairs), and a removed dimmer is gone with its
    # table. The record follows either way.
    gone = set(addresses)
    options[CONF_SG_PAIRINGS] = [
        raw
        for raw in options.get(CONF_SG_PAIRINGS, [])
        if not (isinstance(raw, dict) and {raw.get("node"), raw.get("switch")} & gone)
    ]
    options[CONF_RETIRED_UNICASTS] = sorted(
        {*options.get(CONF_RETIRED_UNICASTS, []), *addresses}
    )
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_CONNECT_JSON: json.dumps(data, indent=2)},
        options=options,
    )

    registry = dr.async_get(hass)
    prefix = f"{network.identifier}_"
    wanted = {f"{prefix}{a:04x}" for a in addresses}
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if any(domain == DOMAIN and ident in wanted for domain, ident in device.identifiers):
            registry.async_remove_device(device.id)

    await hass.config_entries.async_reload(entry.entry_id)
    return {"unicast": f"0x{unicast:04x}", "reset": reset, "unpaired": unpaired}


# ------------------------------------------------------------------- backup


async def async_write_backup(hass: HomeAssistant, entry) -> str:
    """Write the stored network (keys, nodes, device keys) to ``/config``."""
    data = json.loads(entry.data[CONF_CONNECT_JSON])
    slug = "".join(c if c.isalnum() else "_" for c in (entry.title or "mesh")).strip("_")
    path = hass.config.path(f"bluetooth_mesh_backup_{slug or 'mesh'}.json")
    text = json.dumps(data, indent=2)

    def _write() -> None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    await hass.async_add_executor_job(_write)
    return path
