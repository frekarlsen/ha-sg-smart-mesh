"""SG Armaturen "SG Smart 3.0" vendor protocol (company ID 0x0EE8).

SG Smart 3.0 devices are standard Bluetooth SIG Mesh nodes on a Telink stack,
but they expose no Light Lightness / Generic Level server: dimming, status and
most settings travel over ONE vendor opcode pair on vendor model 0x0EE8:0x0000.

Reverse-engineered from the SG Smart Android app 5.0.952
(``DeviceManager.sendSigMeshPowerLevelCommand``,
``sendRequestSigMeshDeviceStatusCommand``,
``RequestStatusTask.handleSigMeshStatusResponse*``) and verified on a LEDDim
Smart Pill 3.0 (product 0x00A2) on 2026-09-27.

Command (opcode ``0xE0`` + company, unacknowledged)::

    power/level  07 TID SEQ NODE_HI NODE_LO LEVEL CCT HUE_HI HUE_LO WHITE SAT
    status req   09 TID SEQ NODE_HI NODE_LO

The target node is carried INSIDE the payload (big-endian): the app sends to
0xFFFF, but addressing the node's own unicast works as well and keeps the
message off every other node's plate.

``LEVEL`` is 0 = off, 1..100 = percent, 101 = on at the last level. The colour
fields are 0xFF ("unchanged") for a plain dimmer.

Status (opcode ``0xF0`` + company) starts with a kind byte: ``0x09`` answers a
status request, ``0x03`` is published by the node on its own (local operation).
"""

from __future__ import annotations

from enum import IntEnum
from typing import NamedTuple

from .access import vendor_opcode

__all__ = [
    "SG_COMPANY_ID",
    "SG_VENDOR_MODEL_ID",
    "OP_SG_COMMAND",
    "OP_SG_STATUS",
    "SG_STATUS_GROUP",
    "SG_LEVEL_OFF",
    "SG_LEVEL_LAST",
    "SgStatus",
    "sg_power_level_set",
    "sg_status_get",
    "parse_sg_status",
    "SgSwitchCommand",
    "SgButtonEvent",
    "sg_pair_switch",
    "parse_sg_button_event",
    "SG_ACTION_PRESS",
    "SG_ACTION_HOLD",
    "SG_ACTION_ROTATE",
]

SG_COMPANY_ID = 0x0EE8
# Vendor model id as the network model stores it: company << 16 | model.
SG_VENDOR_MODEL_ID = (SG_COMPANY_ID << 16) | 0x0000

# Group address the integration listens on for status a node publishes by
# itself. Point the vendor model's publication (0x0EE8:0x0000) at it.
SG_STATUS_GROUP = 0xCEE8

_OP_BYTE_COMMAND = 0xE0
_OP_BYTE_STATUS = 0xF0
OP_SG_COMMAND = vendor_opcode(_OP_BYTE_COMMAND, SG_COMPANY_ID)
OP_SG_STATUS = vendor_opcode(_OP_BYTE_STATUS, SG_COMPANY_ID)

_KIND_POWER_LEVEL = 0x07
_KIND_STATUS_REQUEST = 0x09
KIND_STATUS_RESPONSE = 0x09
KIND_STATUS_BROADCAST = 0x03

SG_LEVEL_OFF = 0
SG_LEVEL_MAX = 100
SG_LEVEL_LAST = 101  # "on, at whatever level it had before"

# Power and level offsets in the status parameters, per kind. Verified on a
# LEDDim Smart Pill 3.0 on 2026-09-27:
#   0x09 reply     09 01 TID SEQ ADDR_HI ADDR_LO POWER LEVEL 19 ...
#                  (09 01 00 00 7f ff 00 64 19 = off, level 100)
#   0x03 broadcast 03 TID SEQ ff ff POWER LEVEL 19 ...  (sent to 0xFFFF)
#                  (03 a6 84 ff ff 01 1e 19 = on 30 %; ... 00 1e ... = off)
# The node keeps its level while off, so power is its own byte.
_STATUS_OFFSETS = {
    KIND_STATUS_RESPONSE: (6, 7),
    KIND_STATUS_BROADCAST: (5, 6),
}


class SgStatus(NamedTuple):
    kind: int
    on: bool
    level: int  # 0..100


def _header(kind: int, unicast: int, tid: int, seq: int) -> bytes:
    if not 0 < unicast <= 0xFFFF:
        raise ValueError(f"bad node address {unicast:#x}")
    return bytes([kind, tid & 0xFF, seq & 0xFF]) + unicast.to_bytes(2, "big")


def _opcode_bytes(op_byte: int) -> bytes:
    return bytes([op_byte]) + SG_COMPANY_ID.to_bytes(2, "little")


def sg_power_level_set(unicast: int, level: int, *, tid: int, seq: int) -> bytes:
    """Access payload (opcode included) setting power/level on ``unicast``."""
    if not SG_LEVEL_OFF <= level <= SG_LEVEL_LAST:
        raise ValueError(f"SG level must be 0..101, got {level}")
    return (
        _opcode_bytes(_OP_BYTE_COMMAND)
        + _header(_KIND_POWER_LEVEL, unicast, tid, seq)
        + bytes([level, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
    )


def sg_status_get(unicast: int, *, tid: int, seq: int) -> bytes:
    """Access payload (opcode included) asking ``unicast`` for its status."""
    return _opcode_bytes(_OP_BYTE_COMMAND) + _header(
        _KIND_STATUS_REQUEST, unicast, tid, seq
    )


def parse_sg_status(params: bytes) -> SgStatus | None:
    """Parse the parameters of an ``OP_SG_STATUS`` message; None if not mapped."""
    if not params:
        return None
    kind = params[0]
    offsets = _STATUS_OFFSETS.get(kind)
    if offsets is None:
        return None
    power_at, level_at = offsets
    if len(params) <= level_at:
        return None
    level = params[level_at]
    if level > SG_LEVEL_MAX:
        return None
    return SgStatus(kind=kind, on=params[power_at] > 0 and level > 0, level=level)


# --------------------------------------------------------------- switches
#
# A battery switch or dimmer wheel publishes its button events (kind 0x2A, see
# ``parse_sg_button_event``) and a mains node acts on them from a pairing
# table it keeps itself: the switch talks straight to the node, no gateway or
# Home Assistant in the loop. The table is written with kind 0x13, sent to the
# node that should react (``SetControllerSwitchTask`` / ``SigSwitchCommandData``
# in the app). Verified 2026-09-27: wheel 0x0004 -> pill 0x0003,
# ``13 TID SEQ 0003 0004 04 0aff ffff 0bff 0bff`` gives press = on/off, turning
# the wheel = dim up/down, hold = nothing.

_KIND_PAIR_SWITCH = 0x13
KIND_BUTTON_EVENT = 0x2A


class SgSwitchCommand(IntEnum):
    """What a paired node does on a switch action (app enum, wire values)."""

    OFF = 0x00
    ON = 0x01
    DIM_UP = 0x02
    DIM_DOWN = 0x03
    CCT_UP = 0x04
    CCT_DOWN = 0x05
    TOGGLE_ON_OFF = 0x0A
    TOGGLE_DIM = 0x0B
    TOGGLE_CCT = 0x0C
    SCENE = 0x13
    NONE = 0xFF


def sg_pair_switch(
    node: int,
    switch: int,
    button: int,
    *,
    press: int = SgSwitchCommand.TOGGLE_ON_OFF,
    hold: int = SgSwitchCommand.NONE,
    rotate: int = SgSwitchCommand.TOGGLE_DIM,
    tid: int,
    seq: int,
) -> bytes:
    """Access payload making ``node`` react to ``button`` on ``switch``.

    The eight setting bytes are four (command, target) pairs: press, hold,
    then two slots the wheel's rotation reads from. Which of those two it is
    has not been isolated, so ``rotate`` is written to both. Targets are 0xFF
    (none) — they only matter for scenes. All commands NONE unpairs.
    """
    if not 0 < switch <= 0x7FFF:
        raise ValueError(f"bad switch address {switch:#x}")
    if not 0 <= button <= 0xFF:
        raise ValueError(f"bad button index {button}")
    commands = [int(press), int(hold), int(rotate), int(rotate)]
    for command in commands:
        if not 0 <= command <= 0xFF:
            raise ValueError(f"bad switch command {command:#x}")
    settings = b"".join(bytes([command, 0xFF]) for command in commands)
    return (
        _opcode_bytes(_OP_BYTE_COMMAND)
        + _header(_KIND_PAIR_SWITCH, node, tid, seq)
        + switch.to_bytes(2, "big")
        + bytes([button])
        + settings
    )


# Action byte of a button event (verified on an SG Smart 3.0 dimmer wheel).
SG_ACTION_PRESS = 0x01
SG_ACTION_HOLD = 0x02
SG_ACTION_ROTATE = 0x05


class SgButtonEvent(NamedTuple):
    """One frame of a switch/wheel button event (status kind 0x2A)."""

    tid: int  # one gesture (a press, a hold, one turn of the wheel)
    seq: int  # frame within the gesture, from 1
    battery: int  # raw byte; tracks cell voltage in 0.1 V (0x1D = 2.9 V)
    button: int
    action: int
    values: bytes  # newest first, then the previous frames' values

    @property
    def value(self) -> int:
        """This frame's value, signed (rotation: detent steps, + = up)."""
        if not self.values:
            return 0
        raw = self.values[0]
        return raw - 0x100 if raw >= 0x80 else raw


def parse_sg_button_event(params: bytes) -> SgButtonEvent | None:
    """Parse ``OP_SG_STATUS`` parameters of kind 0x2A; None for anything else.

    Layout (``MeshDevice._handleSigSwitchButtonPressed`` indexes it with the
    3-byte opcode in front, hence 8/9/10 there), verified against a wheel::

        2A TID SEQ SRC_HI SRC_LO BATTERY BUTTON ACTION V1 V2 V3 V4 V5

    A gesture is sent as frames with the same TID and SEQ 1, 2, 3, ..., each
    usually twice. ``V1`` is the new frame's value and V2.. repeat the earlier
    frames' values, so a lost frame costs nothing. Press: action 01, V1 01.
    Hold: action 02, frames while held. Rotate: action 05, V1 = signed steps
    turned since the previous frame (01 one detent up, FB five down).
    """
    if len(params) < 9 or params[0] != KIND_BUTTON_EVENT:
        return None
    return SgButtonEvent(
        tid=params[1],
        seq=params[2],
        battery=params[5],
        button=params[6],
        action=params[7],
        values=bytes(params[8:]),
    )
