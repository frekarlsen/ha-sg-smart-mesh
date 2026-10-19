"""Telling identical new devices apart: blink first, or blink once added."""

from __future__ import annotations

import dataclasses
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_coordinator import FakeController, _make_entry, _patch_transport
from custom_components.bluetooth_mesh import provisioning as prov_mod
from custom_components.bluetooth_mesh.btmesh.access import (
    CompositionData,
    CompositionElement,
)
from custom_components.bluetooth_mesh.btmesh.sg_smart import SgStatus
from custom_components.bluetooth_mesh.const import CONF_CONNECT_JSON, DOMAIN
from custom_components.bluetooth_mesh.coordinator import MeshCoordinator
from custom_components.bluetooth_mesh.provisioning import (
    UnprovisionedBeacon,
    _default_name,
    async_finish_add,
    async_identify_unprovisioned,
    rename_node,
)

BEACON = UnprovisionedBeacon(
    address="AA:BB:CC:DD:EE:01", uuid=bytes(range(16)), name="SG", rssi=-50
)
CAPABILITIES = bytes.fromhex("01" + "0100010000000000000000")


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


# ------------------------------------------------ blink before it is added


def _fake_link(answer: bool, sent: list):
    @asynccontextmanager
    async def link(hass, beacon, on_pdu, on_error):
        def send(pdu: bytes) -> None:
            sent.append(pdu)
            if answer and pdu[0] == 0x00:  # Invite -> Capabilities
                hass.loop.call_soon(on_pdu, CAPABILITIES)

        yield send

    return link


async def test_blink_first_sends_an_invite_with_attention_and_adds_nothing(hass) -> None:
    sent: list = []
    with patch.object(prov_mod, "_prov_link", _fake_link(True, sent)):
        assert await async_identify_unprovisioned(hass, BEACON, seconds=1)
    # Only the Invite: no Start, no keys, nothing provisioned.
    assert sent == [bytes([0x00, 1])]


async def test_blink_first_reports_a_device_that_does_not_answer(hass) -> None:
    sent: list = []
    with (
        patch.object(prov_mod, "_prov_link", _fake_link(False, sent)),
        patch.object(prov_mod, "IDENTIFY_ANSWER_TIMEOUT", 0.05),
    ):
        assert not await async_identify_unprovisioned(hass, BEACON, seconds=1)


# ------------------------------------------------------------ the name


def _comp(cid: int, pid: int) -> CompositionData:
    return CompositionData(
        page=0, cid=cid, pid=pid, vid=1, crpl=0x40, features=0x0003,
        elements=(
            CompositionElement(loc=0, sig_models=(0x0000,), vendor_models=((0x0EE8, 0),)),
        ),
    )


def test_a_new_device_is_called_after_its_product_until_named() -> None:
    assert _default_name(BEACON, 5) == "SG 0005"
    assert _default_name(BEACON, 5, _comp(0x0EE8, 0xA2)) == "LEDDim Smart Pill Micro 3.0 0005"
    unnamed = dataclasses.replace(BEACON, name="")
    assert _default_name(unnamed, 5, _comp(0x0059, 1)) == "Mesh device 0005"


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
