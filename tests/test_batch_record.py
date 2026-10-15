"""The batch record and its hash chain."""

from __future__ import annotations

import copy
import json
import os

import pytest
from conftest import REPO_ROOT

from src.report import batch_record

RECORDS_DIR = os.path.join(REPO_ROOT, "results", "batch_records")


class FakeResult:
    def __init__(self, **overrides):
        self.__dict__.update(
            {
                "recipe": "adaptive",
                "scenario": "medium",
                "completed": True,
                "aborted": False,
                "verified_clean": True,
                "fail_code": "NONE",
                "batch_id": 7,
                "cycle_time_s": 2700.0,
                "fresh_water_l": 2300.0,
                "recovered_water_l": 380.0,
                "effluent_l": 1700.0,
                "caustic_l": 36.0,
                "acid_l": 14.0,
                "energy_kwh": 100.0,
                "residual_g_m2": 0.004,
                "residual_chemical_ppm": 3.5,
                "residual_breakdown": {"loose": 0.0, "bound": 0.002, "mineral": 0.002},
                "scans": 13500,
                "alarms": [],
                "phases": [
                    {
                        "phase": "CAUSTIC_WASH",
                        "pass": True,
                        "fail_code": "NONE",
                        "duration_s": 1200.0,
                        "hold_achieved_s": 720.0,
                        "hold_required_s": 720.0,
                        "endpoint": "verified hold",
                        "min_temp_c": 71.2,
                        "max_temp_c": 78.4,
                        "mean_conc_pct": 2.01,
                        "min_flow_m3h": 17.8,
                        "water_l": 0.0,
                        "chemical_l": 36.0,
                        "energy_kwh": 74.0,
                        "end_conductivity_ms_cm": 0.005,
                    }
                ],
            }
        )
        self.__dict__.update(overrides)


RECIPE = {"name": "adaptive", "description": "test", "adaptive": True, "use_acid": True}


def test_a_record_carries_the_tact_evidence():
    record = batch_record.build(FakeResult(), RECIPE)
    phase = record["phases"][0]
    for field in ("hold_achieved_s", "min_temp_c", "mean_conc_pct", "min_flow_m3h"):
        assert phase[field] is not None
    assert record["release"]["decision"] == "RELEASED"
    assert record["independent_check"]["soil_within_limit"] is True
    assert record["independent_check"]["chemical_within_limit"] is True


def test_sentinels_become_nulls_rather_than_999():
    result = FakeResult()
    result.phases[0] = dict(result.phases[0], min_temp_c=999.0, min_flow_m3h=999.0, max_temp_c=-999.0)
    record = batch_record.build(result, RECIPE)
    assert record["phases"][0]["min_temp_c"] is None
    assert record["phases"][0]["min_flow_m3h"] is None
    assert record["phases"][0]["max_temp_c"] is None


def test_a_held_batch_says_why():
    record = batch_record.build(FakeResult(verified_clean=False), RECIPE)
    assert record["release"]["decision"] == "HELD"
    assert record["release"]["reasons"]


def test_a_failed_phase_is_named_in_the_reasons():
    result = FakeResult(verified_clean=False, completed=False)
    result.phases[0] = dict(result.phases[0], **{"pass": False, "fail_code": "PUMP_FAULT"})
    record = batch_record.build(result, RECIPE)
    assert any("CAUSTIC_WASH" in reason for reason in record["release"]["reasons"])


def test_the_chain_verifies_and_tampering_is_detected():
    first = batch_record.build(FakeResult(batch_id=1), RECIPE)
    second = batch_record.build(FakeResult(batch_id=2), RECIPE, previous_signature=first["signature"])
    third = batch_record.build(FakeResult(batch_id=3), RECIPE, previous_signature=second["signature"])
    chain = [first, second, third]
    assert batch_record.verify_chain(chain) == []

    edited = copy.deepcopy(chain)
    edited[1]["phases"][0]["min_temp_c"] = 90.0
    problems = batch_record.verify_chain(edited)
    assert problems and "batch 2" in problems[0]

    dropped = [chain[0], chain[2]]
    assert batch_record.verify_chain(dropped), "a missing record breaks the chain"


def test_a_record_round_trips_through_json(tmp_path):
    record = batch_record.build(FakeResult(), RECIPE)
    path = batch_record.write(str(tmp_path), record)
    with open(path, "r", encoding="utf-8") as handle:
        reloaded = json.load(handle)
    assert batch_record.verify_chain([reloaded]) == []


def test_the_summary_is_readable():
    text = batch_record.summarise(batch_record.build(FakeResult(), RECIPE))
    assert "CAUSTIC_WASH" in text
    assert "RELEASED" in text
    assert "independent check" in text


@pytest.mark.skipif(not os.path.isdir(RECORDS_DIR), reason="results have not been generated")
def test_the_committed_records_form_one_unbroken_chain():
    names = sorted(n for n in os.listdir(RECORDS_DIR) if n.endswith(".json"))
    assert names, "results/batch_records should hold the sweep"
    records = []
    for name in names:
        with open(os.path.join(RECORDS_DIR, name), "r", encoding="utf-8") as handle:
            records.append(json.load(handle))
    assert batch_record.verify_chain(records) == []
