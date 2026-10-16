# The same ST, compiled by RuSTy

Until this check, the only thing that had ever executed `plc/` was the
interpreter in `src/iec61131`, written for this repository. That leaves an
obvious question: does the controller behave the way it does because the ST
is right, or because the interpreter agrees with its author? This page is the
answer to the extent a simulation can give one. Everything here is
**simulated**: no PLC, no vendor runtime and no hardware is involved.

## What runs

* **Compiler.** [RuSTy](https://github.com/PLC-lang/rusty) 1.0.6, the PLC-lang
  project's IEC 61131-3 Structured Text compiler, from its release packages
  (`plc-compiler_1.0.6-1_amd64.deb`). It compiles all of `plc/` through LLVM
  to an x86-64 shared object. Nothing in `plc/` is conditional on the
  compiler; the same files feed both executions.
* **Standard library.** RuSTy's `iec61131std` 1.0.6, built from the tagged
  source with its own `mock_time` feature
  ([scripts/rusty/build_stdlib.sh](../scripts/rusty/build_stdlib.sh)). The
  released library times TON, TOF and TP with the monotonic wall clock;
  `mock_time` swaps that for a clock the caller advances, which is what makes a
  run deterministic and lets an hour of plant run in seconds. One build change:
  upstream's `build.rs` links the whole compiler in as a library only to
  compile the standard library's ST half; here it calls the released `plc`
  binary of the same version with the same arguments instead.
* **Runtime.** [src/harness/compiled.py](../src/harness/compiled.py) plays the
  part a soft-PLC runtime plays: it owns the I/O image, the task and the clock.
  Each 200 ms scan it writes the plant's sensor values into the located
  variables, advances the library clock by 200 ms, calls `PRG_Safety`,
  `PRG_Sequence`, `PRG_Control`, `PRG_Alarms` and `PRG_Kpi` in order and reads
  the outputs back. The memory layout comes from the LLVM IR RuSTy emitted and
  the addresses from the hardware map it wrote; loading fails if either
  disagrees with the declarations.
* **Everything else is shared.** `CipCell(backend="rusty")`, or
  `CIP_BACKEND=rusty` for anything built on it, puts the compiled program
  behind the same plant model, I/O map, Modbus TCP server, operator page and
  batch-record code the interpreter uses.

## What CI checks

The `rusty` job in [ci.yml](../.github/workflows/ci.yml), on every push:

1. `plc/` compiles with **no diagnostics at all**, warnings included.
2. The ST read back from the [PLCopen export](plcopen.md) compiles the same
   way.
3. The compiled program loads with the declared layout, its initial values come
   from the compiler's own constructors, a REAL is single precision and its
   timers follow the scan clock (`pytest -m compiled`).
4. **The whole end-to-end suite runs against the compiled program**: 24 tests,
   among them the sequencing tests, every fault injection case and the Modbus
   and HMI tests that drive a cycle over a real socket.
5. [scripts/crosscheck_compiled.py](../scripts/crosscheck_compiled.py) runs
   eighteen scenarios on both executions and compares them.
6. The Modbus server and operator page come up in front of the compiled
   program, run a cycle and shut down.

## The cross-check

Each scenario runs once on the interpreter and once on the compiled program,
with the same plant, the same seeded noise and the same operator commands at
the same simulated times. Required to be **identical**: the sequence of
(unit state, phase, step) transitions, the phases in the batch record with
their verdicts, fail codes and endpoint types, every alarm raised with its
priority, count and latch state, the end state, the fail code and the release
decision. Allowed to **drift**, within 10 s or 3 %: when each transition
happened, phase durations and the consumption totals.

All eighteen agree. The committed comparison is
[results/crosscheck/rusty_vs_interpreter.json](../results/crosscheck/rusty_vs_interpreter.json),
from a local Linux container run. CI repeats it on every push and keeps its
own copy as an artifact; the first CI run produced an identical file.

| Scenario | Outcome | Transitions | Largest shift | Water, compiled minus interpreter |
| --- | --- | --- | --- | --- |
| fast-nominal | COMPLETE, RELEASED | 34 | 6.8 s | +28.1 L |
| fast-no-acid | COMPLETE, RELEASED | 28 | 1.0 s | +1.2 L |
| fast-with-sanitise | COMPLETE, RELEASED | 40 | 7.2 s | +30.5 L |
| fast-heavy-soil | COMPLETE, RELEASED | 34 | 1.4 s | -5.0 L |
| supply-pump-trip | ABORTED PUMP_FAULT, HELD | 14 | 0.2 s | -1.2 L |
| return-pump-trip | ABORTED CHARGE_TIMEOUT, HELD | 25 | 0.6 s | 0.0 L |
| emergency-stop | ABORTED SAFETY_TRIP, HELD | 13 | 0.2 s | -1.2 L |
| circuit-lost | ABORTED SAFETY_TRIP, HELD | 13 | 0.2 s | -1.2 L |
| steam-lost | ABORTED TEMP_TIMEOUT, HELD | 14 | 0.4 s | -1.2 L |
| heater-derated | COMPLETE, RELEASED | 34 | 2.0 s | +0.1 L |
| conductivity-stuck-clean | ABORTED SENSOR_FAULT, HELD | 16 | 0.2 s | -1.2 L |
| conductivity-stuck-dirty | ABORTED RINSE_NOT_CLEAN, HELD | 20 | 1.0 s | -4.9 L |
| hold-and-restart | COMPLETE, RELEASED | 38 | 1.0 s | -2.5 L |
| operator-abort | ABORTED OPERATOR_ABORT, HELD | 14 | 0.2 s | -1.2 L |
| abort-reset-rerun | COMPLETE, RELEASED | 48 | 4.6 s | +13.4 L |
| adaptive-medium | COMPLETE, RELEASED | 34 | 0.8 s | -4.8 L |
| fixed-conservative-medium | COMPLETE, RELEASED | 34 | 2.2 s | +2.4 L |
| fixed-short-medium | COMPLETE, HELD | 34 | 1.8 s | -1.2 L |

The `fast-*` scenarios use the cut-down test recipe; the last three are the
real recipes from `config/recipes.json`.

## What it took to compile

RuSTy rejected the source on two counts and warned on two more. Each was fixed
in the source in a form that is still standard ST, not by special-casing the
compiler:

| RuSTy said | Cause | Fix |
| --- | --- | --- |
| `Could not resolve reference to TIME_TO_REAL` (and `REAL_TO_TIME`) | Not in RuSTy's library. The unit of a TIME to number conversion is implementation-defined anyway: milliseconds in CODESYS, nanoseconds in RuSTy. `TIME_TO_REAL(t) / 1000.0` meant seconds on one and microseconds on the other. | [plc/fn/Fn_Time.st](../plc/fn/Fn_Time.st): `TimeToSeconds` and `SecondsToTime` divide by the conversion of `T#1s`, which cancels the unit. |
| `Implicit downcast from 'E_UnitState' to 'INT'` (four places; also `E_Phase` used as an array index) | An enumeration assigned straight into an INT. | `TO_INT(...)`. |
| `Unusual type of parentheses around string size expression` | `STRING(24)` | `STRING[24]`, the standard's form. |

## Behavioural differences found

### 1. Inputs and outputs shared storage (fixed in the address map)

The first test of the compiled program saw the safety relay input fall to
FALSE one scan after it was set, with nothing in the ST writing it. RuSTy backs every located variable with a global named
from the address *numbers* only, `__PI_<byte>_<bit>` for both `%I` and `%Q`
(`mangle_hw_name` in its `plc_ast` crate, unchanged on its master branch at
the time of writing). So `xSafetyOk AT %IX0.0` and `xV01FreshWater AT %QX0.0`
were the same byte: `PRG_Control` writing the valve wrote the relay input, and
every program after it in the scan read outputs where it expected inputs.
`%ID0` and `%QD0` had the same problem.

IEC 61131-3 keeps the input and output areas separate and the interpreter
does, which is why nothing had noticed. The fix is in
[GVL_Io.st](../plc/types/GVL_Io.st): outputs now start at byte 2 and double
word 16, clear of every input. That is valid on any toolchain and costs
nothing. A test keeps the numbers disjoint and the compiled loader refuses to
run if two located variables ever share storage again. The Modbus offsets of
the outputs moved with them (coils 16 to 30, holding registers 32 to 35); see
[io_map.md](io_map.md).

### 2. A TON is never true on the scan its input rises (documented)

RuSTy's TON starts timing on the rising edge and returns `Q = FALSE` on that
scan whatever `PT` is; `Q` can only go true on a later scan. With
`PT = T#0s` that is one scan of delay where the standard's timing diagram, the
interpreter and CODESYS all give `Q = IN` at once. TOF has the mirror image
with `T#0s`: `Q` stays true one scan after the input falls.

In this project that touches the alarms with no on-delay. They latch, and
clear, one scan (200 ms) later than in the interpreter. Visible in one place:
losing the circuit release raises two alarms on the same condition. In the
interpreter both latch on the same scan and first-out is the lower index,
`SAFETY_TRIP`; compiled, `CIRCUIT_UNAVAILABLE` latches a scan earlier and is
first-out. The cross-check lists this as a known difference with that reason.
Which of the two is "first" for a single root cause is a judgement, not a
fault. It is left as it is.

### 3. REAL is single precision when compiled (documented, not fixed)

IEC 61131-3 says a REAL is a 32-bit float. The compiled program uses one; the
interpreter holds every REAL as a Python float, which is 64-bit. The
interpreter's documentation said it implements PLC semantics and in this
respect it does not.

Where it shows: the phase engine and the KPI program measure elapsed time by
adding the scan period, 0.2, to a REAL every scan. In single precision 25
additions of 0.2 make 4.9999995, not 5.0, so the five-second LINEUP step
takes one scan longer compiled than interpreted. Likewise 675 additions make
134.999 against the 135 s rinse blanking time. Each phase boundary can move by
a scan. Over a full cycle the transitions drifted by at most 2.2 s on the real
recipes.

On a real controller the fix would be to count elapsed time in `TIME` or as a
DINT number of scans rather than accumulating a REAL. It has not been made
here: it would change every committed figure by a scan or two for no change in
behaviour; the drift is understood and bounded.

### 4. An adaptive endpoint amplifies small shifts (documented)

The largest shifts, 6.8 s and 7.2 s, are both the final rinse of the cut-down
recipe. An adaptive rinse ends when the return conductivity has stayed below
its endpoint for ten seconds. Near the endpoint the measured value crosses
the threshold back and forth on the transmitter noise. Arriving at that stretch
a few hundred milliseconds later (differences 2 and 3) lands on different noise
samples, so the ten quiet seconds can come several seconds later or earlier.
It is the same rinse ending on the same criterion with the same verdict; on
the real recipes the endpoint moved by under a second. The water figure
follows the rinse length, which is the 28 to 31 L in the table.

## Speed

On one local run the compiled program completed each scenario about ten times
faster than the interpreter (the plant model, which is Python in both cases,
is then most of the time). This is a property of the harness, not a claim
about scan time on any controller.

## What this does and does not show

It shows that an independent, open-source IEC 61131-3 compiler accepts the
source without a diagnostic and that the code it generates behaves like the
interpreter on every scenario the test suite and the cross-check exercise,
with the differences above explained.

It does not show behaviour on a PLC or under a vendor runtime. The runtime
here is a Python harness with a simulated clock; real task scheduling, jitter,
I/O update timing, retentive memory and watchdogs are not exercised. CODESYS,
TwinCAT and the OpenPLC runtime have not been run. MatIEC, the compiler behind
OpenPLC, was not attempted: it follows the standard's scoping, where globals
belong to a `CONFIGURATION` and each POU names the ones it uses in
`VAR_EXTERNAL`, while `plc/` uses CODESYS-style global variable lists, so a
MatIEC build would need that restructuring first.
