"""Shared fixtures.

`st_harness` builds a runtime out of the real ST library plus a small test
program, so a function block test exercises the same source that is downloaded
to the controller rather than a copy of it.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable, Optional, Sequence

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from src.iec61131.runtime import Runtime  # noqa: E402

PLC_DIR = os.path.join(REPO_ROOT, "plc")
TYPES = [
    os.path.join(PLC_DIR, "types", "Types_Cip.st"),
    os.path.join(PLC_DIR, "types", "GVL_Cip.st"),
    os.path.join(PLC_DIR, "types", "GVL_Io.st"),
]


def fb_path(name: str) -> str:
    return os.path.join(PLC_DIR, "fb", name)


def prog_path(name: str) -> str:
    return os.path.join(PLC_DIR, "prog", name)


class Harness:
    """A runtime holding one test program, with helpers for driving a scan."""

    def __init__(self, runtime: Runtime, scan_s: float = 0.1):
        self.rt = runtime
        self.scan_s = scan_s

    def scan(self, count: int = 1, scan_s: Optional[float] = None) -> None:
        step = self.scan_s if scan_s is None else scan_s
        for _ in range(count):
            self.rt.scan(step)

    def run_for(self, seconds: float, scan_s: Optional[float] = None) -> None:
        step = self.scan_s if scan_s is None else scan_s
        self.scan(int(round(seconds / step)), step)

    def get(self, path: str):
        return self.rt.get_path(path)

    def set(self, path: str, value) -> None:
        self.rt.set_path(path, value)


def build_harness(
    program_source: str,
    program_name: str,
    extra_files: Sequence[str] = (),
    include_types: bool = True,
    scan_s: float = 0.1,
) -> Harness:
    runtime = Runtime()
    files: Iterable[str] = (list(TYPES) if include_types else []) + list(extra_files)
    runtime.load_files(files)
    runtime.load_source(program_source, program_name + ".st")
    runtime.build([program_name])
    return Harness(runtime, scan_s)


@pytest.fixture
def harness_factory():
    return build_harness


def fast_recipe(base: str = "adaptive", **overrides):
    """A cut-down recipe so a sequencing test is seconds rather than a minute.

    Holds and temperatures are trimmed, not removed: the point is to exercise
    the phase order, the interlocks and the fault paths without simulating a
    forty-five minute heat-up every time. The consumption figures in results/
    come from the real recipes in config/recipes.json.
    """
    from src.harness.cell import load_recipes

    recipe = dict(load_recipes()[base])
    recipe.update(
        {
            "name": "test-fast",
            "pre_rinse_s": 60,
            "caustic_temp_sp_c": 45.0,
            "caustic_temp_min_c": 40.0,
            "caustic_hold_s": 60,
            "int_rinse_s": 60,
            "acid_temp_sp_c": 40.0,
            "acid_temp_min_c": 35.0,
            "acid_hold_s": 60,
            "final_rinse_s": 60,
            "sanitise_temp_sp_c": 50.0,
            "sanitise_temp_min_c": 45.0,
            "sanitise_hold_s": 60,
            "rinse_max_s": 420,
            "phase_max_s": 1200,
        }
    )
    recipe.update(overrides)
    return recipe
