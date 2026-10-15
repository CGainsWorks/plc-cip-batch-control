"""The unit procedure, run end to end against the plant.

These use a cut-down recipe (see conftest.fast_recipe) so the suite stays in
seconds. The numbers quoted in the README come from the full recipes in
config/recipes.json, exercised by scripts/run_experiments.py and re-checked by
scripts/verify_results.py.
"""

from __future__ import annotations

import pytest
from conftest import fast_recipe

from src.harness.cell import PHASE_NAMES, CipCell, load_recipes
from src.plant.params import RESIDUAL_CHEMICAL_LIMIT_PPM, RESIDUAL_LIMIT_G_M2, SCENARIOS

pytestmark = pytest.mark.slow

IDLE, PRE_RINSE, CAUSTIC, INT_RINSE, ACID, FINAL_RINSE, SANITISE, COMPLETE = range(8)


def run(recipe=None, soil="medium", **kwargs):
    cell = CipCell(recipe or fast_recipe(), SCENARIOS[soil], sample_every_s=1e9, **kwargs)
    return cell, cell.run_cycle(max_minutes=90)


def test_a_cycle_completes_and_is_released():
    cell, result = run()
    assert result.completed is True
    assert result.aborted is False
    assert result.fail_code == "NONE"
    assert result.verified_clean is True
    assert [p["phase"] for p in result.phases] == [
        "PRE_RINSE",
        "CAUSTIC_WASH",
        "INTERMEDIATE_RINSE",
        "ACID_WASH",
        "FINAL_RINSE",
    ]
    assert all(p["pass"] for p in result.phases)


def test_the_circuit_actually_ends_up_clean():
    """The release decision is only worth anything if it agrees with the two
    quantities no instrument on the skid can see.

    This one uses the real recipe rather than the cut-down one: a wash trimmed
    to sixty seconds at 45 degC is fine for testing phase order and no use at
    all for removing protein and asserting otherwise would only prove the
    assertion wrong.
    """
    cell = CipCell(load_recipes()["adaptive"], SCENARIOS["heavy"], sample_every_s=1e9)
    result = cell.run_cycle(max_minutes=90)
    assert result.completed is True
    assert result.verified_clean is True
    assert result.residual_g_m2 < RESIDUAL_LIMIT_G_M2
    assert result.residual_chemical_ppm < RESIDUAL_CHEMICAL_LIMIT_PPM


def test_the_recipe_chooses_which_phases_run():
    _cell, result = run(fast_recipe(use_acid=False))
    assert [p["phase"] for p in result.phases] == [
        "PRE_RINSE",
        "CAUSTIC_WASH",
        "INTERMEDIATE_RINSE",
        "FINAL_RINSE",
    ]

    _cell, result = run(fast_recipe(use_sanitise=True))
    assert result.phases[-1]["phase"] == "SANITISE"
    assert result.phases[-1]["pass"] is True


def test_the_hold_only_counts_time_that_met_the_specification():
    _cell, result = run(soil="medium")
    caustic = next(p for p in result.phases if p["phase"] == "CAUSTIC_WASH")
    assert caustic["hold_achieved_s"] >= caustic["hold_required_s"]
    # It took longer than the hold, because heating and charging do not count.
    assert caustic["duration_s"] > caustic["hold_achieved_s"] + 60.0
    assert caustic["min_temp_c"] is not None


def test_the_wash_meets_every_tact_criterion():
    _cell, result = run()
    caustic = next(p for p in result.phases if p["phase"] == "CAUSTIC_WASH")
    assert caustic["mean_conc_pct"] == pytest.approx(2.0, abs=0.25)
    assert caustic["min_flow_m3h"] >= 14.0
    assert caustic["max_temp_c"] >= 40.0


def test_the_steam_valve_never_opens_without_flow():
    """The interlock that stops the plate pack baking soil onto itself."""
    cell = CipCell(fast_recipe(), SCENARIOS["medium"], sample_every_s=1e9)
    rt = cell.runtime
    violations = 0
    cell.press("start")
    for _ in range(int(60 * 60 / cell.scan_s)):
        cell.step()
        if bool(rt.get_path("xSteamIsolation")) and float(rt.get_path("rFlowSupplyM3h")) < 5.0:
            violations += 1
        if int(rt.get_path("gUnitState")) in (7, 9):
            break
    assert violations == 0


def test_only_one_return_path_is_ever_open():
    cell = CipCell(fast_recipe(), SCENARIOS["medium"], sample_every_s=1e9)
    rt = cell.runtime
    returns = (
        "xV06ReturnToDrain",
        "xV07ReturnToBalance",
        "xV08ReturnToCaustic",
        "xV09ReturnToRecovery",
        "xV10ReturnToAcid",
    )
    sources = ("xV01FreshWater", "xV02RecoveredWater", "xV11CausticSupply", "xV12AcidSupply")
    cell.press("start")
    for _ in range(int(60 * 60 / cell.scan_s)):
        cell.step()
        assert sum(bool(rt.get_path(tag)) for tag in returns) <= 1
        assert sum(bool(rt.get_path(tag)) for tag in sources) <= 1
        if int(rt.get_path("gUnitState")) in (7, 9):
            break


def test_a_second_batch_starts_from_a_clean_slate():
    cell = CipCell(fast_recipe(), SCENARIOS["medium"], sample_every_s=1e9)
    first = cell.run_cycle(max_minutes=90)
    assert first.completed
    cell.press("reset")
    for _ in range(20):
        cell.step()
    assert int(cell.runtime.get_path("gUnitState")) == 0
    cell.press("start")
    for _ in range(int(60 * 60 / cell.scan_s)):
        cell.step()
        if int(cell.runtime.get_path("gUnitState")) in (7, 9):
            break
    second = cell.result()
    assert second.batch_id == first.batch_id + 1
    assert second.completed is True
    # The totals were zeroed at the start of the batch, not carried forward.
    assert second.fresh_water_l < first.fresh_water_l * 1.5


def test_hold_freezes_the_phase_and_restart_resumes_it():
    cell = CipCell(fast_recipe(), SCENARIOS["medium"], sample_every_s=1e9)
    rt = cell.runtime
    cell.press("start")
    # Run into the caustic wash, then hold.
    for _ in range(int(60 * 60 / cell.scan_s)):
        cell.step()
        if int(rt.get_path("gPhase")) == CAUSTIC and int(rt.get_path("gPhaseStep")) == 3:
            break
    cell.press("hold")
    for _ in range(30):
        cell.step()
    assert int(rt.get_path("gUnitState")) in (3, 4)
    held_elapsed = float(rt.get_path("grPhaseElapsedS"))
    held_hold = float(rt.get_path("grHoldAchievedS"))
    for _ in range(300):
        cell.step()
    assert float(rt.get_path("grPhaseElapsedS")) == pytest.approx(held_elapsed)
    assert float(rt.get_path("grHoldAchievedS")) == pytest.approx(held_hold)
    assert bool(rt.get_path("xSupplyPumpRun")) is False
    assert bool(rt.get_path("xSteamIsolation")) is False

    cell.press("restart")
    for _ in range(50):
        cell.step()
    assert int(rt.get_path("gUnitState")) == 2
    assert float(rt.get_path("grPhaseElapsedS")) > held_elapsed


def test_an_operator_abort_stops_the_skid_and_records_why():
    cell = CipCell(fast_recipe(), SCENARIOS["medium"], sample_every_s=1e9)
    rt = cell.runtime
    cell.press("start")
    for _ in range(int(10 * 60 / cell.scan_s)):
        cell.step()
        if int(rt.get_path("gPhase")) == CAUSTIC:
            break
    cell.press("abort")
    for _ in range(int(30 / cell.scan_s)):
        cell.step()
    result = cell.result()
    assert result.aborted is True
    assert result.verified_clean is False
    assert result.fail_code == "OPERATOR_ABORT"
    assert bool(rt.get_path("xSupplyPumpRun")) is False
    assert bool(rt.get_path("xSteamIsolation")) is False


def test_the_unit_will_not_start_while_the_circuit_is_in_use():
    from src.plant.model import Faults

    cell = CipCell(
        fast_recipe(),
        SCENARIOS["medium"],
        faults=Faults(circuit_lost_at_s=0.0, circuit_restored_at_s=120.0),
        sample_every_s=1e9,
    )
    rt = cell.runtime
    for _ in range(50):
        cell.step()
    cell.press("start")
    for _ in range(50):
        cell.step()
    assert int(rt.get_path("gUnitState")) == 0, "not released, so it must not start"
    for _ in range(int(150 / cell.scan_s)):
        cell.step()
    cell.press("start")
    for _ in range(20):
        cell.step()
    assert int(rt.get_path("gUnitState")) == 2


def test_phase_names_cover_the_enumeration():
    assert PHASE_NAMES[PRE_RINSE] == "PRE_RINSE"
    assert PHASE_NAMES[COMPLETE] == "COMPLETE"
