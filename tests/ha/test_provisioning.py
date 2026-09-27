"""Adding a device: address choice, stored-network edits, backup, menu."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

from custom_components.bluetooth_mesh.btmesh.access import (
    CompositionData,
    CompositionElement,
)
from custom_components.bluetooth_mesh.btmesh.network_model import Network
from custom_components.bluetooth_mesh.const import MODEL_SG_VENDOR
from custom_components.bluetooth_mesh.coordinator import is_sg_switch
from custom_components.bluetooth_mesh.provisioning import (
    ProvisionResult,
    add_node,
    choose_unicast,
    set_node_composition,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample.connect.json"


def _data() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _pill_comp(features=0x0003) -> CompositionData:
    return CompositionData(
        page=0, cid=0x0EE8, pid=0x00A2, vid=0x0041, crpl=0x0080,
        features=features,
        elements=(
            CompositionElement(
                loc=0, sig_models=(0x0000, 0x0002, 0x1000),
                vendor_models=((0x0EE8, 0x0000), (0x0EE8, 0x0001)),
            ),
        ),
    )


def test_choose_unicast_skips_every_element_of_existing_nodes():
    data = {"nodes": [
        {"unicastAddress": "0002", "elements": [{}, {}]},  # 0002-0003
        {"unicastAddress": "0005", "elements": [{}]},
    ]}
    assert choose_unicast(data, 1) == 0x0004
    assert choose_unicast(data, 2) == 0x0006
    assert choose_unicast(data, 1, reserved={0x0004}) == 0x0006


def test_added_node_parses_and_then_gets_its_models():
    data = _data()
    unicast = choose_unicast(data, 1)
    result = ProvisionResult(
        unicast=unicast, device_key=bytes(range(16)), num_elements=1,
        uuid=bytes.fromhex("11223344556677889900aabbccddeeff"),
    )
    data = add_node(data, result, "Stue pille", 0)
    node = next(n for n in Network.from_connect(data).nodes if n.unicast == unicast)
    assert node.name == "Stue pille"
    assert node.device_key == bytes(range(16))
    assert not node.has_model(MODEL_SG_VENDOR)

    data = set_node_composition(data, unicast, _pill_comp(), 0)
    node = next(n for n in Network.from_connect(data).nodes if n.unicast == unicast)
    assert node.cid == 0x0EE8
    assert node.has_model(MODEL_SG_VENDOR)
    raw = next(n for n in data["nodes"] if n.get("name") == "Stue pille")
    assert raw["configComplete"] is True
    assert raw["UUID"] == "11223344-5566-7788-9900-AABBCCDDEEFF"


def test_switch_detection_by_missing_proxy_feature():
    assert not is_sg_switch(_pill_comp(features=0x0003))  # relay + proxy
    assert is_sg_switch(_pill_comp(features=0x0000))


async def test_backup_writes_the_stored_network(hass, tmp_path):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.bluetooth_mesh.const import CONF_CONNECT_JSON, DOMAIN
    from custom_components.bluetooth_mesh.provisioning import async_write_backup

    hass.config.config_dir = str(tmp_path)
    text = FIXTURE.read_text(encoding="utf-8")
    entry = MockConfigEntry(domain=DOMAIN, title="Hjem", data={CONF_CONNECT_JSON: text})
    path = await async_write_backup(hass, entry)
    assert Path(path).name == "bluetooth_mesh_backup_Hjem.json"
    assert json.loads(Path(path).read_text()) == json.loads(text)


def test_remove_node_drops_every_element_and_reports_the_addresses():
    from custom_components.bluetooth_mesh.provisioning import remove_node

    data = {"nodes": [
        {"unicastAddress": "0002", "elements": [{}, {}]},
        {"unicastAddress": "0005", "elements": [{}]},
    ]}
    out, addresses = remove_node(data, 0x0003)
    assert addresses == [2, 3]
    assert [n["unicastAddress"] for n in out["nodes"]] == ["0005"]
    assert len(data["nodes"]) == 2  # input untouched


async def test_async_remove_device_unpairs_resets_and_retires(hass):
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.bluetooth_mesh.const import (
        CONF_CONNECT_JSON,
        CONF_RETIRED_UNICASTS,
        CONF_SG_SWITCHES,
        DOMAIN,
    )
    from custom_components.bluetooth_mesh.provisioning import async_remove_device

    data = _data()
    pill = ProvisionResult(unicast=choose_unicast(data, 1), device_key=bytes(16),
                           num_elements=1, uuid=bytes(16))
    data = add_node(data, pill, "Pille", 0)
    data = set_node_composition(data, pill.unicast, _pill_comp(), 0)
    wheel = ProvisionResult(unicast=choose_unicast(data, 1), device_key=bytes(16),
                            num_elements=1, uuid=bytes(range(16)))
    data = add_node(data, wheel, "Hjul", 0)

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_CONNECT_JSON: json.dumps(data)},
        options={CONF_SG_SWITCHES: [wheel.unicast]},
    )
    entry.add_to_hass(hass)

    class _Coord:
        network = Network.from_connect(data)
        sg_switches = [wheel.unicast]
        pairs: list = []

        async def async_sg_pair_switch(self, node, switch, button, **kw):
            self.pairs.append((node, switch, int(kw["press"])))
            return True

        async def async_node_reset(self, unicast):
            return True

    coord = _Coord()
    entry.runtime_data = coord
    with patch.object(hass.config_entries, "async_reload", AsyncMock()):
        summary = await async_remove_device(hass, entry, wheel.unicast)

    assert summary["reset"] is True
    assert coord.pairs == [(pill.unicast, wheel.unicast, 0xFF)]
    stored = json.loads(entry.data[CONF_CONNECT_JSON])
    assert all(n.get("name") != "Hjul" for n in stored["nodes"])
    assert entry.options[CONF_SG_SWITCHES] == []
    assert wheel.unicast in entry.options[CONF_RETIRED_UNICASTS]
    # A retired address is never handed out again.
    assert choose_unicast(stored, 1, set(entry.options[CONF_RETIRED_UNICASTS])) != wheel.unicast


async def test_async_remove_device_refuses_a_silent_node_unless_forced(hass):
    from unittest.mock import AsyncMock, patch

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.bluetooth_mesh.const import CONF_CONNECT_JSON, DOMAIN
    from custom_components.bluetooth_mesh.provisioning import (
        ProvisioningFailed,
        async_remove_device,
    )

    data = _data()
    pill = ProvisionResult(unicast=choose_unicast(data, 1), device_key=bytes(16),
                           num_elements=1, uuid=bytes(16))
    data = set_node_composition(add_node(data, pill, "Pille", 0), pill.unicast, _pill_comp(), 0)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_CONNECT_JSON: json.dumps(data)})
    entry.add_to_hass(hass)

    class _Coord:
        network = Network.from_connect(data)
        sg_switches: list = []

        async def async_node_reset(self, unicast):
            return False

    entry.runtime_data = _Coord()
    with patch.object(hass.config_entries, "async_reload", AsyncMock()):
        with pytest.raises(ProvisioningFailed):
            await async_remove_device(hass, entry, pill.unicast)
        summary = await async_remove_device(hass, entry, pill.unicast, force=True)
    assert summary["reset"] is False
    assert all(n.get("name") != "Pille" for n in json.loads(entry.data[CONF_CONNECT_JSON])["nodes"])
