"""Re-run part of the experiment sweep and check it against the committed file.

The simulation is deterministic, so a rerun of any cell of the matrix has to
land on the numbers already in results/experiments.json. CI runs a couple of
cells rather than all twelve, which is enough to catch a change in the ST, the
plant model or the interpreter that nobody meant to make.

    python scripts/verify_results.py --recipe adaptive --scenario medium
    python scripts/verify_results.py --all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.harness.cell import CipCell, load_recipes  # noqa: E402
from src.plant.params import SCENARIOS  # noqa: E402
from src.report import batch_record  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO_ROOT, "results")
EXPERIMENTS = os.path.join(RESULTS, "experiments.json")
RECORDS = os.path.join(RESULTS, "batch_records")

# Fields compared, with the tolerance each is allowed. Everything derived from
# integration gets a small relative allowance; verdicts have to match exactly.
NUMERIC_FIELDS = {
    "cycle_time_s": 0.5,
    "fresh_water_l": 1.0,
    "recovered_water_l": 1.0,
    "effluent_l": 1.0,
    "caustic_l": 0.05,
    "acid_l": 0.05,
    "energy_kwh": 0.05,
    "residual_g_m2": 0.001,
    "residual_chemical_ppm": 0.5,
}
EXACT_FIELDS = ("completed", "aborted", "verified_clean", "fail_code")


def load_expected() -> Dict[Tuple[str, str], Dict[str, Any]]:
    with open(EXPERIMENTS, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return {(run["recipe"], run["scenario"]): run["result"] for run in payload["runs"]}


def compare(expected: Dict[str, Any], actual: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    for field in EXACT_FIELDS:
        if expected[field] != actual[field]:
            problems.append(
                "{0}: expected {1!r}, got {2!r}".format(field, expected[field], actual[field])
            )
    for field, tolerance in NUMERIC_FIELDS.items():
        if field not in expected:
            continue
        delta = abs(float(expected[field]) - float(actual[field]))
        if delta > tolerance:
            problems.append(
                "{0}: expected {1}, got {2} (delta {3:.4f} > {4})".format(
                    field, expected[field], actual[field], delta, tolerance
                )
            )
    if len(expected["phases"]) != len(actual["phases"]):
        problems.append(
            "phase count: expected {0}, got {1}".format(
                len(expected["phases"]), len(actual["phases"])
            )
        )
    else:
        for want, got in zip(expected["phases"], actual["phases"]):
            if want["phase"] != got["phase"] or want["pass"] != got["pass"]:
                problems.append(
                    "phase {0}: expected pass={1}, got {2} pass={3}".format(
                        want["phase"], want["pass"], got["phase"], got["pass"]
                    )
                )
    return problems


def verify_records() -> List[str]:
    if not os.path.isdir(RECORDS):
        return ["results/batch_records is missing"]
    names = sorted(n for n in os.listdir(RECORDS) if n.endswith(".json"))
    if not names:
        return ["results/batch_records holds no records"]
    records = []
    for name in names:
        with open(os.path.join(RECORDS, name), "r", encoding="utf-8") as handle:
            records.append(json.load(handle))
    return batch_record.verify_chain(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", action="append", default=None)
    parser.add_argument("--scenario", action="append", default=None)
    parser.add_argument("--all", action="store_true", help="re-run every committed cell")
    parser.add_argument("--skip-records", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(EXPERIMENTS):
        print("results/experiments.json is missing; run scripts/run_experiments.py")
        return 1
    expected = load_expected()

    if args.all:
        wanted = sorted(expected)
    else:
        recipes = args.recipe or ["adaptive"]
        scenarios = args.scenario or ["medium"]
        wanted = [(r, s) for r in recipes for s in scenarios]

    catalogue = load_recipes()
    failures = 0
    for recipe, scenario in wanted:
        if (recipe, scenario) not in expected:
            print("no committed result for {0}/{1}".format(recipe, scenario))
            failures += 1
            continue
        cell = CipCell(catalogue[recipe], SCENARIOS[scenario], sample_every_s=1e9)
        result = cell.run_cycle(max_minutes=120).to_dict()
        problems = compare(expected[(recipe, scenario)], result)
        if problems:
            failures += 1
            print("MISMATCH {0}/{1}".format(recipe, scenario))
            for problem in problems:
                print("   ", problem)
        else:
            print(
                "ok {0}/{1}: {2:.1f} min, {3:.0f} L, {4}".format(
                    recipe,
                    scenario,
                    result["cycle_time_s"] / 60.0,
                    result["fresh_water_l"],
                    "RELEASED" if result["verified_clean"] else "HELD",
                )
            )

    if not args.skip_records:
        problems = verify_records()
        if problems:
            failures += 1
            print("batch record chain is broken:")
            for problem in problems:
                print("   ", problem)
        else:
            print("batch record chain verifies")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
