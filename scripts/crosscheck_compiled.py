"""Run the same scenarios on the interpreter and on the RuSTy-compiled program.

Both executions read the same plant model through the same I/O map, take the
same operator commands at the same simulated times and are judged on the same
things:

* exactly equal: the sequence of (unit state, phase, step) transitions, the
  phases in the batch record with their verdicts and fail codes, the alarms
  raised (tag, priority, count, still latched), the end state, the fail code
  and the release decision;
* reported, with a tolerance: when each transition happened, how long each
  phase took and what the cycle consumed.

The second group differs for a known reason. IEC 61131-3 says a REAL is a
32-bit float and the compiled program uses one; the interpreter holds every
REAL as a Python float, which is 64-bit. docs/compiled_crosscheck.md has the
numbers this produces.

    python scripts/crosscheck_compiled.py                 # every scenario
    python scripts/crosscheck_compiled.py --quick         # the cut-down ones
    python scripts/crosscheck_compiled.py --out results/crosscheck/rusty.json

Needs the RuSTy build (scripts/rusty/compile_plc.sh), so Linux only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.harness.cell import (  # noqa: E402
    FAIL_NAMES,
    PHASE_NAMES,
    STEP_NAMES,
    UNIT_STATE_NAMES,
    CipCell,
    fast_recipe,
    load_recipes,
)
from src.plant.model import Faults  # noqa: E402
from src.plant.params import SCENARIOS  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Allowed numeric differences between the two executions. Anything outside
# these fails the run; anything inside is reported in the output file. They are
# as wide as they are because an adaptive rinse ends on ten seconds of a noisy
# conductivity below a threshold, which turns a shift of a scan or two earlier
# in the cycle into several seconds at the endpoint (docs/compiled_crosscheck.md).
TRANSITION_TOLERANCE_S = 10.0
RELATIVE_TOLERANCE = 0.03  # consumption and durations, 3 %
ABSOLUTE_FLOOR = {"duration_s": 10.0, "water_l": 10.0, "energy_kwh": 0.2, "chemical_l": 0.5}

# Differences that are understood, with the reason. Each is reported, not
# failed; a difference not listed here fails the run.
KNOWN_DIFFERENCES = {
    ("circuit-lost", "first_out"): (
        "Two alarms latch on the loss of the circuit release on the same scan in the "
        "interpreter, so first-out is the lower index, SAFETY_TRIP. RuSTy's TON never "
        "sets Q on the scan its input rises, even with PT = T#0s, so the zero-delay "
        "SAFETY_TRIP alarm latches one scan after CIRCUIT_UNAVAILABLE and that one is "
        "first-out."
    ),
}

Event = Tuple[str, Any]  # ("at", seconds) or ("when_phase", phase number)


class Scenario:
    def __init__(
        self,
        name: str,
        recipe: Callable[[], Dict[str, Any]],
        soil: str = "medium",
        faults: Optional[Faults] = None,
        commands: Optional[List[Tuple[Event, str]]] = None,
        freeze_conductivity_when_phase: Optional[int] = None,
        minutes: float = 90.0,
        quick: bool = True,
    ):
        self.name = name
        self.recipe = recipe
        self.soil = soil
        self.faults = faults
        self.commands = commands or []
        self.freeze_when_phase = freeze_conductivity_when_phase
        self.minutes = minutes
        self.quick = quick


def _full(name: str) -> Callable[[], Dict[str, Any]]:
    return lambda: load_recipes()[name]


SCENARIOS_TO_RUN = [
    Scenario("fast-nominal", fast_recipe),
    Scenario("fast-no-acid", lambda: fast_recipe(use_acid=False)),
    Scenario("fast-with-sanitise", lambda: fast_recipe(use_sanitise=True)),
    Scenario("fast-heavy-soil", fast_recipe, soil="heavy"),
    Scenario("supply-pump-trip", fast_recipe, faults=Faults(supply_pump_trip_at_s=400.0)),
    Scenario("return-pump-trip", fast_recipe, faults=Faults(return_pump_trip_at_s=400.0)),
    Scenario("emergency-stop", fast_recipe, faults=Faults(estop_at_s=400.0)),
    Scenario("circuit-lost", fast_recipe, faults=Faults(circuit_lost_at_s=400.0)),
    Scenario(
        "steam-lost",
        lambda: fast_recipe(caustic_temp_sp_c=75.0, caustic_temp_min_c=70.0, phase_max_s=900),
        faults=Faults(steam_lost_at_s=1.0),
    ),
    Scenario(
        "heater-derated",
        lambda: fast_recipe(caustic_hold_s=120, phase_max_s=2400),
        faults=Faults(heater_derate_from_s=1.0, heater_derate_factor=0.4),
    ),
    Scenario("conductivity-stuck-clean", fast_recipe, faults=Faults(conductivity_frozen_at_s=1.0)),
    Scenario(
        "conductivity-stuck-dirty",
        lambda: fast_recipe(rinse_max_s=300, use_acid=False),
        freeze_conductivity_when_phase=3,
    ),
    Scenario(
        "hold-and-restart",
        fast_recipe,
        commands=[(("at", 600.0), "hold"), (("at", 720.0), "restart")],
    ),
    Scenario("operator-abort", fast_recipe, commands=[(("at", 500.0), "abort")]),
    Scenario(
        "abort-reset-rerun",
        fast_recipe,
        commands=[
            (("at", 500.0), "abort"),
            (("at", 560.0), "reset"),
            (("at", 570.0), "start"),
        ],
    ),
    Scenario("adaptive-medium", _full("adaptive"), minutes=120, quick=False),
    Scenario("fixed-conservative-medium", _full("fixed-conservative"), minutes=120, quick=False),
    Scenario("fixed-short-medium", _full("fixed-short"), minutes=120, quick=False),
]


def run(scenario: Scenario, backend: str) -> Dict[str, Any]:
    faults = Faults(**vars(scenario.faults)) if scenario.faults else None
    cell = CipCell(
        scenario.recipe(), SCENARIOS[scenario.soil], faults=faults,
        sample_every_s=1e9, backend=backend,
    )
    rt = cell.runtime
    max_scans = int(scenario.minutes * 60.0 / cell.scan_s)
    pending = list(scenario.commands)
    transitions: List[Dict[str, Any]] = []
    last: Optional[Tuple[int, int, int]] = None
    started = False
    froze = False
    began = time.perf_counter()
    for _ in range(max_scans):
        now = cell.plant.time_s
        if not started and now >= 2.0:
            cell.press("start")
            started = True
        for item in list(pending):
            (kind, value), command = item
            if kind == "at" and now >= value:
                cell.press(command)
                pending.remove(item)
        cell.step()
        state = (
            int(rt.get_path("gUnitState")),
            int(rt.get_path("gPhase")),
            int(rt.get_path("gPhaseStep")),
        )
        if state != last:
            transitions.append(
                {
                    "t_s": round(cell.plant.time_s, 1),
                    "unit_state": UNIT_STATE_NAMES[state[0]],
                    "phase": PHASE_NAMES[state[1]],
                    "step": STEP_NAMES[state[2]],
                }
            )
            last = state
        if scenario.freeze_when_phase is not None and not froze and state[1] == scenario.freeze_when_phase:
            cell.plant.faults.conductivity_frozen_at_s = cell.plant.time_s
            froze = True
        if started and not pending and state[0] in (7, 9):
            break
    result = cell.result().to_dict()
    result["transitions"] = transitions
    result["wall_s"] = round(time.perf_counter() - began, 2)
    result["first_out"] = int(rt.get_path("giFirstOut"))
    result["fail_code_now"] = FAIL_NAMES[int(rt.get_path("gFailCode"))]
    close = getattr(rt, "close", None)
    if close:
        close()
    return result


def _within(field: str, a: float, b: float) -> bool:
    floor = ABSOLUTE_FLOOR.get(field, 0.0)
    return abs(a - b) <= max(floor, RELATIVE_TOLERANCE * max(abs(a), abs(b)))


def compare(name: str, ref: Dict[str, Any], dut: Dict[str, Any]) -> Dict[str, Any]:
    problems: List[str] = []
    known: List[str] = []

    for field in ("completed", "aborted", "verified_clean", "fail_code", "batch_id", "first_out"):
        if ref[field] != dut[field]:
            text = "{0}: interpreter {1!r}, compiled {2!r}".format(field, ref[field], dut[field])
            if (name, field) in KNOWN_DIFFERENCES:
                known.append(text + ". " + KNOWN_DIFFERENCES[(name, field)])
            else:
                problems.append(text)

    def key(t: Dict[str, Any]) -> Tuple[str, str, str]:
        return (t["unit_state"], t["phase"], t["step"])

    ref_seq = [key(t) for t in ref["transitions"]]
    dut_seq = [key(t) for t in dut["transitions"]]
    max_shift = 0.0
    if ref_seq != dut_seq:
        problems.append(
            "transition sequence differs ({0} vs {1} transitions)".format(len(ref_seq), len(dut_seq))
        )
    else:
        for a, b in zip(ref["transitions"], dut["transitions"]):
            max_shift = max(max_shift, abs(a["t_s"] - b["t_s"]))
        if max_shift > TRANSITION_TOLERANCE_S:
            problems.append("a transition moved by {0:.1f} s".format(max_shift))

    phase_keys = ("phase", "pass", "fail_code", "endpoint")
    ref_phases = [tuple(p[k] for k in phase_keys) for p in ref["phases"]]
    dut_phases = [tuple(p[k] for k in phase_keys) for p in dut["phases"]]
    if ref_phases != dut_phases:
        problems.append("batch record phases differ: {0} vs {1}".format(ref_phases, dut_phases))

    alarm_keys = ("tag", "priority", "count", "latched")
    ref_alarms = [tuple(a[k] for k in alarm_keys) for a in ref["alarms"]]
    dut_alarms = [tuple(a[k] for k in alarm_keys) for a in dut["alarms"]]
    if ref_alarms != dut_alarms:
        problems.append("alarms differ: {0} vs {1}".format(ref_alarms, dut_alarms))

    deltas: Dict[str, float] = {}
    for field, kind in (
        ("cycle_time_s", "duration_s"),
        ("fresh_water_l", "water_l"),
        ("recovered_water_l", "water_l"),
        ("effluent_l", "water_l"),
        ("caustic_l", "chemical_l"),
        ("acid_l", "chemical_l"),
        ("energy_kwh", "energy_kwh"),
    ):
        delta = float(dut[field]) - float(ref[field])
        deltas[field] = round(delta, 4)
        if not _within(kind, float(ref[field]), float(dut[field])):
            problems.append("{0}: interpreter {1}, compiled {2}".format(field, ref[field], dut[field]))
    if ref_phases == dut_phases:
        for a, b in zip(ref["phases"], dut["phases"]):
            for field in ("duration_s", "hold_achieved_s", "water_l"):
                kind = "water_l" if field == "water_l" else "duration_s"
                name = "{0}.{1}".format(a["phase"], field)
                deltas[name] = round(float(b[field]) - float(a[field]), 3)
                if not _within(kind, float(a[field]), float(b[field])):
                    problems.append("{0}: interpreter {1}, compiled {2}".format(name, a[field], b[field]))
    alarm_shift = 0.0
    if ref_alarms == dut_alarms:
        for a, b in zip(ref["alarms"], dut["alarms"]):
            alarm_shift = max(alarm_shift, abs(a["first_in_s"] - b["first_in_s"]))
        if alarm_shift > TRANSITION_TOLERANCE_S:
            problems.append("an alarm was raised {0:.1f} s apart".format(alarm_shift))

    return {
        "identical_sequence": ref_seq == dut_seq,
        "transitions": len(ref_seq),
        "max_transition_shift_s": round(max_shift, 1),
        "max_alarm_time_shift_s": round(alarm_shift, 1),
        "deltas_compiled_minus_interpreter": deltas,
        "known_differences": known,
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="cut-down recipes only")
    parser.add_argument("--only", action="append", default=None, help="scenario name")
    parser.add_argument("--out", default=None, help="write the comparison as JSON")
    args = parser.parse_args()

    chosen = [s for s in SCENARIOS_TO_RUN if s.quick or not args.quick]
    if args.only:
        chosen = [s for s in chosen if s.name in args.only]

    report: Dict[str, Any] = {
        "note": (
            "Simulated. Both executions drive the plant model in src/plant; no "
            "hardware or vendor runtime is involved."
        ),
        "tolerances": {
            "transition_s": TRANSITION_TOLERANCE_S,
            "relative": RELATIVE_TOLERANCE,
            "absolute_floor": ABSOLUTE_FLOOR,
        },
        "scenarios": [],
    }
    failures = 0
    for scenario in chosen:
        ref = run(scenario, "interpreter")
        dut = run(scenario, "rusty")
        verdict = compare(scenario.name, ref, dut)
        verdict.update(
            {
                "scenario": scenario.name,
                "soil": scenario.soil,
                "outcome": "{0}{1}, {2}".format(
                    "COMPLETE" if ref["completed"] else ("ABORTED" if ref["aborted"] else "RUNNING"),
                    "" if ref["fail_code"] == "NONE" else " " + ref["fail_code"],
                    "RELEASED" if ref["verified_clean"] else "HELD",
                ),
                "phases": [p["phase"] for p in ref["phases"]],
                "alarms": [a["tag"] for a in ref["alarms"]],
                "wall_s": {"interpreter": ref["wall_s"], "rusty": dut["wall_s"]},
            }
        )
        report["scenarios"].append(verdict)
        status = "ok  " if not verdict["problems"] else "FAIL"
        failures += bool(verdict["problems"])
        print(
            "{0} {1:28s} {2:34s} {3:3d} transitions, max shift {4:4.1f} s, "
            "water {5:+.1f} L, interp {6:5.1f} s, rusty {7:5.1f} s".format(
                status,
                scenario.name,
                verdict["outcome"],
                verdict["transitions"],
                verdict["max_transition_shift_s"],
                verdict["deltas_compiled_minus_interpreter"]["fresh_water_l"],
                ref["wall_s"],
                dut["wall_s"],
            )
        )
        for problem in verdict["problems"]:
            print("       ", problem)
        for note in verdict["known_differences"]:
            print("        known:", note)

    if args.out:
        # wall-clock timings vary run to run and machine to machine
        for entry in report["scenarios"]:
            entry.pop("wall_s", None)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
    print("{0} of {1} scenarios agree".format(len(chosen) - failures, len(chosen)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
