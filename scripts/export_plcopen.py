"""Write plc/ as PLCopen TC6 XML, check it, or turn it back into ST.

    python scripts/export_plcopen.py                  # write export/plcopen/cip_skid.xml
    python scripts/export_plcopen.py --check          # fail if the committed file is stale
    python scripts/export_plcopen.py --roundtrip DIR  # regenerate .st from the XML into DIR
                                                      # and load it in the interpreter

The file is deterministic: SOURCE_DATE_EPOCH (default 0) is the creation time,
so the same source always exports to the same bytes and CI can diff it.
Schema validation and the RuSTy compile of the round trip run in CI.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.export.plcopen import export, to_st  # noqa: E402
from src.harness.cell import PLC_DIR, PROGRAM_ORDER  # noqa: E402
from src.iec61131.runtime import load  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(REPO_ROOT, "export", "plcopen", "cip_skid.xml")


def creation_time() -> str:
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    stamp = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S")


def roundtrip(xml_text: str, directory: str) -> int:
    os.makedirs(directory, exist_ok=True)
    for name, text in to_st(xml_text).items():
        with open(os.path.join(directory, name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    original = load(PLC_DIR, PROGRAM_ORDER)
    rebuilt = load(directory, PROGRAM_ORDER)
    problems = []
    if sorted(original.globals) != sorted(rebuilt.globals):
        problems.append("the global variables differ")
    addresses = {k: str(v.address) for k, v in original.located.items()}
    if addresses != {k: str(v.address) for k, v in rebuilt.located.items()}:
        problems.append("the located addresses differ")
    for attribute in ("fb_pous", "functions", "program_pous", "types"):
        if sorted(getattr(original, attribute)) != sorted(getattr(rebuilt, attribute)):
            problems.append("the {0} differ".format(attribute))
    for problem in problems:
        print("round trip:", problem)
    if not problems:
        print(
            "round trip ok: {0} types, {1} function blocks, {2} functions, {3} programs, "
            "{4} globals, {5} located".format(
                len(rebuilt.types), len(rebuilt.fb_pous), len(rebuilt.functions),
                len(rebuilt.programs), len(rebuilt.globals), len(rebuilt.located),
            )
        )
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--roundtrip", metavar="DIR", default=None)
    args = parser.parse_args()

    text = export(PLC_DIR, PROGRAM_ORDER, creation=creation_time())
    if args.check:
        try:
            with open(args.out, "r", encoding="utf-8") as handle:
                committed = handle.read()
        except FileNotFoundError:
            committed = ""
        if committed != text:
            print("{0} is stale; run scripts/export_plcopen.py".format(
                os.path.relpath(args.out, REPO_ROOT)))
            return 1
        print("PLCopen export matches plc/")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print("wrote", os.path.relpath(args.out, REPO_ROOT))
    if args.roundtrip:
        return roundtrip(text, args.roundtrip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
