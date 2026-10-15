"""Run the cell live behind a Modbus TCP server and an operator page.

    python scripts/serve_hmi.py --speed 20

Opens http://127.0.0.1:8080 for the page and 127.0.0.1:5020 for Modbus TCP.
The scan loop runs in this process; the servers run on their own threads and
only ever touch the shared image, never the interpreter.

--speed is a simulation speed multiplier. 1 is real time, which for a CIP cycle
means about fifty minutes; 20 is a good compromise for watching it work.

The process stops on its own after --seconds of wall clock (default 600) or on
Ctrl-C, so it will not outlive a terminal that has been walked away from.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from types import FrameType
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.harness.cell import CipCell, load_recipes  # noqa: E402
from src.harness.io_map import build_io_map  # noqa: E402
from src.plant.params import SCENARIOS  # noqa: E402
from src.services.hmi import HmiServer, HmiState  # noqa: E402
from src.services.modbus_tcp import ModbusBridge, ModbusServer  # noqa: E402

_stop = False


def _handle_signal(_signum: int, _frame: Optional[FrameType]) -> None:
    global _stop
    _stop = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", default="adaptive", choices=sorted(load_recipes()))
    parser.add_argument("--soil", default="medium", choices=sorted(SCENARIOS))
    parser.add_argument("--speed", type=float, default=20.0)
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--modbus-port", type=int, default=5020)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--seconds", type=float, default=600.0, help="wall-clock run limit")
    parser.add_argument(
        "--autostart",
        action="store_true",
        help="press Start after two simulated seconds instead of waiting for the operator",
    )
    args = parser.parse_args()

    recipe = load_recipes()[args.recipe]
    cell = CipCell(recipe, SCENARIOS[args.soil], external_commands=True, sample_every_s=1e9)
    bridge = ModbusBridge(cell.runtime, build_io_map(cell.runtime))
    state = HmiState(bridge, recipe_name=args.recipe)

    modbus = ModbusServer(bridge, args.host, args.modbus_port)
    http = HmiServer(state, args.host, args.http_port)
    modbus.serve_in_background()
    http.serve_in_background()

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):  # not available on every platform
        pass

    print("operator page  http://{0}:{1}".format(args.host, http.port))
    print("modbus tcp     {0}:{1}".format(args.host, modbus.port))
    print(
        "recipe {0}, soil {1}, {2:.0f}x real time, stopping after {3:.0f} s".format(
            args.recipe, args.soil, args.speed, args.seconds
        )
    )
    if args.autostart:
        print("autostart: the cycle will begin shortly")

    started_wall = time.time()
    publish_every = max(int(round(0.25 / cell.scan_s)), 1)
    autostarted = False
    exit_code = 0
    try:
        while not _stop:
            wall = time.time() - started_wall
            if wall >= args.seconds:
                print("\nwall-clock limit reached, stopping")
                # With --autostart this is a failure: the cycle was supposed to
                # finish. CI relies on that distinction.
                exit_code = 1 if args.autostart else 0
                break
            # Keep simulated time in step with the requested speed multiplier.
            target_sim = wall * args.speed
            if cell.runtime.now > target_sim:
                time.sleep(min((cell.runtime.now - target_sim) / args.speed, 0.05))
                continue

            bridge.apply_writes()
            if args.autostart and not autostarted and cell.runtime.now >= 2.0:
                cell.runtime.set_path("xCmdStart", True)
                autostarted = True
            cell.step()
            if cell.runtime.scan_count % publish_every == 0:
                bridge.refresh()
                state.refresh()
            unit = int(cell.runtime.get_path("gUnitState"))
            if autostarted and unit in (7, 9):
                bridge.refresh()
                state.refresh()
                print(
                    "\ncycle finished: {0}".format("COMPLETE" if unit == 7 else "ABORTED"),
                )
                print(
                    "  {0:.1f} min, {1:.0f} L fresh water, {2:.1f} kWh, release {3}".format(
                        float(cell.runtime.get_path("gKpi.rCycleTimeS")) / 60.0,
                        float(cell.runtime.get_path("gKpi.rFreshWaterL")),
                        float(cell.runtime.get_path("gKpi.rEnergyKWh")),
                        "RELEASED"
                        if bool(cell.runtime.get_path("gKpi.xVerifiedClean"))
                        else "HELD",
                    )
                )
                exit_code = 0 if unit == 7 else 1
                break
    finally:
        http.shutdown()
        http.server_close()
        modbus.shutdown()
        modbus.server_close()
        print("servers stopped")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
