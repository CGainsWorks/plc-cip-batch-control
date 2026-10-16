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


# The cut-down recipe lives with the harness so scripts/crosscheck_compiled.py
# can run the same scenarios the tests do.
from src.harness.cell import fast_recipe  # noqa: E402, F401
