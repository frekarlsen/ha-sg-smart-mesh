"""Wheel frames -> Home Assistant events, from a real capture (2026-09-27)."""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.bluetooth_mesh.btmesh.sg_smart import parse_sg_button_event
from custom_components.bluetooth_mesh.sg_switch import battery_voltage, event_for


def _ev(hex_):
    return parse_sg_button_event(bytes.fromhex(hex_))


def test_press():
    assert event_for(_ev("2a0a0100041d04010100000000"), set()) == (
        "press", {"button": 4}
    )


def test_rotation_up_and_down_carry_steps():
    holds: set = set()
    assert event_for(_ev("2a470200041d04050908000000"), holds) == (
        "rotate_up", {"button": 4, "steps": 9}
    )
    assert event_for(_ev("2a090100041d0405fb00000000"), holds) == (
        "rotate_down", {"button": 4, "steps": 5}
    )


def test_hold_fires_once_per_gesture():
    holds: set = set()
    frames = ["2a870100041c04020200000000", "2a870200041c04020202000000",
              "2a870300041c04020202020000"]
    fired = [event_for(_ev(f), holds) for f in frames]
    assert fired == [("hold", {"button": 4}), None, None]
    # The next hold (new TID) fires again.
    assert event_for(_ev("2a880100041c04020200000000"), holds)[0] == "hold"


def test_unknown_action_keeps_the_raw_bytes():
    kind, attrs = event_for(_ev("2a0b0100041d04090100000000"), set())
    assert kind == "unknown"
    assert attrs["action"] == "0x09"


def test_battery_voltage():
    assert battery_voltage(_ev("2a0a0100041d04010100000000")) == pytest.approx(2.9)
    assert battery_voltage(_ev("2a0a0100040004010100000000")) is None
