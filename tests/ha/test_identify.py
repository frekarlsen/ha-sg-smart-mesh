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
