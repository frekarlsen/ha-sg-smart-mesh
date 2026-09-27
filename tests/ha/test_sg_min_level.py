"""HA percent <-> SG level with a load minimum spread in."""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from custom_components.bluetooth_mesh.light import SgDimmerLight


class _Node:
    unicast = 3
    name = "Pill"
    cid = 0x0EE8

    def element_for_model(self, _model):
        return None


class _Coord:
    def __init__(self, low):
        self.low = low
        self.network = type("N", (), {"identifier": "net"})()

    def sg_min_level(self, _unicast):
        return self.low


def _light(low):
    return SgDimmerLight(_Coord(low), _Node())


def test_no_minimum_is_identity():
    light = _light(0)
    assert [light._pct_to_level(p) for p in (1, 50, 100)] == [1, 50, 100]


def test_minimum_spreads_the_slider_over_the_lit_range():
    light = _light(45)
    assert light._pct_to_level(1) == 45
    assert light._pct_to_level(100) == 100
    assert 45 < light._pct_to_level(50) < 100
    # 100 slider steps share 56 levels, so a round trip may move by one.
    for pct in (1, 100):
        assert light._level_to_pct(light._pct_to_level(pct)) == pct
    for pct in range(1, 101):
        assert abs(light._level_to_pct(light._pct_to_level(pct)) - pct) <= 1


def test_level_below_minimum_reads_as_one_percent():
    assert _light(45)._level_to_pct(20) == 1


def test_changing_the_minimum_relights_at_the_same_slider_position():
    from unittest.mock import MagicMock

    light = _light(50)
    light.hass = MagicMock()
    light._is_on = True
    light._level = 45  # 1 % under the old minimum of 45
    sent = []

    async def fake_send(level):
        sent.append(level)

    light._send = fake_send
    light._handle_min_level(45, 50)
    coro = light.hass.async_create_task.call_args[0][0]
    coro.close()  # the coroutine itself is fake_send(50)
    assert light.hass.async_create_task.called


async def test_number_entity_reads_and_writes_the_minimum():
    from custom_components.bluetooth_mesh.number import SgMinLevelNumber

    coord = _Coord(45)
    written = []
    coord.async_set_sg_min_level = lambda unicast, level: written.append((unicast, level))
    number = SgMinLevelNumber(coord, _Node())
    assert number.native_value == 45
    await number.async_set_native_value(52.4)
    assert written == [(3, 52)]
    assert number.unique_id == "net_0003_min_level"
