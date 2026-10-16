# Status

Last updated 2026-10-16.

Everything below runs on a normal PC and in GitHub Actions. There is no
hardware: the skid, its instruments and its field bus are simulated by the
plant model in `src/plant`, the PLC is the ST interpreter in `src/iec61131` or
the same ST compiled by RuSTy; SCADA is the Modbus TCP server and operator
page in `src/services`. Every figure produced by them is simulated.

## Complete

* **Controller.** About 2000 lines of IEC 61131-3 Structured Text in `plc/`:
  ISA-88 unit procedure and phase engine, ISA-18.2 alarm list with first-out,
  interlocks, flow and temperature loops with a high-limit override, totals and
  the batch record.
* **Interpreter.** `src/iec61131` executes `plc/` with PLC integer semantics and
  a simulated clock (`tests/test_st_language.py` is its contract).
* **Independent compiler.** `plc/` compiles with no diagnostics under RuSTy
  1.0.6 (PLC-lang, LLVM-based, pinned release). The compiled program runs
  against the same plant model with RuSTy's standard library built with its
  `mock_time` clock, passes the whole end-to-end suite and agrees with the
  interpreter on 18 scenarios: identical transitions, verdicts, alarms and
  release decisions, timing drift at most 7.2 s (cut-down recipe) and 2.2 s
  (real recipes). Details and the differences found:
  [docs/compiled_crosscheck.md](docs/compiled_crosscheck.md).
* **Source fixes from the compile.** TIME conversions made unit-independent
  (`plc/fn/Fn_Time.st`), enumerations published through `TO_INT`,
  `STRING[24]` and input and output addresses made disjoint because RuSTy
  keeps `%I` and `%Q` in one process image.
* **PLCopen XML.** `export/plcopen/cip_skid.xml` (TC6 v2.01), generated from
  `plc/`, validated against the schema, read back into ST that loads in the
  interpreter and compiles clean under RuSTy ([docs/plcopen.md](docs/plcopen.md)).
* **Plant model**, Modbus TCP server, operator page, I/O map derived from the
  `AT` declarations, twelve-cell experiment sweep reproduced in CI.
* **Batch records** are hash-chained and described as tamper-evident, not
  signed, everywhere (`record_hash`, `previous_hash`, schema
  `cip-batch-record/2`).
* **Tests.** 150: 121 fast tests on the interpreter, 5 fast tests that need the
  RuSTy build, 23 end-to-end cycles with fault injection and one interpreter
  against compiled cycle. CI runs the fast and end-to-end tests on the
  interpreter and, in a second job, the five compiled tests plus all 24
  end-to-end tests against the compiled program.
* **CI.** Two parallel jobs (interpreter checks; RuSTy compile and
  cross-check) and a Python 3.9 and 3.13 compatibility matrix, pinned actions,
  cached RuSTy toolchain, timeouts and cancellation of superseded runs.

## In progress

Nothing.

## Not started

* Import of the PLCopen XML into CODESYS or TwinCAT and a compile with either
  vendor toolchain. Neither has been run.
* A run under a soft-PLC runtime (CODESYS Control, OpenPLC). MatIEC would first
  need the global lists restructured into a `CONFIGURATION` with
  `VAR_EXTERNAL` in every POU.
* Electronic signatures on the batch records (a key per operator, held outside
  the controller, plus an approval step).
* OPC UA.
* Counting elapsed time in `TIME` or a DINT scan count instead of accumulating
  a REAL (see Limitations).

## Limitations

* **No hardware.** Nothing has run on a PLC, a skid or real instruments. Loop
  checks, I/O tests, water trials and cleaning validation are all still to do
  ([docs/limitations.md](docs/limitations.md) lists the order).
* **Neither execution is a PLC runtime.** The RuSTy build is real compiled
  code but the task, the I/O image and the clock are provided by a Python
  harness; scheduling, jitter, I/O timing, retentive memory and watchdogs are
  not exercised.
* **Known compiled differences**, all explained in
  [docs/compiled_crosscheck.md](docs/compiled_crosscheck.md): RuSTy's TON is
  one scan later at `PT = T#0s` (which changes first-out in one scenario);
  REAL is single precision compiled and double in the interpreter; an adaptive
  rinse endpoint turns small timing shifts into a few seconds.
* **Elapsed time accumulated in REAL** loses a scan at some step boundaries in
  single precision. Understood and bounded; not changed, so the committed
  figures stand.
* **Model coefficients are plausible, not fitted**, so the 23 % water saving is
  a property of the model.
* **Batch records are not signed** and carry simulated time only.
* **The RuSTy build is Linux only** (an ELF shared object); on Windows the five
  compiled tests skip.
