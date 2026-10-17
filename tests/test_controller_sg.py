"""MeshController's SG Smart 3.0 settings, against a device that answers like a pill.

The pill answers everything on one vendor opcode and also publishes its own
status there, so these pin that a settings request waits for ITS reply.
"""

from __future__ import annotations

import pytest
from test_controller import FIXTURE, UNICAST, FakeBearer

from btmesh.controller import MeshController
from btmesh.network_model import Network
from btmesh.node import MeshNode, ReceivedMessage
from btmesh.sg_smart import (
    OP_SG_COMMAND,
    SgPushSwitchMode,
    SgTrim,
    SgTrimLevels,
)

_STATUS = bytes.fromhex("f0e80e")


def make_pill(*, answer: bool = True):
    network = Network.from_connect_file(FIXTURE)
    bearer = FakeBearer()
    controller = MeshController(network, bearer, tid=0x40)
    seen: list[ReceivedMessage] = []
    trim = {1: 20, 2: 60, 3: 100}  # half %, half %, %

    device = MeshNode(
        netkey=network.net_key,
        appkey=network.app_key,
        iv_index=network.iv_index,
        src_addr=UNICAST,
        send_network_pdu=bearer.feed,
        seq=0x3000,
    )

    def reply(dst: int, params: bytes) -> None:
        device.send_access(dst, _STATUS + params)

    def responder(msg: ReceivedMessage) -> None:
        seen.append(msg)
        if msg.opcode != OP_SG_COMMAND or not answer:
            return
        kind, tid, seq = msg.params[0], msg.params[1], msg.params[2]
        # Its own status first, on the same opcode: must not be taken for
        # the answer.
        reply(0xFFFF, bytes([0x03, 0x11, 0x22, 0xFF, 0xFF, 0x01, 0x32, 0x19]))
        addr = msg.src.to_bytes(2, "big")
        if kind == 0x06 and msg.params[5] == 0x04:
            reply(msg.src, bytes([0x06, 0x01, tid, seq]) + addr
                  + bytes([trim[1], trim[2], trim[3]]))
        elif kind == 0x06:
            trim[msg.params[5]] = msg.params[6]
            reply(msg.src, bytes([0x06, 0x01, tid, seq]) + addr)
        elif kind == 0x0E and msg.params[5] > 30:
            # Refused, as a pill answered a 6 s fade-in.
            reply(msg.src, bytes([kind, 0x02, tid, seq]) + addr + msg.params[5:])
        elif kind in (0x0D, 0x0E):
            reply(msg.src, bytes([kind, 0x01, tid, seq]) + addr + msg.params[5:])

    device.on_message = responder
    bearer.device = device
    return controller, seen, trim


@pytest.fixture
async def pill():
    controllers = []

    async def start(**kw):
        controller, seen, trim = make_pill(**kw)
        await controller.start()
        controllers.append(controller)
        return controller, seen, trim

    yield start
    for controller in controllers:
        await controller.stop()


async def test_get_trim_waits_for_the_trim_reply(pill):
    controller, seen, _ = await pill()
    levels = await controller.sg_get_trim(UNICAST, timeout=0.5)
    assert levels == SgTrimLevels(min=10.0, start_up=30.0, max=100)
    assert seen[-1].params[0] == 0x06 and seen[-1].params[5:] == b"\x04\x00"


async def test_set_trim_is_acknowledged_and_lands(pill):
    controller, _, trim = await pill()
    assert await controller.sg_set_trim(UNICAST, SgTrim.MIN, 40, timeout=0.5)
    assert trim[1] == 80
    assert (await controller.sg_get_trim(UNICAST, timeout=0.5)).min == 40.0


async def test_push_switch_and_fading_are_acknowledged(pill):
    controller, seen, _ = await pill()
    assert await controller.sg_set_push_switch(
        UNICAST, SgPushSwitchMode.SENSOR, timeout=0.5
    )
    assert seen[-1].params[0] == 0x0D and seen[-1].params[-1] == 0x02
    assert await controller.sg_set_fading(UNICAST, 20, timeout=0.5)
    assert seen[-1].params[-2:] == bytes([20, 0x01])


async def test_silent_node_reads_none_and_writes_false(pill):
    controller, _, _ = await pill(answer=False)
    assert await controller.sg_get_trim(UNICAST, timeout=0.05, retries=0) is None
    assert not await controller.sg_set_trim(
        UNICAST, SgTrim.MAX, 80, timeout=0.05, retries=0
    )


async def test_a_refused_setting_is_false_at_once(pill):
    """Status 02 is a refusal: reported as such, not waited out as silence."""
    import time

    from btmesh import sg_smart

    controller, _, _ = await pill()
    payload = sg_smart.sg_fading_set(UNICAST, 30, tid=0x29, seq=0x29)
    payload = payload[:-2] + bytes([60, 0x01])  # 6 s, beyond what the app sends
    started = time.monotonic()
    assert not await controller._sg_set(
        UNICAST, payload, sg_smart.KIND_FADING, timeout=1.0, retries=1
    )
    assert time.monotonic() - started < 0.5
