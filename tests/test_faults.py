"""Fault injection: what the controller does when the plant misbehaves.

Every case here is one a commissioning engineer would try on site with the
skid in front of them. Without hardware they are the closest thing available
to a factory acceptance test and they are the tests that would be re-run
against the real machine.
"""

from __future__ import annotations

import pytest
from conftest import fast_recipe

from src.harness.cell import ALARM_TAGS, CipCell
from src.plant.model import Faults
from src.plant.params import SCENARIOS

pytestmark = pytest.mark.slow

CAUSTIC = 2


def run_with(faults: Faults, recipe=None, soil="medium", minutes: float = 90.0):
    cell = CipCell(
        recipe or fast_recipe(), SCENARIOS[soil], faults=faults, sample_every_s=1e9
    )
    return cell, cell.run_cycle(max_minutes=minutes)


def alarm_tags(result) -> set:
    return {alarm["tag"] for alarm in result.alarms}


def test_a_pump_trip_mid_wash_aborts_the_batch():
    cell, result = run_with(Faults(supply_pump_trip_at_s=400.0))
    assert result.completed is False
    assert result.aborted is True
    assert result.fail_code == "PUMP_FAULT"
    assert result.verified_clean is False
    assert "SUPPLY_PUMP" in alarm_tags(result)
    assert bool(cell.runtime.get_path("xSupplyPumpRun")) is False
    assert bool(cell.runtime.get_path("xSteamIsolation")) is False
    failed = [p for p in result.phases if not p["pass"]]
    assert failed, "the batch record has to say which phase failed"
    assert failed[0]["fail_code"] == "PUMP_FAULT"


def test_an_emergency_stop_trips_the_unit_and_first_out_says_so():
    cell, result = run_with(Faults(estop_at_s=400.0))
    assert result.aborted is True
    assert result.fail_code == "SAFETY_TRIP"
    assert "SAFETY_TRIP" in alarm_tags(result)
    assert int(cell.runtime.get_path("giFirstOut")) != 0
    assert ALARM_TAGS[int(cell.runtime.get_path("giFirstOut"))] in alarm_tags(result)


def test_losing_the_circuit_release_mid_cycle_aborts():
    cell, result = run_with(Faults(circuit_lost_at_s=400.0))
    assert result.aborted is True
    assert result.fail_code == "SAFETY_TRIP"
    assert "CIRCUIT_UNAVAILABLE" in alarm_tags(result)


def test_a_failed_steam_supply_fails_the_wash_on_temperature():
    """No steam means the hold timer never advances, so the phase runs out of
    time rather than quietly completing a wash that never got hot."""
    recipe = fast_recipe(caustic_temp_sp_c=75.0, caustic_temp_min_c=70.0, phase_max_s=900)
    cell, result = run_with(Faults(steam_lost_at_s=1.0), recipe=recipe)
    assert result.completed is False
    assert result.fail_code in ("TEMP_TIMEOUT", "PHASE_TIMEOUT")
    assert "STEAM_UNAVAILABLE" in alarm_tags(result)
    caustic = next((p for p in result.phases if p["phase"] == "CAUSTIC_WASH"), None)
    assert caustic is not None
    assert caustic["pass"] is False
    assert caustic["hold_achieved_s"] < caustic["hold_required_s"]


def test_a_derated_heater_takes_longer_but_still_earns_its_hold():
    """A partly blocked plate pack should slow the wash down, not fail it."""
    recipe = fast_recipe(caustic_hold_s=120, phase_max_s=2400)
    _cell, healthy = run_with(Faults(), recipe=recipe)
    _cell, derated = run_with(
        Faults(heater_derate_from_s=1.0, heater_derate_factor=0.4), recipe=recipe
    )
    assert healthy.completed and derated.completed
    healthy_caustic = next(p for p in healthy.phases if p["phase"] == "CAUSTIC_WASH")
    derated_caustic = next(p for p in derated.phases if p["phase"] == "CAUSTIC_WASH")
    assert derated_caustic["duration_s"] > healthy_caustic["duration_s"]
    assert derated_caustic["hold_achieved_s"] >= derated_caustic["hold_required_s"]


def test_a_transmitter_stuck_clean_is_caught_before_any_rinse_trusts_it():
    """The failure mode that matters most for an adaptive rinse.

    A return element frozen at a clean reading would satisfy every rinse
    endpoint the instant the blanking time expired and release a dirty
    circuit. The wash plausibility check catches it first: circulating 2 %
    caustic, the return line cannot read like water.
    """
    cell, result = run_with(Faults(conductivity_frozen_at_s=1.0))
    assert result.verified_clean is False
    assert result.completed is False
    assert result.fail_code == "SENSOR_FAULT"
    assert "CONDUCTIVITY_SENSOR" in alarm_tags(result)
    assert bool(cell.runtime.get_path("gxCondSensorFault")) is True


def test_a_transmitter_stuck_dirty_makes_the_rinse_time_out():
    """The other half: frozen above the endpoint, an adaptive rinse has to run
    to its maximum and fail rather than stopping early."""
    recipe = fast_recipe(rinse_max_s=300, use_acid=False)
    cell = CipCell(recipe, SCENARIOS["medium"], sample_every_s=1e9)
    cell.press("start")
    # Let the caustic wash prove the element first, then freeze it while it is
    # reading the wash; from there it sits well above any rinse endpoint.
    for _ in range(int(60 * 60 / cell.scan_s)):
        cell.step()
        if int(cell.runtime.get_path("gPhase")) == 3:  # INTERMEDIATE_RINSE
            break
    cell.plant.faults.conductivity_frozen_at_s = cell.plant.time_s
    for _ in range(int(60 * 60 / cell.scan_s)):
        cell.step()
        if int(cell.runtime.get_path("gUnitState")) in (7, 9):
            break
    result = cell.result()
    assert result.verified_clean is False
    assert result.fail_code == "RINSE_NOT_CLEAN"
    assert "RINSE_NOT_CLEAN" in alarm_tags(result)


def test_a_return_pump_trip_is_alarmed():
    _cell, result = run_with(Faults(return_pump_trip_at_s=400.0))
    assert "RETURN_PUMP" in alarm_tags(result)


def test_an_aborted_batch_can_be_reset_and_run_again():
    cell, result = run_with(Faults(supply_pump_trip_at_s=400.0))
    assert result.aborted is True
    # The fault is still latched, so a reset alone must not clear the pump trip.
    cell.press("reset")
    for _ in range(20):
        cell.step()
    assert int(cell.runtime.get_path("gUnitState")) == 0
    cell.plant.faults.supply_pump_trip_at_s = None
    cell.press("reset")
    for _ in range(20):
        cell.step()
    cell.press("start")
    for _ in range(int(30 / cell.scan_s)):
        cell.step()
    assert int(cell.runtime.get_path("gUnitState")) == 2


def test_alarms_stay_on_the_list_until_acknowledged():
    cell, result = run_with(Faults(supply_pump_trip_at_s=400.0))
    assert int(cell.runtime.get_path("giActiveAlarms")) > 0
    assert bool(cell.runtime.get_path("xHmiAlarmHorn")) is True
    cell.plant.faults.supply_pump_trip_at_s = None
    for _ in range(50):
        cell.step()
    assert int(cell.runtime.get_path("giActiveAlarms")) > 0, "cleared but not acknowledged"
    cell.press("ack")
    for _ in range(10):
        cell.step()
    assert bool(cell.runtime.get_path("xHmiAlarmHorn")) is False
