"""Telling identical lamps apart: they blink once added, and on Identify."""

from __future__ import annotations

import dataclasses
import json
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_coordinator import FakeController, _make_entry, _patch_transport
from custom_components.bluetooth_mesh.btmesh.access import (
    CompositionData,
    CompositionElement,
)
from custom_components.bluetooth_mesh.btmesh.sg_smart import SgStatus
from custom_components.bluetooth_mesh.button import SgIdentifyButton
from custom_components.bluetooth_mesh.const import CONF_CONNECT_JSON, DOMAIN
from custom_components.bluetooth_mesh.coordinator import MeshCoordinator
from custom_components.bluetooth_mesh.provisioning import (
    UnprovisionedBeacon,
    _default_name,
    async_finish_add,
    rename_node,
    unfinished_nodes,
)

BEACON = UnprovisionedBeacon(
    address="AA:BB:CC:DD:EE:01", uuid=bytes(range(16)), name="SG", rssi=-50
)


# ------------------------------------------------- blink once added (SG)


async def _blinks(hass, before):
    entry = _make_entry(hass)
    with _patch_transport(FakeController()):
        coord = MeshCoordinator(hass, entry)
        await coord.async_start()
        levels = []

        async def set_level(unicast, level):
            levels.append(level)
            return True

        with (
            patch.object(coord, "async_sg_get_status", AsyncMock(return_value=before)),
            patch.object(coord, "async_sg_set_level", set_level),
        ):
            assert await coord.async_sg_identify(8, flashes=2, period=0)
    await coord.async_stop()
    return levels


async def test_a_lit_lamp_blinks_and_returns_to_its_level(hass) -> None:
    assert await _blinks(hass, SgStatus(kind=9, on=True, level=40)) == [0, 100, 0, 100, 40]


async def test_a_dark_lamp_blinks_and_goes_dark_again(hass) -> None:
    assert await _blinks(hass, SgStatus(kind=9, on=False, level=40)) == [0, 100, 0, 100, 0]


async def test_a_lamp_that_did_not_say_is_left_on(hass) -> None:
    # A brand new lamp does not always answer a status read straight away.
    assert await _blinks(hass, None) == [0, 100, 0, 100, 101]


# ------------------------------------------------------------ the name


def _comp(cid: int, pid: int) -> CompositionData:
    return CompositionData(
        page=0, cid=cid, pid=pid, vid=1, crpl=0x40, features=0x0003,
        elements=(
            CompositionElement(loc=0, sig_models=(0x0000,), vendor_models=((0x0EE8, 0),)),
        ),
    )


def test_a_new_device_is_called_after_its_product_until_named() -> None:
    assert _default_name("SG", 5) == "SG 0005"
    assert _default_name("SG", 5, _comp(0x0EE8, 0xA2)) == "LEDDim Smart Pill Micro 3.0 0005"
    assert _default_name("", 5, _comp(0x0059, 1)) == "Mesh device 0005"


def test_nodes_that_never_answered_are_found_to_finish() -> None:
    data = {"nodes": [
        {"unicastAddress": "0011", "name": "Dimmehjul_06", "configComplete": False},
        {"unicastAddress": "000F", "name": "Dimmehjul_04", "configComplete": True},
        {"unicastAddress": "0001", "name": "nRF Mesh"},  # from the export
    ]}
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_CONNECT_JSON: json.dumps(data)})
    assert unfinished_nodes(entry) == [(0x11, "Dimmehjul_06")]


async def test_the_name_given_is_stored_and_the_entry_reloaded(hass) -> None:
    data = {"nodes": [{"unicastAddress": "0005", "name": "SG 0005", "elements": [{}, {}]}]}
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_CONNECT_JSON: json.dumps(data)})
    entry.add_to_hass(hass)
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        await async_finish_add(hass, entry, 6, "Stue")  # its second element
    stored = json.loads(entry.data[CONF_CONNECT_JSON])
    assert stored["nodes"][0]["name"] == "Stue"
    reload.assert_awaited_once_with(entry.entry_id)
    # And the input is left alone.
    assert rename_node(data, 0x0099, "x") == data


# ------------------------------------------------------- the Identify button


class _Node:
    unicast = 8

    def element_for_model(self, _model):
        return None


class _Coord:
    available = True

    def __init__(self, sent: bool = True):
        self.network = type("N", (), {"identifier": "net"})()
        self.blinked: list[int] = []
        self._sent = sent

    async def async_sg_identify(self, unicast):
        self.blinked.append(unicast)
        return self._sent


async def test_identify_blinks_the_lamp() -> None:
    coord = _Coord()
    button = SgIdentifyButton(coord, _Node())
    assert button.unique_id == "net_0008_identify"
    assert button.device_class == "identify"
    await button.async_press()
    assert coord.blinked == [8]


async def test_identify_says_so_when_the_lamp_cannot_be_reached() -> None:
    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError) as err:
        await SgIdentifyButton(_Coord(sent=False), _Node()).async_press()
    assert err.value.translation_key == "sg_identify_failed"


# --------------------------------- a wheel heard through one proxy node only


class _Wheel(FakeController):
    """Answers only when HA reaches the mesh through ``reachable_via``."""

    def __init__(self, coord_ref: list, reachable_via: str) -> None:
        super().__init__()
        self._coord_ref = coord_ref
        self._via = reachable_via
        self.composition = CompositionData(
            page=0, cid=0x0EE8, pid=0xB1, vid=1, crpl=0x40, features=0x0000,
            elements=(
                CompositionElement(loc=0, sig_models=(0x0000,), vendor_models=((0x0EE8, 0),)),
            ),
        )

    async def get_composition(self, unicast, *, page=0, timeout=5.0):
        self.calls.append(("get_composition", self._coord_ref[0].proxy_address))
        if self._coord_ref[0].proxy_address != self._via:
            return None
        return self.composition

    async def add_app_key(self, unicast, **kw):
        return 0

    async def bind_model(self, element, model, *, unicast, **kw):
        return 0

    async def set_publication(self, element, model, address, *, unicast, **kw):
        return 0


def _two_proxies(found: list):
    def find(hass, net_key, *, max_age=None, exclude=()):
        found.append(set(exclude))
        for address in ("AA:AA:AA:AA:AA:01", "BB:BB:BB:BB:BB:02"):
            if address not in exclude:
                return address
        return None

    return find


async def test_a_silent_wheel_is_tried_through_another_proxy_node(hass) -> None:
    """2026-10-19: wheels answered through one pill and not through the one HA
    had moved to. Configuration now moves on until one of them gets through."""
    from custom_components.bluetooth_mesh import coordinator as coordinator_mod

    entry = _make_entry(hass)
    ref: list = []
    wheel = _Wheel(ref, reachable_via="BB:BB:BB:BB:BB:02")
    found: list = []
    with (
        _patch_transport(wheel),
        patch.object(coordinator_mod, "find_proxy_address", _two_proxies(found)),
        patch.object(coordinator_mod, "CONFIGURE_SECONDS_PER_PROXY", 0.3),
    ):
        coord = MeshCoordinator(hass, entry)
        ref.append(coord)
        await coord.async_start()
        report, comp = await coord.async_configure_node_full(0x12, seconds=5)
        assert comp is wheel.composition
        assert report["proxy"] == "BB:BB:BB:BB:BB:02"
        assert report["publication"]["address"] == "0xffff"  # a wheel: to all
        # Tried through the first node, then the second; avoids nothing after.
        tried = [via for name, via in wheel.calls if name == "get_composition"]
        assert tried[0] == "AA:AA:AA:AA:AA:01" and tried[-1] == "BB:BB:BB:BB:BB:02"
        assert coord._proxy_avoid == set()
    await coord.async_stop()


async def test_with_one_proxy_node_it_keeps_trying_that_one(hass) -> None:
    from custom_components.bluetooth_mesh import coordinator as coordinator_mod

    entry = _make_entry(hass)
    ref: list = []
    wheel = _Wheel(ref, reachable_via="nowhere")
    with (
        _patch_transport(wheel, address="AA:AA:AA:AA:AA:01"),
        patch.object(coordinator_mod, "CONFIGURE_SECONDS_PER_PROXY", 0.2),
    ):
        coord = MeshCoordinator(hass, entry)
        ref.append(coord)
        await coord.async_start()
        report, comp = await coord.async_configure_node_full(0x12, seconds=1)
        assert comp is None
        assert "no Composition Data reply" in report["error"]
        assert {via for name, via in wheel.calls if name == "get_composition"} == {
            "AA:AA:AA:AA:AA:01"
        }
    await coord.async_stop()


# ------------------------------------------------- a device that does not answer


def _link(on_send):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def link(hass, beacon, on_pdu, on_error):
        yield lambda pdu: on_send(hass, pdu, on_pdu)

    return link


async def test_no_answer_to_the_invite_fails_fast_and_needs_no_reset(hass) -> None:
    from custom_components.bluetooth_mesh import provisioning as prov_mod

    with (
        patch.object(prov_mod, "_prov_link", _link(lambda *a: None)),
        patch.object(prov_mod, "CAPABILITIES_TIMEOUT", 0.05),
    ):
        with pytest.raises(prov_mod.ProvisioningFailed) as err:
            await prov_mod.async_provision(
                hass, BEACON, net_key=bytes(16), net_key_index=0, iv_index=0,
                choose_address=lambda n: 0x20,
            )
    assert err.value.untouched
    assert "did not answer the invitation" in str(err.value)
    assert "-50 dBm" in str(err.value)  # the signal it was listed with


async def test_a_device_lost_after_it_got_the_keys_is_not_untouched(hass) -> None:
    """Silence after the Data PDU: it may hold our keys now, so a reset is due."""
    from types import SimpleNamespace

    from custom_components.bluetooth_mesh import provisioning as prov_mod

    class KeysThenSilence:
        """Answers the Invite, is sent the keys, then never says Complete."""

        def __init__(self, *args, send):
            self._send = send
            self.state = SimpleNamespace(name="WAIT_COMPLETE")
            self.on_capabilities = self.on_done = None

        def start(self):
            self._send(bytes([0x00, 0x00]))
            self.on_capabilities(SimpleNamespace(num_elements=1))
            self._send(bytes([0x07]) + bytes(33))  # Provisioning Data

        def handle_pdu(self, data):
            pass

    with (
        patch.object(prov_mod, "_prov_link", _link(lambda *a: None)),
        patch.object(prov_mod, "Provisioner", KeysThenSilence),
        patch.object(prov_mod, "PROVISIONING_TIMEOUT", 0.05),
    ):
        with pytest.raises(prov_mod.ProvisioningFailed) as err:
            await prov_mod.async_provision(
                hass, BEACON, net_key=bytes(16), net_key_index=0, iv_index=0,
                choose_address=lambda n: 0x20,
            )
    assert not err.value.untouched
    assert "WAIT_COMPLETE" in str(err.value)


async def test_a_broken_link_ends_the_wait_at_once_and_leaves_it_untouched(hass) -> None:
    from contextlib import asynccontextmanager

    from custom_components.bluetooth_mesh import provisioning as prov_mod

    @asynccontextmanager
    async def link(hass_, beacon, on_pdu, on_error):
        # The subscribe fails just after the Invite went out.
        yield lambda pdu: hass_.loop.call_soon(
            on_error, RuntimeError("GATT Protocol Error: Unlikely Error")
        )

    with (
        patch.object(prov_mod, "_prov_link", link),
        patch.object(prov_mod, "CAPABILITIES_TIMEOUT", 5.0),
    ):
        loop = hass.loop
        started = loop.time()
        with pytest.raises(prov_mod.ProvisioningFailed) as err:
            await prov_mod.async_provision(
                hass, BEACON, net_key=bytes(16), net_key_index=0, iv_index=0,
                choose_address=lambda n: 0x20,
            )
        assert loop.time() - started < 1.0  # not the 5 s timeout
    assert err.value.untouched
    assert "the link failed" in str(err.value) and "Unlikely Error" in str(err.value)


async def test_adding_tries_once_more_when_the_device_was_left_untouched(hass) -> None:
    from types import SimpleNamespace

    from custom_components.bluetooth_mesh import provisioning as prov_mod

    data = {"nodes": []}
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_CONNECT_JSON: json.dumps(data)})
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(
        network=SimpleNamespace(net_key=bytes(16), net_key_index=0, iv_index=0),
        src_addr=0x7FFF,
    )
    tries: list = []

    async def provision(*args, **kwargs):
        tries.append(1)
        raise prov_mod.ProvisioningFailed("it did not answer", untouched=True)

    with (
        patch.object(prov_mod, "async_provision", provision),
        patch.object(prov_mod.asyncio, "sleep", AsyncMock()),
    ):
        with pytest.raises(prov_mod.ProvisioningFailed):
            await prov_mod.async_add_device(hass, entry, BEACON)
    assert len(tries) == 2  # once more, then it gives up

    tries.clear()

    async def provision_touched(*args, **kwargs):
        tries.append(1)
        raise prov_mod.ProvisioningFailed("lost after the keys", untouched=False)

    with patch.object(prov_mod, "async_provision", provision_touched):
        with pytest.raises(prov_mod.ProvisioningFailed):
            await prov_mod.async_add_device(hass, entry, BEACON)
    assert len(tries) == 1  # it may hold our keys now: no blind retry


def test_a_weak_signal_is_flagged_in_the_list() -> None:
    near = dataclasses.replace(BEACON, rssi=-60)
    far = dataclasses.replace(BEACON, rssi=-85)
    assert "weak signal" not in near.label
    assert "weak signal" in far.label and "-85 dBm" in far.label
