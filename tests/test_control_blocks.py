"""The reusable function blocks, driven as the real ST source.

Each test loads the actual .st file out of plc/fb rather than a copy, so a
change to the block that breaks its contract fails here.
"""

from __future__ import annotations

import pytest
from conftest import build_harness, fb_path

PID_PROGRAM = """
VAR_GLOBAL
    gPv : REAL;
    gSp : REAL;
    gEnable : BOOL;
    gOut : REAL;
    gSat : BOOL;
    gKp : REAL := 2.0;
    gTi : REAL := 10.0;
END_VAR
PROGRAM PRG_Test
VAR
    pid : FB_Pid;
END_VAR
pid(xEnable := gEnable, rPv := gPv, rSp := gSp, rKp := gKp, rTi := gTi,
    rOutMin := 0.0, rOutMax := 100.0, rCycleS := 0.1);
gOut := pid.rOut;
gSat := pid.xSaturated;
END_PROGRAM
"""


def pid_harness():
    return build_harness(PID_PROGRAM, "PRG_Test", [fb_path("FB_Pid.st")])


def test_pid_is_proportional_when_the_integral_is_off():
    h = pid_harness()
    h.set("gTi", 0.0)
    h.set("gEnable", True)
    h.set("gSp", 50.0)
    h.set("gPv", 45.0)
    h.scan()
    assert h.get("gOut") == pytest.approx(10.0)  # Kp 2.0 times an error of 5


def test_pid_integral_closes_the_offset():
    h = pid_harness()
    h.set("gEnable", True)
    h.set("gSp", 50.0)
    h.set("gPv", 49.0)
    first = None
    for _ in range(50):
        h.scan()
        if first is None:
            first = h.get("gOut")
    assert h.get("gOut") > first


def test_pid_does_not_wind_up_against_its_limit():
    """The whole point of the anti-windup: a long saturated period must not
    leave the output stuck high once the process finally responds."""
    h = pid_harness()
    h.set("gEnable", True)
    h.set("gSp", 90.0)
    h.set("gPv", 0.0)
    h.run_for(600.0)
    assert h.get("gOut") == 100.0
    assert h.get("gSat") is True
    # Process arrives at setpoint: the output must come off the limit promptly.
    h.set("gPv", 90.0)
    h.run_for(5.0)
    assert h.get("gOut") < 100.0
    h.run_for(30.0)
    assert h.get("gOut") < 50.0


def test_pid_parks_when_disabled():
    h = pid_harness()
    h.set("gEnable", True)
    h.set("gSp", 90.0)
    h.set("gPv", 0.0)
    h.run_for(60.0)
    h.set("gEnable", False)
    h.scan()
    assert h.get("gOut") == 0.0
    # Re-enabling must not resume from a wound-up integrator.
    h.set("gEnable", True)
    h.set("gPv", 90.0)
    h.scan()
    assert h.get("gOut") == pytest.approx(0.0, abs=1e-6)


PUMP_PROGRAM = """
VAR_GLOBAL
    gStart : BOOL;
    gInterlock : BOOL := TRUE;
    gFeedback : BOOL;
    gFault : BOOL;
    gReset : BOOL;
    gRun : BOOL;
    gTripped : BOOL;
    gFbFault : BOOL;
    gHealthy : BOOL;
END_VAR
PROGRAM PRG_Test
VAR
    pump : FB_Pump;
END_VAR
pump(xStart := gStart, xInterlockOk := gInterlock, xRunFeedback := gFeedback,
     xFault := gFault, xReset := gReset);
gRun := pump.xRun;
gTripped := pump.xTripped;
gFbFault := pump.xFeedbackFault;
gHealthy := pump.xHealthy;
END_PROGRAM
"""


def pump_harness():
    return build_harness(PUMP_PROGRAM, "PRG_Test", [fb_path("FB_Pump.st")])


def test_pump_runs_when_started_and_interlocked():
    h = pump_harness()
    h.set("gStart", True)
    h.scan()
    h.set("gFeedback", True)
    h.run_for(2.0)
    assert h.get("gRun") is True
    assert h.get("gHealthy") is True


def test_pump_will_not_run_without_the_interlock():
    h = pump_harness()
    h.set("gStart", True)
    h.set("gInterlock", False)
    h.scan()
    assert h.get("gRun") is False


def test_missing_feedback_latches_a_fault_and_stops_the_pump():
    h = pump_harness()
    h.set("gStart", True)
    h.run_for(6.0)  # feedback never appears
    assert h.get("gFbFault") is True
    assert h.get("gRun") is False
    # The fault has to be reset explicitly; the pump must not restart on its own.
    h.set("gFeedback", True)
    h.run_for(2.0)
    assert h.get("gRun") is False
    h.set("gReset", True)
    h.scan()
    h.set("gReset", False)
    h.run_for(1.0)
    assert h.get("gRun") is True


def test_motor_protection_trip_latches():
    h = pump_harness()
    h.set("gStart", True)
    h.set("gFeedback", True)
    h.run_for(1.0)
    h.set("gFault", True)
    h.scan()
    assert h.get("gTripped") is True
    assert h.get("gRun") is False
    h.set("gFault", False)
    h.run_for(2.0)
    assert h.get("gRun") is False, "an intermittent trip must not restart the pump"
    h.set("gReset", True)
    h.scan()
    assert h.get("gTripped") is False


ALARM_PROGRAM = """
VAR_GLOBAL
    gCondition : BOOL;
    gAck : BOOL;
    gNow : REAL;
    gNew : BOOL;
    gAlarm : ST_Alarm;
END_VAR
PROGRAM PRG_Test
VAR
    alm : FB_Alarm;
END_VAR
gNow := gNow + 0.1;
alm(xCondition := gCondition, xAck := gAck, iPriority := 2, tOnDelay := T#1s,
    rNowS := gNow, stAlarm := gAlarm);
gNew := alm.xNewAlarm;
END_PROGRAM
"""


def alarm_harness():
    return build_harness(ALARM_PROGRAM, "PRG_Test", [fb_path("FB_Alarm.st")])


def test_alarm_on_delay_suppresses_a_fleeting_condition():
    h = alarm_harness()
    h.set("gCondition", True)
    h.run_for(0.5)
    h.set("gCondition", False)
    h.run_for(2.0)
    assert h.get("gAlarm.xLatched") is False
    assert h.get("gAlarm.iCount") == 0


def test_alarm_latches_and_needs_both_clearing_and_acknowledging():
    h = alarm_harness()
    h.set("gCondition", True)
    h.run_for(1.5)
    assert h.get("gAlarm.xActive") is True
    assert h.get("gAlarm.xLatched") is True
    assert h.get("gAlarm.xAcked") is False
    assert h.get("gAlarm.iCount") == 1
    # Condition clears but nobody has looked at it: it stays on the list.
    h.set("gCondition", False)
    h.run_for(2.0)
    assert h.get("gAlarm.xActive") is False
    assert h.get("gAlarm.xLatched") is True
    h.set("gAck", True)
    h.scan()
    assert h.get("gAlarm.xLatched") is False


def test_acknowledging_an_active_alarm_does_not_clear_it():
    h = alarm_harness()
    h.set("gCondition", True)
    h.run_for(1.5)
    h.set("gAck", True)
    h.run_for(1.0)
    assert h.get("gAlarm.xAcked") is True
    assert h.get("gAlarm.xLatched") is True, "still active, so still on the list"


def test_a_second_occurrence_re_arms_the_acknowledgement():
    h = alarm_harness()
    h.set("gCondition", True)
    h.run_for(1.5)
    h.set("gAck", True)
    h.run_for(0.2)
    h.set("gAck", False)
    h.set("gCondition", False)
    h.run_for(1.0)
    h.set("gCondition", True)
    h.run_for(1.5)
    assert h.get("gAlarm.iCount") == 2
    assert h.get("gAlarm.xAcked") is False


TOTALISER_PROGRAM = """
VAR_GLOBAL
    gRate : REAL;
    gEnable : BOOL;
    gReset : BOOL;
    gTotal : REAL;
END_VAR
PROGRAM PRG_Test
VAR
    tot : FB_Totaliser;
END_VAR
tot(xEnable := gEnable, rRatePerHour := gRate, rCutOff := 50.0, rCycleS := 0.1,
    xReset := gReset);
gTotal := tot.rTotal;
END_PROGRAM
"""


def test_totaliser_integrates_and_respects_its_cut_off():
    h = build_harness(TOTALISER_PROGRAM, "PRG_Test", [fb_path("FB_Totaliser.st")])
    h.set("gEnable", True)
    h.set("gRate", 3600.0)  # units per hour, so one unit per second
    h.run_for(10.0)
    assert h.get("gTotal") == pytest.approx(10.0, rel=1e-6)
    # Below the cut-off, a noisy transmitter must not book anything.
    h.set("gRate", 20.0)
    h.run_for(100.0)
    assert h.get("gTotal") == pytest.approx(10.0, rel=1e-6)
    h.set("gReset", True)
    h.scan()
    assert h.get("gTotal") == 0.0


UNIT_PROGRAM = """
VAR_GLOBAL
    gStart : BOOL;
    gHold : BOOL;
    gRestart : BOOL;
    gAbort : BOOL;
    gReset : BOOL;
    gReady : BOOL := TRUE;
    gComplete : BOOL;
    gFailed : BOOL;
    gTrip : BOOL;
    gState : E_UnitState;
    gExecute : BOOL;
    gHolding : BOOL;
    gStartPulse : BOOL;
END_VAR
PROGRAM PRG_Test
VAR
    unit : FB_UnitStateMachine;
END_VAR
unit(xStart := gStart, xHold := gHold, xRestart := gRestart, xAbort := gAbort,
     xReset := gReset, xReadyToStart := gReady, xProcedureComplete := gComplete,
     xProcedureFailed := gFailed, xSafetyTrip := gTrip);
gState := unit.eState;
gExecute := unit.xExecute;
gHolding := unit.xHolding;
gStartPulse := unit.xStartPulse;
END_PROGRAM
"""

IDLE, STARTING, RUNNING, HOLDING, HELD, RESTARTING, COMPLETING, COMPLETE, ABORTING, ABORTED = range(10)


def unit_harness():
    return build_harness(UNIT_PROGRAM, "PRG_Test", [fb_path("FB_UnitStateMachine.st")])


def pulse(h, name: str) -> None:
    h.set(name, True)
    h.scan()
    h.set(name, False)
    h.scan()


def test_unit_runs_the_isa88_happy_path():
    h = unit_harness()
    assert h.get("gState") == IDLE
    pulse(h, "gStart")
    assert h.get("gState") == RUNNING
    assert h.get("gExecute") is True
    h.set("gComplete", True)
    h.scan()
    h.scan()
    assert h.get("gState") == COMPLETE
    assert h.get("gExecute") is False
    h.set("gComplete", False)
    pulse(h, "gReset")
    assert h.get("gState") == IDLE


def test_unit_will_not_start_when_it_is_not_ready():
    h = unit_harness()
    h.set("gReady", False)
    pulse(h, "gStart")
    assert h.get("gState") == IDLE


def test_hold_and_restart():
    h = unit_harness()
    pulse(h, "gStart")
    pulse(h, "gHold")
    assert h.get("gState") == HOLDING
    assert h.get("gHolding") is True
    h.run_for(3.0)
    assert h.get("gState") == HELD
    pulse(h, "gRestart")
    assert h.get("gState") == RUNNING


def test_safety_trip_pre_empts_everything_and_settles_before_aborted():
    h = unit_harness()
    pulse(h, "gStart")
    h.set("gTrip", True)
    h.scan()
    assert h.get("gState") == ABORTING
    assert h.get("gExecute") is False
    h.run_for(1.0)
    assert h.get("gState") == ABORTING, "aborting runs for the settle time"
    h.run_for(6.0)
    assert h.get("gState") == ABORTED
    # A reset while the trip is still present must not clear the unit.
    pulse(h, "gReset")
    assert h.get("gState") == ABORTED
    h.set("gTrip", False)
    pulse(h, "gReset")
    assert h.get("gState") == IDLE


def test_a_failed_procedure_aborts_the_unit():
    h = unit_harness()
    pulse(h, "gStart")
    h.set("gFailed", True)
    h.scan()
    assert h.get("gState") == ABORTING


def test_start_pulse_is_exactly_one_scan():
    """IDLE takes the edge, STARTING raises the pulse, RUNNING follows."""
    h = unit_harness()
    h.set("gStart", True)
    h.scan()
    assert h.get("gState") == STARTING
    assert h.get("gStartPulse") is False
    h.scan()
    assert h.get("gState") == RUNNING
    assert h.get("gStartPulse") is True
    h.scan()
    assert h.get("gStartPulse") is False
