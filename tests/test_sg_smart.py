"""SG Smart 3.0 vendor encoding, pinned to bytes verified on a real Pill 3.0."""

import pytest

from btmesh.access import parse_access
from btmesh.sg_smart import (
    OP_SG_STATUS,
    SgPushSwitchMode,
    SgStatus,
    SgTrim,
    SgTrimLevels,
    is_sg_ack,
    parse_sg_status,
    parse_sg_trim,
    sg_fading_set,
    sg_power_level_set,
    sg_product,
    sg_push_switch_set,
    sg_status_get,
    sg_trim_get,
    sg_trim_set,
)


def test_power_level_matches_hardware_verified_bytes():
    # Sent from nRF Mesh (op 0x20, params 070101000332FFFFFFFFFF) -> 50 %.
    assert sg_power_level_set(0x0003, 50, tid=1, seq=1) == bytes.fromhex(
        "e0e80e070101000332ffffffffff"
    )


def test_status_request_matches_hardware_verified_bytes():
    assert sg_status_get(0x0003, tid=6, seq=1) == bytes.fromhex("e0e80e0906010003")


def test_status_reply_off_keeps_level():
    # Reply to Home Assistant (src 0x7FFF) after switching off from 100 %.
    opcode, params = parse_access(
        bytes.fromhex("f0e80e090100007fff00641900000000000000000000000000")
    )
    assert opcode == OP_SG_STATUS
    assert parse_sg_status(params) == SgStatus(kind=9, on=False, level=100)


def test_broadcast_on_and_off():
    on = parse_sg_status(bytes.fromhex("03a684ffff011e1900000000000000000000000000"))
    off = parse_sg_status(bytes.fromhex("03f585ffff001e1900000000000000000000000000"))
    assert on == SgStatus(kind=3, on=True, level=30)
    assert off == SgStatus(kind=3, on=False, level=30)


def test_unknown_kind_is_ignored():
    assert parse_sg_status(bytes.fromhex("2a0000")) is None


# ---------------------------------------------------------------- switches

from btmesh.sg_smart import (  # noqa: E402
    SgSwitchCommand,
    parse_sg_button_event,
    sg_pair_switch,
)


def test_pair_switch_matches_verified_payload():
    # Sent by hand on 2026-09-27; wheel 0x0004 -> pill 0x0003: press toggles,
    # turning dims up/down, hold does nothing.
    assert sg_pair_switch(
        0x0003, 0x0004, 4,
        press=SgSwitchCommand.TOGGLE_ON_OFF,
        hold=SgSwitchCommand.NONE,
        rotate=SgSwitchCommand.TOGGLE_DIM,
        tid=0x0A, seq=0x0A,
    ) == bytes.fromhex("e0e80e130a0a00030004040affffff0bff0bff")


def test_unpair_is_all_none():
    none = SgSwitchCommand.NONE
    payload = sg_pair_switch(3, 4, 4, press=none, hold=none, rotate=none, tid=1, seq=1)
    assert payload[-8:] == b"\xff" * 8


def test_pair_switch_rejects_bad_switch():
    import pytest
    with pytest.raises(ValueError):
        sg_pair_switch(3, 0, 4, tid=1, seq=1)


# Captured from an SG Smart 3.0 dimmer wheel (0x0004) on 2026-09-27.
PRESS = "2a0a0100041d04010100000000"
ROTATE_UP_1 = ["2a010100041d04050100000000", "2a010200041d04050101000000",
               "2a010300041d04050101010000"]
ROTATE_UP_FAST = ["2a470100041d04050800000000", "2a470200041d04050908000000",
                  "2a470300041d04050109080000", "2a470400041d04050101090800"]
ROTATE_DOWN_FAST = ["2a090100041d0405fb00000000", "2a090200041d0405f8fb000000",
                    "2a090300041d0405fff8fb0000"]
HOLD = ["2a870100041c04020200000000", "2a870200041c04020202000000",
        "2a870300041c04020202020000"]


def _ev(hex_):
    ev = parse_sg_button_event(bytes.fromhex(hex_))
    assert ev is not None
    return ev


def test_parse_button_event_press():
    ev = _ev(PRESS)
    assert (ev.tid, ev.seq, ev.battery, ev.button, ev.action, ev.value) == (
        0x0A, 1, 0x1D, 4, 0x01, 1
    )


def test_parse_rotation_values_are_signed_newest_first():
    assert [_ev(h).value for h in ROTATE_UP_FAST] == [8, 9, 1, 1]
    assert [_ev(h).value for h in ROTATE_DOWN_FAST] == [-5, -8, -1]
    assert [_ev(h).seq for h in ROTATE_UP_1] == [1, 2, 3]
    assert {_ev(h).action for h in ROTATE_UP_1} == {0x05}


def test_parse_hold():
    assert {(_ev(h).action, _ev(h).tid) for h in HOLD} == {(0x02, 0x87)}


def test_parse_button_event_ignores_other_kinds_and_short():
    assert parse_sg_button_event(bytes.fromhex("03a684ffff011e1900")) is None
    assert parse_sg_button_event(bytes.fromhex("2a1105ffff5a04")) is None


# ------------------------------------------------------------ device settings


def test_trim_read_matches_hardware_verified_bytes():
    # Sent to pill 0x0008 with send_raw on 2026-10-17; it answered.
    assert sg_trim_get(0x0008, tid=0x21, seq=0x21) == bytes.fromhex(
        "e0e80e06212100080400"
    )


def test_trim_reply_from_hardware():
    # The answer to the read above: min 10 %, start-up 30 %, max 100 %.
    opcode, params = parse_access(bytes.fromhex("f0e80e060121217fff143c64"))
    assert opcode == OP_SG_STATUS
    assert parse_sg_trim(params) == SgTrimLevels(min=10.0, start_up=30.0, max=100)


def test_max_trim_matches_hardware_verified_bytes():
    # Max 50 %, which visibly capped the lamp on 2026-10-17.
    assert sg_trim_set(0x0008, SgTrim.MAX, 50, tid=0x22, seq=0x22) == bytes.fromhex(
        "e0e80e06222200080332"
    )


def test_min_and_start_up_trim_travel_in_half_percent():
    assert sg_trim_set(0x0008, SgTrim.MIN, 40, tid=1, seq=1)[-2:] == bytes([1, 80])
    assert sg_trim_set(0x0008, SgTrim.MIN, 0.5, tid=1, seq=1)[-2:] == bytes([1, 1])
    assert sg_trim_set(0x0008, SgTrim.START_UP, 30, tid=1, seq=1)[-2:] == bytes(
        [2, 60]
    )


@pytest.mark.parametrize(
    ("trim", "percent"),
    [(SgTrim.MIN, 0), (SgTrim.MIN, 64), (SgTrim.START_UP, 40.5), (SgTrim.MAX, 39)],
)
def test_trim_outside_the_apps_range_is_refused(trim, percent):
    # 64 % as a minimum is what the node ignored on 2026-09-27.
    with pytest.raises(ValueError):
        sg_trim_set(0x0008, trim, percent, tid=1, seq=1)


def test_trim_parser_ignores_short_and_other_kinds():
    assert parse_sg_trim(bytes.fromhex("0601212100")) is None  # a bare ack
    assert parse_sg_trim(bytes.fromhex("090121217fff143c64")) is None
    assert parse_sg_trim(bytes.fromhex("060021217fff143c64")) is None


def test_push_switch_payload():
    assert sg_push_switch_set(
        0x0008, SgPushSwitchMode.SENSOR, tid=5, seq=5
    ) == bytes.fromhex("e0e80e0d0505000802")
    assert sg_push_switch_set(0x0008, SgPushSwitchMode.NORMAL, tid=5, seq=5)[-1] == 1


def test_fading_payload_and_choices():
    assert sg_fading_set(0x0008, 30, tid=0x25, seq=0x25) == bytes.fromhex(
        "e0e80e0e252500081e01"
    )
    with pytest.raises(ValueError):
        sg_fading_set(0x0008, 15, tid=1, seq=1)


def test_ack_is_kind_then_one():
    assert is_sg_ack(bytes.fromhex("0d01050500"), 0x0D)
    assert not is_sg_ack(bytes.fromhex("0d00050500"), 0x0D)
    assert not is_sg_ack(bytes.fromhex("0e01"), 0x0D)


def test_products():
    pill = sg_product(0xA2)
    assert pill.name == "LEDDim Smart Pill Micro 3.0"
    assert pill.dimmable and pill.push_input
    wheel = sg_product(0xB1)
    assert wheel.name == "Smart Wireless 3.0"
    assert not wheel.dimmable and not wheel.push_input
    on_off = sg_product(0xAB)
    assert not on_off.dimmable and on_off.push_input
    # Unknown: taken for a dimmer, as every SG node has been so far.
    assert sg_product(0).dimmable
