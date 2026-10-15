"""The committed results and the claims the README makes about them.

A README that quotes a number the data no longer supports is worse than no
README, so the headline figures are asserted against results/experiments.json
rather than trusted.
"""

from __future__ import annotations

import json
import os
import re

import pytest
from conftest import REPO_ROOT

RESULTS = os.path.join(REPO_ROOT, "results")
EXPERIMENTS = os.path.join(RESULTS, "experiments.json")
README = os.path.join(REPO_ROOT, "README.md")

pytestmark = pytest.mark.skipif(
    not os.path.exists(EXPERIMENTS),
    reason="run scripts/run_experiments.py first",
)


@pytest.fixture(scope="module")
def payload():
    with open(EXPERIMENTS, "r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def readme():
    with open(README, "r", encoding="utf-8") as handle:
        return handle.read()


def test_the_sweep_covers_every_recipe_and_soil_load(payload):
    assert payload["schema"] == "cip-experiments/1"
    assert len(payload["runs"]) == len(payload["recipes"]) * len(payload["scenarios"])
    seen = {(run["recipe"], run["scenario"]) for run in payload["runs"]}
    assert len(seen) == len(payload["runs"]), "no duplicated cells"


def test_the_summary_is_arithmetic_on_the_runs(payload):
    for recipe, summary in payload["summary"]["per_recipe"].items():
        results = [r["result"] for r in payload["runs"] if r["recipe"] == recipe]
        assert summary["runs"] == len(results)
        assert summary["released"] == sum(1 for r in results if r["verified_clean"])
        assert summary["held"] == len(results) - summary["released"]
        mean_water = sum(r["fresh_water_l"] for r in results) / len(results)
        assert summary["mean_fresh_water_l"] == pytest.approx(mean_water, abs=0.05)
        mean_cycle = sum(r["cycle_time_s"] for r in results) / len(results) / 60.0
        assert summary["mean_cycle_min"] == pytest.approx(mean_cycle, abs=0.01)


def test_the_scan_period_is_the_one_the_st_declares(payload):
    from src.harness.cell import PLC_DIR, PROGRAM_ORDER
    from src.iec61131.runtime import load

    runtime = load(PLC_DIR, PROGRAM_ORDER)
    assert payload["scan_period_s"] == pytest.approx(float(runtime.get_path("C_CYCLE_S")))


def test_every_recipe_that_was_released_really_was_clean(payload):
    limits = payload["summary"]
    for run in payload["runs"]:
        result = run["result"]
        if not result["verified_clean"]:
            continue
        assert result["residual_g_m2"] <= limits["residual_limit_g_m2"], run
        assert (
            result["residual_chemical_ppm"] <= limits["residual_chemical_limit_ppm"]
        ), run


def test_the_controller_and_the_model_agree_on_what_was_consumed(payload):
    """The KPI code reads instruments; the model knows the truth. If these
    drifted apart the batch records would be fiction."""
    for run in payload["runs"]:
        result = run["result"]
        truth = result["plant_totals"]
        assert result["fresh_water_l"] == pytest.approx(truth["fresh_water_l"], rel=0.05)
        assert result["caustic_l"] == pytest.approx(truth["caustic_stock_l"], rel=0.02)
        assert result["acid_l"] == pytest.approx(truth["acid_stock_l"], rel=0.02)
        assert result["energy_kwh"] == pytest.approx(truth["steam_kwh"], rel=0.02)


def test_every_figure_the_readme_shows_exists(readme):
    for path in re.findall(r"!\[[^\]]*\]\((?!https?:)([^)]+)\)", readme):
        assert os.path.exists(os.path.join(REPO_ROOT, path)), path


def test_every_link_the_readme_makes_resolves(readme):
    for target in re.findall(r"\]\((?!https?:)([^)#]+)\)", readme):
        assert os.path.exists(os.path.join(REPO_ROOT, target)), target


def test_the_readme_headline_matches_the_data(payload, readme):
    comparison = payload["summary"]["comparisons"]["adaptive_vs_fixed_conservative"]
    water = comparison["fresh_water_saved_pct"]
    effluent = comparison["effluent_saved_pct"]
    minutes = comparison["cycle_minutes_saved"]
    assert "{0:.0f} % less mains water".format(water) in readme, water
    assert "{0:.0f} % less effluent".format(effluent) in readme, effluent
    assert 3.0 <= minutes < 4.0, "the README says three and a half minutes"
    assert comparison["both_released_every_run"] is True


def test_the_readme_table_matches_the_summary(payload, readme):
    for recipe, summary in payload["summary"]["per_recipe"].items():
        row = next(
            (line for line in readme.splitlines() if line.startswith("| ") and recipe in line),
            None,
        )
        assert row is not None, recipe
        assert "{0:.1f} min".format(summary["mean_cycle_min"]) in row, row
        assert "{0:.0f} L".format(summary["mean_fresh_water_l"]) in row, row
        assert "{0}".format(summary["released"]) in row, row


def test_the_fixed_short_warning_is_still_true(payload):
    risk = payload["summary"]["comparisons"]["fixed_short_risk"]
    assert risk["runs_held"] == 4
    assert risk["runs_over_chemical_limit"] == 4
    assert risk["runs_over_residual_limit"] == 0, (
        "the README says the soil residue is inside the limit but trending the "
        "wrong way; if this fires, that sentence needs rewriting"
    )
