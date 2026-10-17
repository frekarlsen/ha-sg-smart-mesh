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
    "KIND_TRIM",
    "KIND_PUSH_SWITCH",
    "KIND_FADING",
    "SgTrim",
    "SgTrimLevels",
    "SG_TRIM_RANGES",
    "sg_trim_get",
    "sg_trim_set",
    "parse_sg_trim",
    "SgPushSwitchMode",
    "sg_push_switch_set",
    "SG_FADING_TENTHS",
    "sg_fading_set",
    "is_sg_ack",
    "SgProduct",
    "sg_product",
    "KIND_SWITCH_TABLE",
    "SG_SWITCH_TABLE_PAGES",
    "SgButtonActions",
    "SgSwitchPairing",
    "sg_switch_table_get",
    "parse_sg_switch_table",
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


# ------------------------------------------------------- the switch table
#
# A node keeps the switches it reacts to in a table of 8 pages of 4 entries
# (``RequestSigMeshControllerSettingsTask``, which the app only runs from its
# hidden debug page). Read one page at a time:
#
#     3A TID SEQ NODE_HI NODE_LO FF FF PAGE        (PAGE 1..8)
#
# The answer is ``3A 01 TID SEQ ADDR_HI ADDR_LO PAGE`` followed by up to four
# 54-byte entries: the switch, a second address the app prints as "to", and
# five buttons of five (command, target) pairs — short press, long press,
# double press, long-press release, rotation — with the commands of
# ``SgSwitchCommand``.

KIND_SWITCH_TABLE = 0x3A
SG_SWITCH_TABLE_PAGES = 8
_TABLE_HEADER = 7
_TABLE_ENTRY = 54
_TABLE_BUTTONS = 5
_TABLE_BUTTON = 10


class SgButtonActions(NamedTuple):
    """What one switch button makes the node do; each is (command, target)."""

    press: tuple[int, int]
    hold: tuple[int, int]
    double_press: tuple[int, int]
    hold_release: tuple[int, int]
    rotate: tuple[int, int]

    @property
    def used(self) -> bool:
        return any(command != SgSwitchCommand.NONE for command, _ in self)


class SgSwitchPairing(NamedTuple):
    """One entry of a node's switch table."""

    switch: int
    target: int  # the second address the app prints as "to"
    buttons: dict[int, SgButtonActions]  # button index -> actions, used only


def sg_switch_table_get(unicast: int, page: int, *, tid: int, seq: int) -> bytes:
    """Access payload asking ``unicast`` for one page (1..8) of its switch table."""
    if not 1 <= page <= SG_SWITCH_TABLE_PAGES:
        raise ValueError(f"switch table page must be 1..{SG_SWITCH_TABLE_PAGES}")
    return (
        _opcode_bytes(_OP_BYTE_COMMAND)
        + _header(KIND_SWITCH_TABLE, unicast, tid, seq)
        + bytes([0xFF, 0xFF, page])
    )


def parse_sg_switch_table(params: bytes, page: int) -> list[SgSwitchPairing] | None:
    """The used entries of one switch-table page; None if not that page's answer."""
    if (
        len(params) < _TABLE_HEADER
        or not is_sg_ack(params, KIND_SWITCH_TABLE)
        or params[6] != page
    ):
        return None
    pairings = []
    body = params[_TABLE_HEADER:]
    for start in range(0, len(body) - _TABLE_ENTRY + 1, _TABLE_ENTRY):
        entry = body[start : start + _TABLE_ENTRY]
        switch = int.from_bytes(entry[0:2], "big")
        if switch in (0x0000, 0xFFFF):
            continue
        buttons = {}
        for index in range(_TABLE_BUTTONS):
            raw = entry[4 + index * _TABLE_BUTTON : 4 + (index + 1) * _TABLE_BUTTON]
            actions = SgButtonActions(*(tuple(raw[i : i + 2]) for i in range(0, 10, 2)))
            if actions.used:
                buttons[index] = actions
        if buttons:
            pairings.append(
                SgSwitchPairing(
                    switch=switch,
                    target=int.from_bytes(entry[2:4], "big"),
                    buttons=buttons,
                )
            )
    return pairings


# ------------------------------------------------------------ device settings
#
# Settings a node stores itself, so they hold for a wheel or the wall button
# as much as for Home Assistant. Worked out from the SG Smart app 5.0.958
# (``DeviceManager.sendSetSigMeshDeviceTrimLevelCommand``,
# ``sendRequestSigMeshDeviceTrimLevelCommand``,
# ``RequestTrimLevelTask.handleSigMeshTrimLevelResponse``,
# ``sendSetSigMeshDeviceExternalPushSwitchCommand``,
# ``sendSetSigMeshDeviceFadingTimeCommand``). Every one is acknowledged by the
# node with ``KIND 01 TID SEQ ...`` on ``OP_SG_STATUS``.


def is_sg_ack(params: bytes, kind: int) -> bool:
    """Whether ``OP_SG_STATUS`` parameters accept a command of ``kind``.

    The second byte is the outcome: ``01`` accepted, ``02`` refused (a node
    answered a 6 s fade-in, beyond the app's 3 s, with ``0e 02 ...``).
    """
    return len(params) >= 2 and params[0] == kind and params[1] == 0x01


# --- trim: minimum, start-up and maximum level (kind 0x06)
#
# The 27 September test that concluded the pill ignores trim wrote 35, 50 and
# 128 as raw bytes. The minimum travels in HALF percent, so that was 17.5 %,
# 25 % (both still dark on a load that lights at ~50 %) and 64 %, which is
# beyond the 40 % the app ever sends. Read back on 17 October from pill 0x0008
# (LEDDim Smart Pill Micro 3.0): ``06 01 21 21 7f ff 14 3c 64`` = min 10 %,
# start-up 30 %, max 100 % (the factory values), and a max of 50 % visibly
# capped the lamp. Trim works.

KIND_TRIM = 0x06
_TRIM_READ = 0x04


class SgTrim(IntEnum):
    """Which trim level a Set writes (the TYPE byte)."""

    MIN = 0x01  # what 1 % is mapped to
    START_UP = 0x02  # a short kick when a cold lamp is switched on
    MAX = 0x03  # what 100 % is mapped to


# The ranges the app offers, in percent. The node's own limits are unknown, so
# nothing outside what the app sends is ever written (64 % as a minimum was
# silently ignored, see above).
SG_TRIM_RANGES = {
    SgTrim.MIN: (0.5, 40.0),
    SgTrim.START_UP: (0.5, 40.0),
    SgTrim.MAX: (40.0, 100.0),
}


class SgTrimLevels(NamedTuple):
    """A node's trim, in percent (min and start-up in 0.5 % steps)."""

    min: float
    start_up: float
    max: int


def _trim_byte(trim: SgTrim, percent: float) -> int:
    low, high = SG_TRIM_RANGES[trim]
    if not low <= percent <= high:
        raise ValueError(f"SG {trim.name} trim must be {low}..{high} %, got {percent}")
    if trim is SgTrim.MAX:
        # Whole percent (``getSentTrimLevelValue``: max is sent as is).
        return int(percent)
    # Half percent, rounded like the app does (``round(level * 2)``).
    return round(percent * 2)


def sg_trim_get(unicast: int, *, tid: int, seq: int) -> bytes:
    """Access payload asking ``unicast`` for its trim levels."""
    return (
        _opcode_bytes(_OP_BYTE_COMMAND)
        + _header(KIND_TRIM, unicast, tid, seq)
        + bytes([_TRIM_READ, 0x00])
    )


def sg_trim_set(
    unicast: int, trim: SgTrim, percent: float, *, tid: int, seq: int
) -> bytes:
    """Access payload writing one trim level (percent) into ``unicast``."""
    trim = SgTrim(trim)
    return (
        _opcode_bytes(_OP_BYTE_COMMAND)
        + _header(KIND_TRIM, unicast, tid, seq)
        + bytes([trim, _trim_byte(trim, percent)])
    )


def parse_sg_trim(params: bytes) -> SgTrimLevels | None:
    """Parse a trim reply; None for anything else.

    ``06 01 TID SEQ ADDR_HI ADDR_LO MIN START_UP MAX`` — ADDR is whoever asked.
    The order is the app's (``TrimLevel(min: [9], startUp: [10], max: [11])``
    counted with the 3-octet opcode in front), and the read-back above fits it.
    """
    if len(params) < 9 or not is_sg_ack(params, KIND_TRIM):
        return None
    return SgTrimLevels(
        min=params[6] / 2, start_up=params[7] / 2, max=params[8]
    )


# --- external push switch input (kind 0x0D)


class SgPushSwitchMode(IntEnum):
    """What the node's push-button input (terminal P) expects.

    The app calls them "Normal" and "Sensor". Normal is the momentary push
    button of the manual: a short press toggles, holding it dims. Sensor is
    presumably a maintained contact, such as a motion sensor's relay.
    """

    NORMAL = 0x01
    SENSOR = 0x02


KIND_PUSH_SWITCH = 0x0D


def sg_push_switch_set(
    unicast: int, mode: SgPushSwitchMode, *, tid: int, seq: int
) -> bytes:
    """Access payload choosing what ``unicast``'s push-button input expects."""
    return (
        _opcode_bytes(_OP_BYTE_COMMAND)
        + _header(KIND_PUSH_SWITCH, unicast, tid, seq)
        + bytes([SgPushSwitchMode(mode)])
    )


# --- fading when switched on (kind 0x0E)

KIND_FADING = 0x0E
# The app's choices, in tenths of a second (0.5, 1, 2 and 3 s). Nothing longer:
# a pill refused 6 s (``0e 02 29 29 7fff 3c 01``, 2026-10-17).
SG_FADING_TENTHS = (5, 10, 20, 30)


def sg_fading_set(unicast: int, tenths: int, *, tid: int, seq: int) -> bytes:
    """Access payload setting ``unicast``'s fade-in time (tenths of a second).

    The last byte is always 01 in the app ("option ... always 1"); the
    dimming-speed choice it logs next to it never reaches a 3.0 node.
    """
    if tenths not in SG_FADING_TENTHS:
        raise ValueError(f"SG fading must be one of {SG_FADING_TENTHS}, got {tenths}")
    return (
        _opcode_bytes(_OP_BYTE_COMMAND)
        + _header(KIND_FADING, unicast, tid, seq)
        + bytes([tenths, 0x01])
    )


# ------------------------------------------------------------------ products
#
# The product ID (composition data PID) of each SG Smart 3.0 node, with the
# name the app gives it and what its device page offers in the default mode
# (``DeviceTypeExtension.sigMeshProductType``, ``getDeviceTypeName``,
# ``Device.hasLevelSetting``, ``supportsStartUpFading``,
# ``DeviceTypeExtension.supportsExternalPushSwitch``).


class SgProduct(NamedTuple):
    name: str
    dimmable: bool  # trim levels and fading
    push_input: bool  # an external push-switch input


_PRODUCTS: dict[int, SgProduct] = {
    0xA0: SgProduct("LEDDim Smart Push 3.0", True, True),
    0xA1: SgProduct("LEDDim Smart Pill 3.0", True, True),
    0xA2: SgProduct("LEDDim Smart Pill Micro 3.0", True, True),
    0xA3: SgProduct("LEDDim Smart Floor 3.0", True, False),
    0xA4: SgProduct("LEDDim Smart Ceiling 3.0", True, True),
    0xA5: SgProduct("LEDDim Smart 3.0", True, True),
    0xA6: SgProduct("LEDDim Smart 2-pole 3.0", True, True),
    0xA7: SgProduct("LEDDim Smart Master 3.0", True, True),
    0xA8: SgProduct("LEDDim Smart DIN 2-pole 3.0", True, True),
    0xA9: SgProduct("LEDDim Smart DIN 1000 3.0", True, False),
    0xAA: SgProduct("Smart Pill ON-OFF Micro PF 3.0", False, True),
    0xAB: SgProduct("Smart Pill ON-OFF Micro 3.0", False, True),
    0xAC: SgProduct("Smart Plug 3.0", False, False),
    0xAD: SgProduct("Smart Socket 3.0", False, False),
    0xAE: SgProduct("Smart DIN 3600 3.0", False, False),
    0xAF: SgProduct("Smart Switch 3.0", False, False),
    0xB0: SgProduct("Smart Switch DK 3.0", False, False),
    0xB1: SgProduct("Smart Wireless 3.0", False, False),
    0xB2: SgProduct("LEDDim Smart Color Controller 3.0", True, False),
    0xB3: SgProduct("LEDDim Smart Pill DALI 3.0", True, True),
    0xB4: SgProduct("HeatReg Smart 3.0", False, False),
    0xB5: SgProduct("Smart Gateway Wall 3.0", False, False),
    0xB6: SgProduct("Smart Gateway 3.0", False, False),
    0xB7: SgProduct("LEDDim Smart Driver 140mA 3.0", True, True),
    0xB8: SgProduct("LEDDim Smart Driver 350mA 3.0", True, True),
    0xB9: SgProduct("LEDDim Smart Driver Pill 3.0", True, True),
    0xBA: SgProduct("LEDDim Smart ZIP Track Dimmer 20VA 3.0", True, False),
    0xBB: SgProduct("TW LEDDim Smart Push 3.0", True, True),
    0xBC: SgProduct("TW LEDDim Smart Pill 3.0", True, True),
    0xBD: SgProduct("TW LEDDim Smart 3.0", True, True),
    0xBE: SgProduct("LEDDim Smart Driver 100W 3.0", True, True),
}

# A node whose PID is unknown (absent from an older export, or a product newer
# than this table) is taken for a dimmer with a push input, as every SG vendor
# node has been taken for a dimmer until now; a setting it lacks just goes
# unanswered.
_UNKNOWN_PRODUCT = SgProduct("SG Smart 3.0", True, True)


def sg_product(pid: int) -> SgProduct:
    """What the SG Smart 3.0 product ``pid`` is and which settings it has."""
    return _PRODUCTS.get(pid, _UNKNOWN_PRODUCT)
