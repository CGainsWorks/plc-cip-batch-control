"""Run one CIP cycle and print the batch record.

    python scripts/run_cycle.py --recipe adaptive --soil heavy
    python scripts/run_cycle.py --recipe fixed-conservative --fault pump-trip

With --trace it also writes the sampled process values to a CSV, which is what
the figures in results/ are built from.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.harness.cell import CipCell, load_recipes  # noqa: E402
from src.plant.model import Faults  # noqa: E402
from src.plant.params import (  # noqa: E402
    RESIDUAL_CHEMICAL_LIMIT_PPM,
    RESIDUAL_LIMIT_G_M2,
    SCENARIOS,
)
from src.report import batch_record  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAULTS = {
    "none": Faults(),
    "pump-trip": Faults(supply_pump_trip_at_s=900.0),
    "steam-lost": Faults(steam_lost_at_s=600.0),
    "heater-derate": Faults(heater_derate_from_s=500.0),
    "circuit-lost": Faults(circuit_lost_at_s=1200.0, circuit_restored_at_s=1400.0),
    "estop": Faults(estop_at_s=1500.0),
    "conductivity-stuck": Faults(conductivity_frozen_at_s=300.0),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", default="adaptive", choices=sorted(load_recipes()))
    parser.add_argument("--soil", default="medium", choices=sorted(SCENARIOS))
    parser.add_argument("--fault", default="none", choices=sorted(FAULTS))
    parser.add_argument("--minutes", type=float, default=120.0)
    parser.add_argument("--trace", default=None, help="write the sampled trace to this CSV")
    parser.add_argument("--json", dest="json_out", default=None, help="write the record here")
    args = parser.parse_args()

    recipe = load_recipes()[args.recipe]
    cell = CipCell(
        recipe,
        SCENARIOS[args.soil],
        faults=FAULTS[args.fault],
        sample_every_s=2.0 if args.trace else 5.0,
    )
    print(
        "scan period {0:.0f} ms, recipe {1}, soil {2} ({3:.0f} g/m2), fault {4}".format(
            cell.scan_s * 1000.0,
            args.recipe,
            args.soil,
            SCENARIOS[args.soil].total_g_m2,
            args.fault,
        )
    )
    result = cell.run_cycle(max_minutes=args.minutes)
    record = batch_record.build(
        result,
        recipe,
        residual_limit_g_m2=RESIDUAL_LIMIT_G_M2,
        chemical_limit_ppm=RESIDUAL_CHEMICAL_LIMIT_PPM,
        notes=["fault injected: " + args.fault] if args.fault != "none" else None,
    )
    print()
    print(batch_record.summarise(record))
    if result.alarms:
        print("  alarms:")
        for alarm in result.alarms:
            print(
                "    P{0} {1} x{2} first in at {3:.0f} s".format(
                    alarm["priority"], alarm["tag"], alarm["count"], alarm["first_in_s"]
                )
            )

    if args.json_out:
        _write_json(args.json_out, record)
        print("\nwrote", os.path.relpath(args.json_out, REPO_ROOT))
    if args.trace:
        _write_trace(args.trace, cell)
        print("wrote", os.path.relpath(args.trace, REPO_ROOT))
    return 0 if result.completed else 1


def _write_json(path: str, record: dict) -> None:
    _ensure_dir(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_trace(path: str, cell: CipCell) -> None:
    _ensure_dir(path)
    rows = cell.samples_as_dicts()
    if not rows:
        return
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _ensure_dir(path: str) -> None:
    directory: Optional[str] = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
