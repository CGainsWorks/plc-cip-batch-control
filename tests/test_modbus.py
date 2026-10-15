"""The Modbus TCP server, exercised over a real socket.

Nothing is faked below the wire format: the tests open a connection to the
server, issue the function codes a SCADA client issues and check that a coil
written by the client reaches the running program.
"""

from __future__ import annotations

import pytest
from conftest import PLC_DIR, fast_recipe

from src.harness.cell import PROGRAM_ORDER, CipCell
from src.harness.io_map import build_io_map, float_to_words, words_to_float
from src.iec61131.runtime import load
from src.plant.params import SCENARIOS
from src.services.modbus_tcp import ModbusBridge, ModbusClient, ModbusServer, build_response


@pytest.fixture
def bridge():
    runtime = load(PLC_DIR, PROGRAM_ORDER)
    return ModbusBridge(runtime, build_io_map(runtime))


@pytest.fixture
def server(bridge):
    server = ModbusServer(bridge, "127.0.0.1", 0)
    server.serve_in_background()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def test_reading_a_discrete_input_reflects_the_plc(bridge, server):
    point = bridge.io_map.find("xSafetyOk")
    bridge.runtime.set_path("xSafetyOk", True)
    bridge.refresh()
    with ModbusClient(port=server.port) as client:
        assert client.read_discrete_inputs(point.offset, 1) == [True]
    bridge.runtime.set_path("xSafetyOk", False)
    bridge.refresh()
    with ModbusClient(port=server.port) as client:
        assert client.read_discrete_inputs(point.offset, 1) == [False]


def test_reading_a_real_gives_back_the_value(bridge, server):
    point = bridge.io_map.find("rTempReturnC")
    bridge.runtime.set_path("rTempReturnC", 72.5)
    bridge.refresh()
    with ModbusClient(port=server.port) as client:
        assert client.read_float("input_register", point.offset) == pytest.approx(72.5)


def test_writing_a_command_coil_reaches_the_program(bridge, server):
    point = bridge.io_map.find("xCmdStart")
    with ModbusClient(port=server.port) as client:
        client.write_coil(point.offset, True)
    assert bridge.apply_writes() == 1
    assert bridge.runtime.get_path("xCmdStart") is True


def test_output_coils_are_read_only(bridge, server):
    point = bridge.io_map.find("xV01FreshWater")
    with ModbusClient(port=server.port) as client:
        with pytest.raises(IOError):
            client.write_coil(point.offset, True)


def test_reading_a_block_of_registers(bridge, server):
    bridge.runtime.set_path("rHmiFreshWaterL", 1234.0)
    bridge.runtime.set_path("rHmiEnergyKWh", 56.25)
    bridge.refresh()
    water = bridge.io_map.find("rHmiFreshWaterL")
    energy = bridge.io_map.find("rHmiEnergyKWh")
    count = energy.offset + 2 - water.offset
    with ModbusClient(port=server.port) as client:
        words = client.read_holding_registers(water.offset, count)
    assert words_to_float(words[0], words[1]) == pytest.approx(1234.0)
    tail = energy.offset - water.offset
    assert words_to_float(words[tail], words[tail + 1]) == pytest.approx(56.25)


def test_bad_requests_get_modbus_exceptions(bridge):
    # Function code 8 is not implemented.
    assert build_response(bridge, 1, bytes([8, 0, 0, 0, 0])) == bytes([0x88, 0x01])
    # Zero-length read.
    assert build_response(bridge, 1, bytes([3, 0, 0, 0, 0])) == bytes([0x83, 0x03])
    # A truncated PDU must produce an exception rather than a traceback.
    assert build_response(bridge, 1, bytes([3, 0]))[0] & 0x80


def test_float_words_are_high_word_first():
    high, low = float_to_words(1.0)
    assert (high, low) == (0x3F80, 0x0000)


@pytest.mark.slow
def test_a_client_can_start_a_cycle_and_watch_it_run():
    """End to end: the only route into the controller is a coil write."""
    cell = CipCell(
        fast_recipe(), SCENARIOS["light"], external_commands=True, sample_every_s=1e9
    )
    bridge = ModbusBridge(cell.runtime, cell.io_map)
    server = ModbusServer(bridge, "127.0.0.1", 0)
    server.serve_in_background()
    try:
        start = bridge.io_map.find("xCmdStart")
        state = bridge.io_map.find("iHmiUnitState")
        phase = bridge.io_map.find("iHmiPhase")

        for _ in range(20):
            bridge.apply_writes()
            cell.step()
        bridge.refresh()

        with ModbusClient(port=server.port) as client:
            assert client.read_holding_registers(state.offset, 1) == [0]  # IDLE
            client.write_coil(start.offset, True)
            bridge.apply_writes()
            for _ in range(50):
                cell.step()
            bridge.refresh()
            assert client.read_holding_registers(state.offset, 1) == [2]  # RUNNING
            assert client.read_holding_registers(phase.offset, 1) == [1]  # PRE_RINSE

        seen = set()
        for _ in range(int(30 * 60 / cell.scan_s)):
            cell.step()
            seen.add(int(cell.runtime.get_path("gPhase")))
            if int(cell.runtime.get_path("gUnitState")) in (7, 9):
                break
        bridge.refresh()

        with ModbusClient(port=server.port) as client:
            assert client.read_holding_registers(state.offset, 1) == [7]  # COMPLETE
            assert {1, 2, 3, 4, 5} <= seen, "every phase of the procedure ran"
            clean = bridge.io_map.find("xHmiVerifiedClean")
            assert client.read_coils(clean.offset, 1) == [True]
    finally:
        server.shutdown()
        server.server_close()
