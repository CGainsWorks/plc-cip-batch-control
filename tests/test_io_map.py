"""The address map and the places the controller and the plant have to agree."""

from __future__ import annotations

import os

import pytest
from conftest import PLC_DIR, REPO_ROOT

from src.harness.cell import OUTPUT_BINDING, PROGRAM_ORDER
from src.harness.io_map import build_io_map, float_to_words, words_to_float
from src.iec61131.runtime import load
from src.plant.model import CipPlant, Outputs
from src.plant.params import DEFAULT_PARAMS


@pytest.fixture(scope="module")
def runtime():
    return load(PLC_DIR, PROGRAM_ORDER)


@pytest.fixture(scope="module")
def io_map(runtime):
    return build_io_map(runtime)


def test_every_located_variable_reaches_the_map(runtime, io_map):
    assert len(io_map.points) == len(runtime.located)
    assert {p.name for p in io_map.points} == set(runtime.located)


def test_no_two_tags_share_a_modbus_register(io_map):
    occupied = set()
    for point in io_map.points:
        for word in range(point.words):
            key = (point.table, point.offset + word)
            assert key not in occupied, "{0} collides at {1}".format(point.name, key)
            occupied.add(key)


def test_inputs_outputs_and_memory_land_in_the_right_tables(io_map):
    for point in io_map.points:
        if point.address.prefix == "I":
            assert point.table in ("discrete_input", "input_register")
        elif point.address.prefix == "Q":
            assert point.table in ("coil", "holding_register")
        else:
            assert point.table in ("coil", "holding_register")
            assert point.offset >= 1000, "the memory area sits above the process image"


def test_reals_occupy_two_registers_and_round_trip(io_map):
    reals = [p for p in io_map.points if p.type_name in ("REAL", "LREAL")]
    assert reals, "the skid has analogue points"
    assert all(p.words == 2 for p in reals)
    for value in (0.0, 1.0, -273.15, 18.35, 1e6):
        high, low = float_to_words(value)
        assert words_to_float(high, low) == pytest.approx(value, rel=1e-6)


def test_operator_commands_are_writable_by_a_client(io_map):
    for tag in ("xCmdStart", "xCmdHold", "xCmdAbort", "xCmdReset", "xCmdAckAlarms"):
        point = io_map.find(tag)
        assert point.address.prefix == "M", "{0} must be writable over the network".format(tag)


def test_every_plant_output_binding_names_a_real_output(io_map):
    outputs = {p.name for p in io_map.points if p.address.prefix == "Q"}
    assert set(OUTPUT_BINDING) <= outputs
    fields = set(Outputs().__dict__)
    assert set(OUTPUT_BINDING.values()) <= fields


def test_every_plant_sensor_has_somewhere_to_go(runtime):
    plant = CipPlant()
    plant.step(0.1, Outputs())
    for tag in plant.sensors():
        runtime.get_path(tag)  # raises if the ST does not declare it


def test_controller_and_plant_agree_on_the_hardware(runtime):
    """A controller that thinks the tank is a different size from the tank is
    a real class of commissioning bug; this is the cheap guard against it."""
    assert float(runtime.get_path("C_BALANCE_TANK_L")) == DEFAULT_PARAMS.balance_tank_l
    assert float(runtime.get_path("C_CAUSTIC_DOSE_LPM")) == DEFAULT_PARAMS.caustic_dose_lpm
    assert float(runtime.get_path("C_ACID_DOSE_LPM")) == DEFAULT_PARAMS.acid_dose_lpm
    # The pump must be able to reach the flow the recipes ask for.
    assert DEFAULT_PARAMS.supply_pump_max_m3h > 18.0


def test_the_documented_map_is_up_to_date():
    from scripts.render_io_map import TARGET, render

    if not os.path.exists(TARGET):
        pytest.fail("docs/io_map.md is missing; run scripts/render_io_map.py")
    with open(TARGET, "r", encoding="utf-8") as handle:
        assert handle.read() == render(), "run scripts/render_io_map.py"


def test_repo_root_is_where_we_think_it_is():
    assert os.path.isdir(os.path.join(REPO_ROOT, "plc"))
