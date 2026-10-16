"""The ST as compiled by RuSTy, loaded and driven through the same harness.

These need the RuSTy build from scripts/rusty/compile_plc.sh and so only run
where it exists (the `rusty` CI job, or a Linux container). Everything else in
the suite can be pointed at the compiled program with CIP_BACKEND=rusty; these
are the checks that only make sense for it.
"""

from __future__ import annotations

import pytest
from conftest import fast_recipe

from src.harness import compiled
from src.harness.cell import PLC_DIR, PROGRAM_ORDER, CipCell
from src.iec61131.runtime import load
from src.plant.params import SCENARIOS

pytestmark = [
    pytest.mark.compiled,
    pytest.mark.skipif(not compiled.available(), reason="no RuSTy build (Linux only)"),
]


@pytest.fixture
def runtime():
    rt = compiled.CompiledRuntime(PLC_DIR, PROGRAM_ORDER)
    yield rt
    rt.close()


def test_every_global_is_where_the_declarations_say(runtime):
    interpreted = load(PLC_DIR, PROGRAM_ORDER)
    assert sorted(runtime.globals) == sorted(interpreted.globals)
    assert {k: str(v.address) for k, v in runtime.located.items()} == {
        k: str(v.address) for k, v in interpreted.located.items()
    }


def test_initial_values_come_from_the_compiled_image(runtime):
    # Set by the compiler's generated constructors, not by Python.
    assert runtime.get_path("gRecipe.sName") == "default"
    assert runtime.get_path("gRecipe.tCausticHold") == pytest.approx(600.0)
    assert runtime.get_path("gResults[3].rMinTempC") == pytest.approx(999.0)
    assert runtime.get_path("C_ALARM_COUNT") == 13
    assert runtime.get_path("C_CYCLE_S") == pytest.approx(0.2, abs=1e-7)


def test_a_real_is_single_precision(runtime):
    runtime.set_path("rLevelBalanceTankPct", 0.1)
    assert runtime.get_path("rLevelBalanceTankPct") != 0.1
    assert runtime.get_path("rLevelBalanceTankPct") == pytest.approx(0.1, rel=1e-7)


def test_timers_run_on_the_scan_clock_not_the_wall_clock(runtime):
    # The dry-run detector in PRG_Safety is a 20 s TON: a running supply pump
    # with no flow. Healthy otherwise, so the trip is that timer and nothing else.
    runtime.set_path("xSafetyOk", True)
    runtime.set_path("xCircuitAvailable", True)
    runtime.set_path("rLevelBalanceTankPct", 50.0)
    runtime.set_path("xSupplyPumpRunning", True)
    runtime.set_path("rFlowSupplyM3h", 0.0)
    scans = 0
    while not runtime.get_path("gxSafetyTrip"):
        runtime.scan(0.2)
        scans += 1
        assert scans < 200
    # 20 s of scans, give or take the scan on which the timer starts; done in
    # far less than 20 s of wall clock.
    assert scans * 0.2 == pytest.approx(20.0, abs=0.41)


def test_each_runtime_is_a_separate_controller():
    first = compiled.CompiledRuntime(PLC_DIR, PROGRAM_ORDER)
    second = compiled.CompiledRuntime(PLC_DIR, PROGRAM_ORDER)
    try:
        first.set_path("giBatchId", 41)
        assert second.get_path("giBatchId") == 0
    finally:
        first.close()
        second.close()


@pytest.mark.slow
def test_a_cut_down_cycle_agrees_with_the_interpreter():
    results = {}
    for backend in ("interpreter", "rusty"):
        cell = CipCell(fast_recipe(), SCENARIOS["medium"], sample_every_s=1e9, backend=backend)
        results[backend] = cell.run_cycle(max_minutes=90)
    ref, dut = results["interpreter"], results["rusty"]
    assert dut.completed and dut.verified_clean
    assert [(p["phase"], p["pass"]) for p in dut.phases] == [
        (p["phase"], p["pass"]) for p in ref.phases
    ]
    assert [a["tag"] for a in dut.alarms] == [a["tag"] for a in ref.alarms]
    assert dut.fresh_water_l == pytest.approx(ref.fresh_water_l, rel=0.01)
