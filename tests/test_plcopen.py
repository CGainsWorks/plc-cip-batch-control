"""The PLCopen XML export: current, complete and readable back."""

from __future__ import annotations

import os

from conftest import REPO_ROOT

from src.export.plcopen import blank_comments, export, read_source, to_st
from src.harness.cell import PLC_DIR, PROGRAM_ORDER
from src.iec61131.runtime import load

COMMITTED = os.path.join(REPO_ROOT, "export", "plcopen", "cip_skid.xml")


def test_the_committed_export_matches_the_source():
    with open(COMMITTED, "r", encoding="utf-8") as handle:
        committed = handle.read()
    assert committed == export(PLC_DIR, PROGRAM_ORDER), "run scripts/export_plcopen.py"


def test_every_pou_and_global_is_exported():
    source = read_source(PLC_DIR)
    runtime = load(PLC_DIR, PROGRAM_ORDER)
    names = {p.name.upper() for p in source.pous}
    assert names == set(runtime.fb_pous) | set(runtime.functions) | set(runtime.program_pous)
    exported = {d.name.upper() for _n, vl in source.globals for d in vl.decls}
    assert exported == set(runtime.globals)


def test_the_export_reads_back_into_st_that_loads(tmp_path):
    for name, text in to_st(export(PLC_DIR, PROGRAM_ORDER)).items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    original = load(PLC_DIR, PROGRAM_ORDER)
    rebuilt = load(str(tmp_path), PROGRAM_ORDER)
    assert sorted(rebuilt.globals) == sorted(original.globals)
    assert {k: str(v.address) for k, v in rebuilt.located.items()} == {
        k: str(v.address) for k, v in original.located.items()
    }
    # One scan of each, from the same initial state, lands on the same image.
    original.scan(0.2)
    rebuilt.scan(0.2)
    for path in ("grPlcTimeS", "gxPermitRun", "gUnitState", "iHmiUnitState"):
        assert rebuilt.get_path(path) == original.get_path(path)


def test_comments_are_blanked_without_moving_anything():
    text = "a := 1; (* x (* nested *) y *) b := 2; // tail\nc := 3;"
    blanked = blank_comments(text)
    assert len(blanked) == len(text)
    assert "nested" not in blanked and "tail" not in blanked
    assert blanked.index("c := 3") == text.index("c := 3")
