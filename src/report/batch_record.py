"""The electronic batch record.

What a food-safety auditor asks for after a cleaning cycle is not "did the
program finish" but "what were the time, action, concentration and temperature
and who says so". This module turns the PLC's own gResults array into that
document and chains each record to the previous one by SHA-256, so an edit to a
record, or a record dropped from the middle of a run, breaks the chain.

That makes the records tamper-evident, not signed. The chain has no key: anyone
who can rewrite the files can recompute every hash after the one they changed,
so it only shows tampering by someone who does not, or against a latest hash
that is kept somewhere they cannot reach. Nothing in a record says who
produced it. Electronic signatures in the 21 CFR Part 11 sense would need a key
per operator and a trusted time source; docs/limitations.md lists both.

Nothing here carries a wall-clock timestamp: the whole point of the simulation
is that a rerun reproduces the file byte for byte and a real installation
would take its stamp from the controller's clock. That is noted as a
limitation in docs/limitations.md rather than papered over.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Sequence

SCHEMA = "cip-batch-record/2"  # 2: signature fields renamed to what they are
GENESIS = "0" * 64

# The criteria a phase has to meet, in the order an auditor reads them.
TACT_FIELDS = ("hold_achieved_s", "min_temp_c", "mean_conc_pct", "min_flow_m3h")


def canonical(record: Dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def chain_hash(record: Dict[str, Any], previous: str) -> str:
    """SHA-256 of the canonical record and the previous record's hash. No key."""
    payload = canonical(record) + "|" + previous
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build(
    result: Any,
    recipe: Dict[str, Any],
    previous_hash: str = GENESIS,
    residual_limit_g_m2: float = 1.0,
    chemical_limit_ppm: float = 50.0,
    notes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Assemble a hash-chained record from one CycleResult."""
    phases = []
    for phase in result.phases:
        entry = dict(phase)
        # 999 and -999 are the phase engine's "never observed" markers; a
        # record should say so rather than print a sentinel.
        if entry.get("min_temp_c", 0) > 900:
            entry["min_temp_c"] = None
        if entry.get("max_temp_c", 0) < -900:
            entry["max_temp_c"] = None
        if entry.get("min_flow_m3h", 0) > 900:
            entry["min_flow_m3h"] = None
        phases.append(entry)

    failed = [p["phase"] for p in phases if not p["pass"]]
    reasons: List[str] = []
    if not result.completed:
        reasons.append("cycle did not reach COMPLETE")
    if failed:
        reasons.append("phases did not meet their criteria: " + ", ".join(failed))
    if result.completed and not result.verified_clean:
        reasons.append("final rinse did not reach the conductivity endpoint")

    body: Dict[str, Any] = {
        "schema": SCHEMA,
        "batch_id": result.batch_id,
        "unit": "CIP-01",
        "circuit": "TANK-CIRCUIT-A",
        "recipe": {
            "name": recipe.get("name"),
            "description": recipe.get("description"),
            "adaptive": bool(recipe.get("adaptive", False)),
            "use_acid": bool(recipe.get("use_acid", False)),
            "use_sanitise": bool(recipe.get("use_sanitise", False)),
            "caustic_conc_sp_pct": recipe.get("caustic_conc_sp_pct"),
            "caustic_temp_min_c": recipe.get("caustic_temp_min_c"),
            "caustic_hold_s": recipe.get("caustic_hold_s"),
            "rinse_end_cond_ms_cm": recipe.get("rinse_end_cond_ms_cm"),
        },
        "soil_scenario": result.scenario,
        "cycle": {
            "completed": result.completed,
            "aborted": result.aborted,
            "fail_code": result.fail_code,
            "cycle_time_s": result.cycle_time_s,
            "scans": result.scans,
        },
        "phases": phases,
        "consumption": {
            "fresh_water_l": result.fresh_water_l,
            "recovered_water_l": result.recovered_water_l,
            "effluent_l": result.effluent_l,
            "caustic_concentrate_l": result.caustic_l,
            "acid_concentrate_l": result.acid_l,
            "steam_energy_kwh": result.energy_kwh,
        },
        "alarms": result.alarms,
        "release": {
            "verified_clean": result.verified_clean,
            "decision": "RELEASED" if result.verified_clean else "HELD",
            "reasons": reasons,
        },
        "independent_check": {
            "note": (
                "Residual soil is tracked by the plant model and measured by no "
                "instrument on the skid. It is here to test the controller's "
                "release decision, not to inform it."
            ),
            "residual_soil_g_m2": result.residual_g_m2,
            "residual_limit_g_m2": residual_limit_g_m2,
            "soil_within_limit": result.residual_g_m2 <= residual_limit_g_m2,
            "breakdown_g_m2": result.residual_breakdown,
            "residual_chemical_ppm": getattr(result, "residual_chemical_ppm", 0.0),
            "chemical_limit_ppm": chemical_limit_ppm,
            "chemical_within_limit": getattr(result, "residual_chemical_ppm", 0.0)
            <= chemical_limit_ppm,
        },
        "notes": list(notes or []),
    }
    body["previous_hash"] = previous_hash
    body["record_hash"] = chain_hash(body, previous_hash)
    return body


def verify_chain(records: Sequence[Dict[str, Any]]) -> List[str]:
    """Recompute every hash in the chain. Returns a list of problems, empty if sound."""
    problems: List[str] = []
    expected_previous = GENESIS
    for index, record in enumerate(records):
        body = {k: v for k, v in record.items() if k != "record_hash"}
        recomputed = chain_hash(body, record.get("previous_hash", ""))
        if record.get("previous_hash") != expected_previous:
            problems.append(
                "record {0} (batch {1}) does not follow the previous record".format(
                    index, record.get("batch_id")
                )
            )
        if recomputed != record.get("record_hash"):
            problems.append(
                "record {0} (batch {1}) hash does not match its contents".format(
                    index, record.get("batch_id")
                )
            )
        expected_previous = record.get("record_hash", "")
    return problems


def write(directory: str, record: Dict[str, Any], filename: Optional[str] = None) -> str:
    os.makedirs(directory, exist_ok=True)
    name = filename or "batch-{0:04d}.json".format(record["batch_id"])
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def summarise(record: Dict[str, Any]) -> str:
    """A few lines an operator would actually read off the screen."""
    release = record["release"]
    lines = [
        "Batch {0}  recipe {1}  soil {2}".format(
            record["batch_id"], record["recipe"]["name"], record["soil_scenario"]
        ),
        "  {0} in {1:.1f} min".format(
            "COMPLETE" if record["cycle"]["completed"] else "ABORTED " + record["cycle"]["fail_code"],
            record["cycle"]["cycle_time_s"] / 60.0,
        ),
    ]
    for phase in record["phases"]:
        temp = "  -" if phase["min_temp_c"] is None else "{0:5.1f}".format(phase["min_temp_c"])
        if phase.get("endpoint") == "conductivity":
            criterion = "{0:.0f} s to endpoint".format(phase["duration_s"])
        else:
            criterion = "{0:.0f}/{1:.0f} s verified hold".format(
                phase["hold_achieved_s"], phase["hold_required_s"]
            )
        lines.append(
            "  {0:<20} {1:<20} {2:<22} minT {3} C  conc {4:4.2f} %  "
            "end dC {5:+.3f} mS/cm".format(
                phase["phase"],
                "pass" if phase["pass"] else "FAIL " + phase["fail_code"],
                criterion,
                temp,
                phase["mean_conc_pct"],
                phase["end_conductivity_ms_cm"],
            )
        )

    consumption = record["consumption"]
    lines.append(
        "  water {0:.0f} L fresh + {1:.0f} L recovered   chemical {2:.1f} L   "
        "steam {3:.1f} kWh".format(
            consumption["fresh_water_l"],
            consumption["recovered_water_l"],
            consumption["caustic_concentrate_l"] + consumption["acid_concentrate_l"],
            consumption["steam_energy_kwh"],
        )
    )
    lines.append(
        "  {0}{1}".format(
            release["decision"],
            "" if release["decision"] == "RELEASED" else ": " + "; ".join(release["reasons"]),
        )
    )
    check = record["independent_check"]
    lines.append(
        "  independent check: soil {0:.4f} g/m2 (limit {1:.1f}) {2}; "
        "detergent {3:.1f} ppm (limit {4:.0f}) {5}".format(
            check["residual_soil_g_m2"],
            check["residual_limit_g_m2"],
            "OK" if check["soil_within_limit"] else "OVER",
            check["residual_chemical_ppm"],
            check["chemical_limit_ppm"],
            "OK" if check["chemical_within_limit"] else "OVER",
        )
    )
    return "\n".join(lines)
