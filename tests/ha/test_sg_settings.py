"""SG Smart 3.0 device settings: trim levels, push-button input, fade-in."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.exceptions import HomeAssistantError

from .test_coordinator import (
    UNICAST,
    FakeController,
    _make_entry,
    _patch_transport,
)
from custom_components.bluetooth_mesh.btmesh.sg_smart import (
    SgPushSwitchMode,
    SgTrim,
    SgTrimLevels,
    sg_product,
)
from custom_components.bluetooth_mesh.const import CONF_SG_FADING, CONF_SG_PUSH_SWITCH
from custom_components.bluetooth_mesh.coordinator import MeshCoordinator
from custom_components.bluetooth_mesh.number import SgMinLevelNumber, SgTrimNumber
from custom_components.bluetooth_mesh.select import SgFadingSelect, SgPushSwitchSelect
from custom_components.bluetooth_mesh.sg_settings import sg_nodes


class SgFakeController(FakeController):
    """A pill: keeps its trim, acknowledges what it is told unless ``deaf``."""

    def __init__(self) -> None:
        super().__init__()
        self.trim = SgTrimLevels(min=10.0, start_up=30.0, max=100)
        self.deaf = False
        self.ignores_writes = False

    async def sg_get_trim(self, unicast, *, timeout=3.0, retries=1):
        self.calls.append(("sg_get_trim", unicast))
        await asyncio.sleep(0.01)  # on air: a request stays in flight a while
        return None if self.deaf else self.trim

    async def sg_set_trim(self, unicast, trim, percent, *, timeout=3.0, retries=1):
        self.calls.append(("sg_set_trim", unicast, trim, percent))
        self.tid = (self.tid + 1) & 0xFF
        if self.deaf or self.ignores_writes:
            return False
        field = {SgTrim.MIN: "min", SgTrim.START_UP: "start_up", SgTrim.MAX: "max"}
        self.trim = self.trim._replace(**{field[trim]: percent})
        return True

    async def sg_set_push_switch(self, unicast, mode, *, timeout=3.0, retries=1):
        self.calls.append(("sg_set_push_switch", unicast, mode))
        return not self.deaf

    async def sg_set_fading(self, unicast, tenths, *, timeout=3.0, retries=1):
        self.calls.append(("sg_set_fading", unicast, tenths))
        return not self.deaf


# ---------------------------------------------------------------- coordinator


async def test_a_trim_write_is_read_back_and_kept(hass) -> None:
    entry = _make_entry(hass)
    fake = SgFakeController()
    with _patch_transport(fake):
        coord = MeshCoordinator(hass, entry)
        await coord.async_start()
        assert await coord.async_sg_set_trim(UNICAST, SgTrim.MIN, 40)
        assert coord.sg_trim(UNICAST) == SgTrimLevels(40, 30.0, 100)
        assert fake.calls[-2:] == [
            ("sg_set_trim", UNICAST, SgTrim.MIN, 40),
            ("sg_get_trim", UNICAST),
        ]
    await coord.async_stop()


async def test_a_write_the_node_did_not_take_is_reported(hass) -> None:
    entry = _make_entry(hass)
    fake = SgFakeController()
    fake.ignores_writes = True
    with _patch_transport(fake):
        coord = MeshCoordinator(hass, entry)
        await coord.async_start()
        assert not await coord.async_sg_set_trim(UNICAST, SgTrim.MAX, 60)
        # What the node really holds is still what shows.
        assert coord.sg_trim(UNICAST).max == 100
    await coord.async_stop()


async def test_concurrent_trim_requests_read_once(hass) -> None:
    entry = _make_entry(hass)
    fake = SgFakeController()
    with _patch_transport(fake):
        coord = MeshCoordinator(hass, entry)
        await coord.async_start()
        for _ in range(3):  # three number entities asking at once
            coord.async_request_sg_trim(UNICAST)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert [c for c in fake.calls if c[0] == "sg_get_trim"] == [
            ("sg_get_trim", UNICAST)
        ]
        assert coord.sg_trim(UNICAST) == fake.trim
    await coord.async_stop()


async def test_push_input_and_fading_are_remembered_once_acknowledged(hass) -> None:
    entry = _make_entry(hass)
    fake = SgFakeController()
    with _patch_transport(fake):
        coord = MeshCoordinator(hass, entry)
        await coord.async_start()
        assert coord.sg_push_switch(UNICAST) is None
        assert await coord.async_sg_set_push_switch(UNICAST, SgPushSwitchMode.SENSOR)
        assert await coord.async_sg_set_fading(UNICAST, 20)
        assert coord.sg_push_switch(UNICAST) is SgPushSwitchMode.SENSOR
        assert coord.sg_fading(UNICAST) == 20
        # JSON-safe: string keys, plain ints.
        assert entry.options[CONF_SG_PUSH_SWITCH] == {str(UNICAST): 2}
        assert entry.options[CONF_SG_FADING] == {str(UNICAST): 20}

        fake.deaf = True
        assert not await coord.async_sg_set_fading(UNICAST, 30)
        assert coord.sg_fading(UNICAST) == 20  # unconfirmed: not recorded
    await coord.async_stop()


# ------------------------------------------------------------------- entities


class _Node:
    unicast = 8
    name = "Pill"
    cid = 0x0EE8
    pid = 0xA2

    def __init__(self, unicast=8, pid=0xA2):
        self.unicast = unicast
        self.pid = pid

    def element_for_model(self, _model):
        return None

    def has_model(self, _model):
        return True


class _Coord:
    available = True

    def __init__(self):
        self.network = type("N", (), {"identifier": "net", "nodes": []})()
        self.entry = type("E", (), {"entry_id": "e"})()
        self.levels: SgTrimLevels | None = SgTrimLevels(10.0, 30.0, 100)
        self.written: list = []
        self.accept = True
        self.push = None
        self.fading = None
        self.sg_switches: list[int] = []

    def sg_trim(self, _unicast):
        return self.levels

    async def async_sg_set_trim(self, unicast, trim, percent):
        self.written.append((unicast, trim, percent))
        return self.accept

    def sg_push_switch(self, _unicast):
        return self.push

    async def async_sg_set_push_switch(self, unicast, mode):
        self.written.append((unicast, mode))
        if self.accept:
            self.push = mode
        return self.accept

    def sg_fading(self, _unicast):
        return self.fading

    async def async_sg_set_fading(self, unicast, tenths):
        self.written.append((unicast, tenths))
        if self.accept:
            self.fading = tenths
        return self.accept


def test_trim_numbers_show_what_the_dimmer_holds():
    coord = _Coord()
    numbers = {t: SgTrimNumber(coord, _Node(), t) for t in SgTrim}
    assert numbers[SgTrim.MIN].native_value == 10.0
    assert numbers[SgTrim.START_UP].native_value == 30.0
    assert numbers[SgTrim.MAX].native_value == 100
    assert numbers[SgTrim.MIN].unique_id == "net_0008_trim_min"
    # The app's ranges and steps.
    assert (numbers[SgTrim.MIN].native_max_value, numbers[SgTrim.MIN].native_step) == (
        40.0,
        0.5,
    )
    assert numbers[SgTrim.MAX].native_min_value == 40.0
    # The start-up kick is never offered below the minimum.
    assert numbers[SgTrim.START_UP].native_min_value == 10.0
    coord.levels = None
    assert numbers[SgTrim.MIN].native_value is None


async def test_trim_number_rounds_to_the_step_and_reports_a_refusal():
    coord = _Coord()
    number = SgTrimNumber(coord, _Node(), SgTrim.MIN)
    number.async_write_ha_state = lambda: None
    await number.async_set_native_value(37.3)
    assert coord.written == [(8, SgTrim.MIN, 37.5)]
    coord.accept = False
    with pytest.raises(HomeAssistantError):
        await number.async_set_native_value(20)


async def test_selects_write_and_show_the_last_value():
    coord = _Coord()
    push = SgPushSwitchSelect(coord, _Node())
    push.async_write_ha_state = lambda: None
    fading = SgFadingSelect(coord, _Node())
    fading.async_write_ha_state = lambda: None
    assert push.current_option is None and fading.current_option is None
    await push.async_select_option("sensor")
    await fading.async_select_option("fade_20")
    assert coord.written == [(8, SgPushSwitchMode.SENSOR), (8, 20)]
    assert push.current_option == "sensor"
    assert fading.current_option == "fade_20"
    assert fading.options == ["fade_5", "fade_10", "fade_20", "fade_30"]
    coord.accept = False
    with pytest.raises(HomeAssistantError):
        await push.async_select_option("normal")


def test_which_nodes_get_which_settings():
    coord = _Coord()
    pill, wheel, on_off, heard_wheel = (
        _Node(8, 0xA2),
        _Node(6, 0xB1),
        _Node(9, 0xAB),
        _Node(7, 0),
    )
    coord.network.nodes = [pill, wheel, on_off, heard_wheel]
    coord.sg_switches = [7]  # a wheel heard on the mesh, PID unknown
    found = {node.unicast: product for node, product in sg_nodes(coord)}
    assert set(found) == {8, 6, 9}
    assert found[8].dimmable and found[8].push_input
    assert not found[6].dimmable and not found[6].push_input
    assert not found[9].dimmable and found[9].push_input


def test_min_level_number_is_unchanged():
    coord = _Coord()
    coord.sg_min_level = lambda _u: 50
    number = SgMinLevelNumber(coord, _Node())
    assert number.native_value == 50
    assert number.unique_id == "net_0008_min_level"


def test_product_name_for_the_device_page():
    assert sg_product(0xA2).name == "LEDDim Smart Pill Micro 3.0"
