"""SG switch pairings: Home Assistant's record of them, and where it shows.

The dimmer keeps a pairing in its own table, but a Pill Micro 3.0 left the
table read (kind 0x3A) unanswered, so what Home Assistant has sent is what the
"Controlled by" / "Controls" sensors and the pairing dialog show.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr

from .test_coordinator import FakeController, _make_entry, _patch_transport
from custom_components.bluetooth_mesh.btmesh.sg_smart import SgSwitchCommand
from custom_components.bluetooth_mesh.const import CONF_SG_PAIRINGS, DOMAIN
from custom_components.bluetooth_mesh.coordinator import MeshCoordinator
from custom_components.bluetooth_mesh.sensor import SgPairedSwitches, SgSwitchTargets
from custom_components.bluetooth_mesh.sg_settings import (
    SgPairing,
    command_name,
    load_pairings,
)

PILL, KITCHEN, WHEEL, SECOND_WHEEL = 0x0008, 0x0009, 0x0007, 0x0006
TOGGLE, DIM, NONE = SgSwitchCommand.TOGGLE_ON_OFF, SgSwitchCommand.TOGGLE_DIM, SgSwitchCommand.NONE


class PairingController(FakeController):
    async def sg_pair_switch(self, node, switch, button, *, press, hold, rotate, repeats=2):
        self.calls.append(("sg_pair_switch", node, switch, button, press, hold, rotate))


async def _pair(coord, node, switch, *, press=TOGGLE, hold=NONE, rotate=DIM, button=4):
    return await coord.async_sg_pair_switch(
        node, switch, button, press=press, hold=hold, rotate=rotate
    )


def _started(hass):
    entry = _make_entry(hass)
    fake = PairingController()
    return entry, fake


# ---------------------------------------------------------------- coordinator


async def test_a_sent_pairing_is_recorded_and_an_unpair_drops_it(hass) -> None:
    entry, fake = _started(hass)
    with _patch_transport(fake):
        coord = MeshCoordinator(hass, entry)
        await coord.async_start()
        assert coord.sg_pairings_sent == []

        assert await _pair(coord, PILL, WHEEL)
        assert await _pair(coord, KITCHEN, WHEEL)
        assert await _pair(coord, PILL, SECOND_WHEEL, rotate=NONE)
        assert coord.sg_pairings(PILL) == [
            SgPairing(PILL, SECOND_WHEEL, 4, TOGGLE, NONE, NONE),
            SgPairing(PILL, WHEEL, 4, TOGGLE, NONE, DIM),
        ]
        assert [p.node for p in coord.sg_switch_targets(WHEEL)] == [PILL, KITCHEN]
        # JSON-safe, so it survives a restart in the entry's options.
        assert entry.options[CONF_SG_PAIRINGS][0] == {
            "node": PILL, "switch": SECOND_WHEEL, "button": 4,
            "press": 0x0A, "hold": 0xFF, "rotate": 0xFF,
        }

        # Pairing the same button again replaces the entry, as in the dimmer.
        assert await _pair(coord, PILL, WHEEL, press=SgSwitchCommand.ON)
        assert [p.press for p in coord.sg_pairings(PILL) if p.switch == WHEEL] == [
            SgSwitchCommand.ON
        ]

        assert await _pair(coord, PILL, WHEEL, press=NONE, rotate=NONE)
        assert [p.node for p in coord.sg_switch_targets(WHEEL)] == [KITCHEN]
        assert [p.switch for p in coord.sg_pairings(PILL)] == [SECOND_WHEEL]
    await coord.async_stop()


async def test_a_pairing_that_was_not_sent_is_not_recorded(hass) -> None:
    entry, fake = _started(hass)
    with _patch_transport(fake):
        coord = MeshCoordinator(hass, entry)
        await coord.async_start()
        with patch.object(coord, "_run_connected", AsyncMock(return_value=None)):
            assert not await _pair(coord, PILL, WHEEL)
        assert coord.sg_pairings_sent == []
    await coord.async_stop()


def test_a_damaged_record_is_skipped_not_fatal() -> None:
    raw = [
        {"node": 8, "switch": 7, "button": 4, "press": 10, "hold": 255, "rotate": 11},
        {"node": 8, "switch": 6},  # a field missing
        {"node": "x", "switch": 6, "button": 4, "press": 1, "hold": 1, "rotate": 1},
        "not a dict",
    ]
    assert load_pairings(raw) == [SgPairing(8, 7, 4, 10, 255, 11)]


def test_commands_are_named_as_the_dialog_names_them() -> None:
    assert command_name(TOGGLE) == "toggle_on_off"
    assert command_name(DIM) == "dim"
    assert command_name(SgSwitchCommand.DIM_UP) == "dim_up"
    assert command_name(NONE) == "none"
    assert command_name(0x42) == "0x42"


# -------------------------------------------------------------------- sensors


class _Node:
    def __init__(self, unicast: int) -> None:
        self.unicast = unicast


def _device(hass, entry, coord, unicast: int, name: str):
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{coord.network.identifier}_{unicast:04x}")},
        name=name,
    )


async def _attach(hass, sensor) -> list:
    """Run the sensor's add hook with its state writes counted."""
    writes: list = []
    sensor.hass = hass
    sensor.async_write_ha_state = callback(lambda: writes.append(1))
    await sensor.async_added_to_hass()
    return writes


async def test_both_ends_show_the_pairing_by_device_name(hass) -> None:
    entry, fake = _started(hass)
    with _patch_transport(fake):
        coord = MeshCoordinator(hass, entry)
        await coord.async_start()
        _device(hass, entry, coord, PILL, "Pill")
        wheel = _device(hass, entry, coord, WHEEL, "SG Smart switch 0007")
        _device(hass, entry, coord, KITCHEN, "Kjøkken")

        dimmer_sensor = SgPairedSwitches(coord, _Node(PILL))
        wheel_sensor = SgSwitchTargets(coord, WHEEL)
        dimmer_writes = await _attach(hass, dimmer_sensor)
        wheel_writes = await _attach(hass, wheel_sensor)
        assert dimmer_sensor.native_value == "–"
        assert wheel_sensor.native_value == "–"
        assert dimmer_sensor.available  # Home Assistant's own record: no link needed

        await _pair(coord, PILL, WHEEL)
        await _pair(coord, KITCHEN, WHEEL)
        await hass.async_block_till_done()
        assert dimmer_writes and wheel_writes  # both ends were told
        assert dimmer_sensor.native_value == "SG Smart switch 0007"
        assert wheel_sensor.native_value == "Pill, Kjøkken"
        assert dimmer_sensor.extra_state_attributes == {
            "switches": [
                {
                    "name": "SG Smart switch 0007", "address": "0x0007", "button": 4,
                    "press": "toggle_on_off", "hold": "none", "rotate": "dim",
                }
            ]
        }
        assert [d["address"] for d in wheel_sensor.extra_state_attributes["dimmers"]] == [
            "0x0008", "0x0009",
        ]

        # A rename shows at once on the other end.
        written = len(dimmer_writes)
        dr.async_get(hass).async_update_device(wheel.id, name_by_user="Hjul stue")
        await hass.async_block_till_done()
        assert len(dimmer_writes) > written
        assert dimmer_sensor.native_value == "Hjul stue"
    await coord.async_stop()


async def test_a_name_without_a_device_falls_back_to_the_address(hass) -> None:
    entry, fake = _started(hass)
    with _patch_transport(fake):
        coord = MeshCoordinator(hass, entry)
        await coord.async_start()
        sensor = SgSwitchTargets(coord, WHEEL)
        await _attach(hass, sensor)
        await _pair(coord, 0x0123, WHEEL)
        assert sensor.native_value == "0x0123"
    await coord.async_stop()
