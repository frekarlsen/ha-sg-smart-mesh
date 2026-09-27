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
from time import monotonic
from dataclasses import dataclass
from typing import Callable

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .btmesh.access import CompositionData
from .btmesh.bearer import PROV_SERVICE, GattBearer, parse_unprovisioned_service_data
from .btmesh.provisioner import Provisioner
from .btmesh.proxy_pdu import MSG_TYPE_PROVISIONING_PDU
from .btmesh.pump import BearerPump
from .const import (
    CONF_CONNECT_JSON,
    CONF_RETIRED_UNICASTS,
    CONF_SG_MIN_LEVEL,
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
    done = asyncio.Event()
    errors: list[BaseException] = []

    def fail(exc: BaseException) -> None:
        errors.append(exc)
        done.set()

    prov = Provisioner(
        net_key, net_key_index, iv_index, FIRST_NODE_ADDRESS, send=pump.put
    )

    def on_capabilities(caps) -> None:
        prov.unicast_addr = choose_address(max(1, caps.num_elements))

    prov.on_capabilities = on_capabilities
    prov.on_done = done.set
    pump.on_error = fail

    def on_message(msg_type: int, payload: bytes) -> None:
        if msg_type != MSG_TYPE_PROVISIONING_PDU:
            return
        try:
            prov.handle_pdu(payload)
        except BaseException as exc:  # noqa: BLE001 - surface to the waiter
            fail(exc)

    try:
        await bearer.start(on_message)
        pump.start()
        prov.start()
        try:
            await asyncio.wait_for(done.wait(), PROVISIONING_TIMEOUT)
        except TimeoutError:
            raise ProvisioningFailed(
                f"the device stopped answering (step {prov.state.name})"
            ) from None
        if errors:
            raise ProvisioningFailed(f"{errors[0]} (step {prov.state.name})")
    finally:
        await pump.stop()
        await bearer.stop()
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    if not prov.done or prov.device_key is None or prov.capabilities is None:
        raise ProvisioningFailed(f"ended without completing (step {prov.state.name})")
    return ProvisionResult(
        unicast=prov.unicast_addr,
        device_key=prov.device_key,
        num_elements=max(1, prov.capabilities.num_elements),
        uuid=beacon.uuid,
    )


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


async def async_add_device(
    hass: HomeAssistant, entry, beacon: UnprovisionedBeacon, name: str
) -> dict:
    """Provision, store, configure and expose one device. Returns a summary.

    Each stage is written to the stored network before the next begins, so a
    device that is provisioned but fails to configure (a sleeping wheel) is
    not lost: its device key is kept and ``configure_node`` can finish it.
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
        "unicast": f"0x{result.unicast:04x}",
        "configured": comp is not None,
    }
    if comp is None:
        summary["error"] = report.get("error", "no reply")
        return summary

    app_key_index = coordinator.network.app_key_for_models(
        CONTROLLED_MODEL_IDS
    ).index
    data = set_node_composition(data, result.unicast, comp, app_key_index)
    _store(hass, entry, data)
    switch = is_sg_switch(comp)
    summary["kind"] = "switch" if switch else "device"
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
    await hass.config_entries.async_reload(entry.entry_id)
    return summary


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
