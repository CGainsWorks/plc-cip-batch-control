"""The operator page and its JSON endpoint."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from conftest import PLC_DIR

from src.harness.cell import PROGRAM_ORDER
from src.harness.io_map import build_io_map
from src.iec61131.runtime import load
from src.services.hmi import HmiServer, HmiState
from src.services.modbus_tcp import ModbusBridge


@pytest.fixture
def served():
    runtime = load(PLC_DIR, PROGRAM_ORDER)
    bridge = ModbusBridge(runtime, build_io_map(runtime))
    state = HmiState(bridge, recipe_name="adaptive")
    server = HmiServer(state, "127.0.0.1", 0)
    server.serve_in_background()
    try:
        yield server, state, bridge
    finally:
        server.shutdown()
        server.server_close()


def get(server, path: str) -> bytes:
    url = "http://127.0.0.1:{0}{1}".format(server.port, path)
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - our own server
        return response.read()


def test_the_page_is_self_contained(served):
    server, _state, _bridge = served
    page = get(server, "/").decode("utf-8")
    assert "CIP-01" in page
    # No dependency on anything off the machine.
    assert "http://" not in page.replace("http://www.w3.org", "")
    assert "src=" not in page


def test_the_state_endpoint_renders_the_plc_image(served):
    server, state, bridge = served
    bridge.runtime.set_path("rTempReturnC", 71.5)
    bridge.runtime.set_path("rLevelBalanceTankPct", 42.0)
    state.refresh()
    payload = json.loads(get(server, "/api/state"))
    assert payload["unit_state"] == "IDLE"
    assert payload["phase"] == "IDLE"
    assert payload["temp_return_c"] == pytest.approx(71.5)
    assert payload["level_pct"] == pytest.approx(42.0)
    assert payload["recipe"] == "adaptive"
    assert any(valve["tag"].startswith("V01") for valve in payload["valves"])
    assert payload["alarms"] == []


def test_a_command_from_the_page_goes_through_the_coil_queue(served):
    server, _state, bridge = served
    url = "http://127.0.0.1:{0}/api/command".format(server.port)
    request = urllib.request.Request(
        url,
        data=json.dumps({"command": "start"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        assert json.loads(response.read())["accepted"] is True
    assert bridge.apply_writes() == 1
    assert bridge.runtime.get_path("xCmdStart") is True


def test_an_unknown_command_is_refused(served):
    server, _state, _bridge = served
    url = "http://127.0.0.1:{0}/api/command".format(server.port)
    request = urllib.request.Request(
        url,
        data=json.dumps({"command": "launch"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=5)  # noqa: S310
    assert excinfo.value.code == 400


def test_an_unknown_path_is_a_404(served):
    server, _state, _bridge = served
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(server, "/nope")
    assert excinfo.value.code == 404


def test_alarms_appear_on_the_page(served):
    server, state, bridge = served
    runtime = bridge.runtime
    runtime.set_path("xSafetyOk", False)
    for _ in range(20):
        runtime.scan(0.2)
    state.refresh()
    payload = json.loads(get(server, "/api/state"))
    tags = {alarm["tag"] for alarm in payload["alarms"]}
    assert "SAFETY_TRIP" in tags
