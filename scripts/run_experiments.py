"""Run every recipe against every soil load and write the results.

Produces results/experiments.json, the figures used in the README and one
hash-chained batch record per run. Deterministic: the plant's noise generator is
seeded per run, no wall-clock time enters any output, so a rerun reproduces the
files byte for byte. scripts/verify_results.py depends on that.

    python scripts/run_experiments.py [--recipes a,b] [--scenarios a,b] [--quick]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.harness.cell import CipCell, load_recipes  # noqa: E402
from src.plant.params import (  # noqa: E402
    RESIDUAL_CHEMICAL_LIMIT_PPM,
    RESIDUAL_LIMIT_G_M2,
    SCENARIOS,
)
from src.report import batch_record, svg  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO_ROOT, "results")
RECORDS = os.path.join(RESULTS, "batch_records")

CACHE = os.path.join(RESULTS, ".cache")

RECIPES = ["fixed-conservative", "fixed-short", "adaptive"]
SCENARIO_ORDER = ["light", "medium", "heavy", "worst"]

PHASE_COLOURS = {
    "PRE_RINSE": "#4aa3a3",
    "CAUSTIC_WASH": "#2c6fbb",
    "INTERMEDIATE_RINSE": "#8b5cb8",
    "ACID_WASH": "#c8582a",
    "FINAL_RINSE": "#3f8f5f",
    "SANITISE": "#b8933f",
}


def fingerprint() -> str:
    """Hash everything that could change a cell's answer.

    The ST, the interpreter, the plant model, the harness and the recipes. Not
    the figures or the report code, which only format what came out.
    """
    digest = hashlib.sha256()
    roots = [
        os.path.join(REPO_ROOT, "plc"),
        os.path.join(REPO_ROOT, "src", "iec61131"),
        os.path.join(REPO_ROOT, "src", "plant"),
        os.path.join(REPO_ROOT, "src", "harness"),
    ]
    paths = [os.path.join(REPO_ROOT, "config", "recipes.json")]
    for root in roots:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in sorted(filenames):
                if name.endswith((".st", ".py", ".json")):
                    paths.append(os.path.join(dirpath, name))
    for path in sorted(paths):
        digest.update(os.path.relpath(path, REPO_ROOT).replace("\\", "/").encode("utf-8"))
        with open(path, "rb") as handle:
            digest.update(handle.read().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def cache_path(recipe: str, scenario: str) -> str:
    return os.path.join(CACHE, "{0}-{1}.json".format(recipe, scenario))


def load_cached(recipe: str, scenario: str, stamp: str):
    path = cache_path(recipe, scenario)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            cached = json.load(handle)
    except ValueError:
        return None
    return cached if cached.get("fingerprint") == stamp else None


def save_cached(run: dict, stamp: str) -> None:
    os.makedirs(CACHE, exist_ok=True)
    payload = dict(run)
    payload["fingerprint"] = stamp
    with open(cache_path(run["recipe"], run["scenario"]), "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def run_matrix(
    recipes: List[str],
    scenarios: List[str],
    sample_every_s: float,
    use_cache: bool = True,
) -> List[Dict[str, Any]]:
    catalogue = load_recipes()
    runs: List[Dict[str, Any]] = []
    stamp = fingerprint()
    for recipe_name in recipes:
        for scenario_name in scenarios:
            if use_cache:
                cached = load_cached(recipe_name, scenario_name, stamp)
                if cached is not None:
                    print(
                        "{0:<20} {1:<7} cached".format(recipe_name, scenario_name), flush=True
                    )
                    runs.append(cached)
                    continue
            started = time.time()
            cell = CipCell(
                catalogue[recipe_name],
                SCENARIOS[scenario_name],
                sample_every_s=sample_every_s,
            )
            result = cell.run_cycle(max_minutes=120)
            elapsed = time.time() - started
            print(
                "{0:<20} {1:<7} {2:>6.1f} min  {3:>6.0f} L fresh  {4:>6.1f} kWh  "
                "residual {5:>7.4f} g/m2  {6}  [{7:.0f} s wall]".format(
                    recipe_name,
                    scenario_name,
                    result.cycle_time_s / 60.0,
                    result.fresh_water_l,
                    result.energy_kwh,
                    result.residual_g_m2,
                    "RELEASED" if result.verified_clean else "HELD",
                    elapsed,
                ),
                flush=True,
            )
            run = {
                "recipe": recipe_name,
                "scenario": scenario_name,
                "result": result.to_dict(),
                "samples": cell.samples_as_dicts(),
                "scan_period_s": cell.scan_s,
            }
            if use_cache:
                save_cached(run, stamp)
            runs.append(run)
    return runs


def summarise(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_key = {(r["recipe"], r["scenario"]): r["result"] for r in runs}
    recipes = sorted({r["recipe"] for r in runs})
    scenarios = [s for s in SCENARIO_ORDER if s in {r["scenario"] for r in runs}]

    summary: Dict[str, Any] = {
        "residual_limit_g_m2": RESIDUAL_LIMIT_G_M2,
        "residual_chemical_limit_ppm": RESIDUAL_CHEMICAL_LIMIT_PPM,
        "per_recipe": {},
        "comparisons": {},
    }
    for recipe in recipes:
        results = [by_key[(recipe, s)] for s in scenarios if (recipe, s) in by_key]
        if not results:
            continue
        summary["per_recipe"][recipe] = {
            "runs": len(results),
            "released": sum(1 for r in results if r["verified_clean"]),
            "held": sum(1 for r in results if not r["verified_clean"]),
            "over_residual_limit": sum(
                1 for r in results if r["residual_g_m2"] > RESIDUAL_LIMIT_G_M2
            ),
            "over_chemical_limit": sum(
                1
                for r in results
                if r["residual_chemical_ppm"] > RESIDUAL_CHEMICAL_LIMIT_PPM
            ),
            "worst_residual_g_m2": round(max(r["residual_g_m2"] for r in results), 4),
            "worst_chemical_ppm": round(max(r["residual_chemical_ppm"] for r in results), 1),
            "mean_cycle_min": round(
                sum(r["cycle_time_s"] for r in results) / len(results) / 60.0, 2
            ),
            "mean_fresh_water_l": round(
                sum(r["fresh_water_l"] for r in results) / len(results), 1
            ),
            "mean_effluent_l": round(sum(r["effluent_l"] for r in results) / len(results), 1),
            "mean_energy_kwh": round(sum(r["energy_kwh"] for r in results) / len(results), 2),
            "mean_chemical_l": round(
                sum(r["caustic_l"] + r["acid_l"] for r in results) / len(results), 2
            ),
        }

    if "adaptive" in summary["per_recipe"] and "fixed-conservative" in summary["per_recipe"]:
        adaptive = summary["per_recipe"]["adaptive"]
        fixed = summary["per_recipe"]["fixed-conservative"]
        summary["comparisons"]["adaptive_vs_fixed_conservative"] = {
            "fresh_water_saved_pct": _pct(fixed["mean_fresh_water_l"], adaptive["mean_fresh_water_l"]),
            "effluent_saved_pct": _pct(fixed["mean_effluent_l"], adaptive["mean_effluent_l"]),
            "cycle_time_saved_pct": _pct(fixed["mean_cycle_min"], adaptive["mean_cycle_min"]),
            "energy_saved_pct": _pct(fixed["mean_energy_kwh"], adaptive["mean_energy_kwh"]),
            "cycle_minutes_saved": round(fixed["mean_cycle_min"] - adaptive["mean_cycle_min"], 2),
            "fresh_water_saved_l": round(
                fixed["mean_fresh_water_l"] - adaptive["mean_fresh_water_l"], 1
            ),
            "both_released_every_run": adaptive["held"] == 0 and fixed["held"] == 0,
        }
    if "fixed-short" in summary["per_recipe"]:
        short = summary["per_recipe"]["fixed-short"]
        summary["comparisons"]["fixed_short_risk"] = {
            "runs_held": short["held"],
            "runs_over_residual_limit": short["over_residual_limit"],
            "runs_over_chemical_limit": short["over_chemical_limit"],
            "worst_residual_g_m2": short["worst_residual_g_m2"],
            "mean_fresh_water_l": short["mean_fresh_water_l"],
        }
    return summary


def _pct(before: float, after: float) -> float:
    if not before:
        return 0.0
    return round(100.0 * (before - after) / before, 1)


def write_figures(runs: List[Dict[str, Any]]) -> List[str]:
    by_key = {(r["recipe"], r["scenario"]): r for r in runs}
    recipes = [r for r in RECIPES if any(k[0] == r for k in by_key)]
    scenarios = [s for s in SCENARIO_ORDER if any(k[1] == s for k in by_key)]
    written: List[str] = []

    def series(field: str) -> List:
        return [
            (
                recipe,
                [by_key[(recipe, s)]["result"][field] for s in scenarios if (recipe, s) in by_key],
            )
            for recipe in recipes
        ]

    written.append(
        _write(
            "water_by_scenario.svg",
            svg.grouped_bars(
                scenarios,
                series("fresh_water_l"),
                title="Mains water per cycle",
                subtitle="Lower is better. The adaptive recipe stops each rinse on the return "
                "conductivity instead of the clock.",
                y_label="litres of mains water",
            ),
        )
    )
    written.append(
        _write(
            "cycle_time_by_scenario.svg",
            svg.grouped_bars(
                scenarios,
                [
                    (name, [value / 60.0 for value in values])
                    for name, values in series("cycle_time_s")
                ],
                title="Cycle time per soil load",
                subtitle="Time the circuit is out of production.",
                y_label="minutes",
                value_format="{0:.1f}",
            ),
        )
    )
    written.append(
        _write(
            "residual_by_scenario.svg",
            svg.grouped_bars(
                scenarios,
                series("residual_g_m2"),
                title="Residual soil after the cycle, against the release limit",
                subtitle="Measured by the plant model, not by the skid. The short fixed recipe "
                "is the cheap one; it is also the one that leaves soil behind.",
                y_label="g/m2 remaining",
                value_format="{0:.3f}",
            ),
        )
    )

    trace_runs = [
        by_key[(recipe, "heavy")] for recipe in recipes if (recipe, "heavy") in by_key
    ]
    if trace_runs:
        traces = []
        for run in trace_runs:
            samples = [s for s in run["samples"] if s["phase"] == 5]
            if not samples:
                continue
            origin = samples[0]["t_s"]
            traces.append(
                (
                    run["recipe"],
                    [(s["t_s"] - origin) / 60.0 for s in samples],
                    [s["conductivity_delta"] for s in samples],
                )
            )
        if traces:
            written.append(
                _write(
                    "final_rinse_endpoint.svg",
                    svg.line_chart(
                        traces,
                        title="Final rinse: return conductivity above the incoming water",
                        subtitle="Heavy soil. The adaptive recipe stops when the trace has been "
                        "under the limit for ten seconds; the fixed ones stop on a timer.",
                        x_label="minutes into the phase",
                        y_label="return minus supply, mS/cm",
                        hlines=[(0.10, "release limit 0.10 mS/cm")],
                    ),
                )
            )

    rows = []
    for recipe in recipes:
        run = by_key.get((recipe, "heavy"))
        if not run:
            continue
        bars = []
        cursor = 0.0
        for phase in run["result"]["phases"]:
            end = cursor + phase["duration_s"]
            bars.append((phase["phase"], cursor, end))
            cursor = end
        rows.append((recipe, bars))
    if rows:
        written.append(
            _write(
                "phase_timeline.svg",
                svg.phase_timeline(
                    rows,
                    title="Where the cycle time goes, heavy soil",
                    subtitle="Washes hold for their verified time in every recipe; only the "
                    "rinses differ.",
                    phase_colours=PHASE_COLOURS,
                    height=90 + 60 * len(rows),
                ),
            )
        )

    medium = by_key.get(("adaptive", "medium"))
    if medium:
        samples = medium["samples"]
        written.append(
            _write(
                "cycle_trace.svg",
                svg.line_chart(
                    [
                        (
                            "return temperature, C",
                            [s["t_s"] / 60.0 for s in samples],
                            [s["temp_return_c"] for s in samples],
                        ),
                        (
                            "supply flow, m3/h",
                            [s["t_s"] / 60.0 for s in samples],
                            [s["flow_m3h"] for s in samples],
                        ),
                        (
                            "balance tank level, %",
                            [s["t_s"] / 60.0 for s in samples],
                            [s["level_pct"] for s in samples],
                        ),
                    ],
                    title="One adaptive cycle on medium soil",
                    subtitle="Phases run left to right: pre-rinse, caustic, intermediate rinse, "
                    "acid, final rinse.",
                    x_label="minutes",
                    y_label="see legend",
                ),
            )
        )
    return written


def _write(name: str, markup: str) -> str:
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, name)
    svg.write(path, markup)
    return path


def write_records(runs: List[Dict[str, Any]]) -> List[str]:
    catalogue = load_recipes()
    os.makedirs(RECORDS, exist_ok=True)
    for stale in sorted(os.listdir(RECORDS)):
        if stale.endswith(".json"):
            os.remove(os.path.join(RECORDS, stale))
    previous = batch_record.GENESIS
    written: List[str] = []
    for index, run in enumerate(runs, start=1):
        result = _as_result(run["result"])
        record = batch_record.build(
            result,
            catalogue[run["recipe"]],
            previous_hash=previous,
            residual_limit_g_m2=RESIDUAL_LIMIT_G_M2,
            chemical_limit_ppm=RESIDUAL_CHEMICAL_LIMIT_PPM,
            notes=["simulated run {0} of the experiment sweep".format(index)],
        )
        previous = record["record_hash"]
        written.append(
            batch_record.write(
                RECORDS,
                record,
                filename="{0:02d}-{1}-{2}.json".format(index, run["recipe"], run["scenario"]),
            )
        )
    return written


class _ResultView:
    """Adapts the serialised result dict back to the attribute access build() wants."""

    def __init__(self, data: Dict[str, Any]):
        self.__dict__.update(data)


def _as_result(data: Dict[str, Any]) -> Any:
    return _ResultView(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipes", default=",".join(RECIPES))
    parser.add_argument("--scenarios", default=",".join(SCENARIO_ORDER))
    parser.add_argument(
        "--quick",
        action="store_true",
        help="one recipe pair on medium soil only, for a smoke test",
    )
    parser.add_argument("--sample-every", type=float, default=5.0)
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="re-run every cell even if results/.cache already holds it",
    )
    parser.add_argument("--out", default=os.path.join(RESULTS, "experiments.json"))
    args = parser.parse_args()

    recipes = ["fixed-conservative", "adaptive"] if args.quick else args.recipes.split(",")
    scenarios = ["medium"] if args.quick else args.scenarios.split(",")

    runs = run_matrix(recipes, scenarios, args.sample_every, use_cache=not args.no_cache)
    summary = summarise(runs)

    payload = {
        "schema": "cip-experiments/1",
        "scan_period_s": runs[0]["scan_period_s"],
        "recipes": recipes,
        "scenarios": scenarios,
        "summary": summary,
        "runs": [
            {"recipe": run["recipe"], "scenario": run["scenario"], "result": run["result"]}
            for run in runs
        ],
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print("wrote", os.path.relpath(args.out, REPO_ROOT))

    for path in write_figures(runs):
        print("wrote", os.path.relpath(path, REPO_ROOT))
    for path in write_records(runs):
        print("wrote", os.path.relpath(path, REPO_ROOT))

    comparison = summary["comparisons"].get("adaptive_vs_fixed_conservative")
    if comparison:
        print(
            "\nadaptive vs fixed-conservative: {0}% less mains water, {1} min shorter, "
            "{2}% less steam".format(
                comparison["fresh_water_saved_pct"],
                comparison["cycle_minutes_saved"],
                comparison["energy_saved_pct"],
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
