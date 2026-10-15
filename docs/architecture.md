# Architecture

Four things live in this repository and the boundaries between them are the
point:

```
plc/            IEC 61131-3 Structured Text -- the deliverable
src/iec61131/   an ST front end and scan-cycle interpreter
src/plant/      a first-principles model of the skid
src/harness/    the wiring: one PLC, one plant, one scan loop
src/services/   Modbus TCP and an operator page, fed from the PLC image
```

The controller cannot see the model. Everything it knows arrives through the
located variables declared in [plc/types/GVL_Io.st](../plc/types/GVL_Io.st)
and everything it does leaves the same way. Take the interpreter out, download
the same ST to a real PLC and the only thing that changes is what is on the
other side of the terminals.

## The scan

```
                    +-------------------------------------------+
   plant.sensors()  |  input image      %IX0.0 .. %ID12         |
   ---------------> |                                           |
                    |  PRG_Safety    interlocks and permissives |
                    |  PRG_Sequence  ISA-88 unit procedure      |
                    |  PRG_Control   the only writer of outputs |
                    |  PRG_Alarms    ISA-18.2 alarm list        |
                    |  PRG_Kpi       totals, record, SCADA image|
                    |                                           |
                    |  output image     %QX0.0 .. %MD19         |
                    +-------------------------------------------+
   plant.step(dt, outputs)  <-----------------------------------+
```

Task period is 200 ms, declared once as `C_CYCLE_S` in
[GVL_Cip.st](../plc/types/GVL_Cip.st) and read back by the harness, so the
timing constants in the ST and the integration step of the model cannot drift
apart.

Program order matters and is explicit:

1. **PRG_Safety** first, so every other program sees permits evaluated against
   this scan's inputs rather than the previous one's.
2. **PRG_Sequence** owns the state machine and the phase engine. It writes no
   outputs at all; it produces a demand.
3. **PRG_Control** is the only program that writes to the field. That is what
   makes "what could possibly open the steam valve?" a question with one
   answer.
4. **PRG_Alarms** and **PRG_Kpi** last, so both see the outputs that were
   actually written this scan.

## The ST front end

`src/iec61131` is a lexer, a recursive-descent parser and a tree-walking
interpreter for the subset of IEC 61131-3 Structured Text this project uses:

* `PROGRAM`, `FUNCTION_BLOCK`, `FUNCTION`, `TYPE` (enumerations, structures,
  aliases, arrays), `VAR_GLOBAL`, `VAR_INPUT`, `VAR_OUTPUT`, `VAR_IN_OUT`,
  `VAR CONSTANT`;
* `IF`, `CASE` with comma and range labels, `FOR`, `WHILE`, `REPEAT`, `EXIT`,
  `RETURN`;
* the standard blocks `TON`, `TOF`, `TP`, `R_TRIG`, `F_TRIG`, `CTU`, `CTD`,
  `SR`, `RS` and the usual function library;
* located variables with `AT %IX0.0` style addresses;
* PLC semantics rather than Python ones: integers wrap at their declared width,
  `/` on two integers truncates towards zero, `MOD` takes the sign of the
  dividend, `REAL` to integer conversion rounds to nearest, structures assign
  by value.

Timers read a simulated clock that only advances because a scan says so, so a
run is reproducible and a timer behaves the same at a 20 ms scan as at 200 ms.
[tests/test_st_language.py](../tests/test_st_language.py) is the contract.

Three things in `runtime.py` exist for speed rather than clarity: each POU
instance gets one flattened scope dictionary, structure assignment copies into
the existing instance rather than replacing it and element references are
small `__slots__` objects rather than closures. Together they roughly halved
the scan cost. Compiling the tree to Python closures once per instance would be
faster again and is the obvious next step.

**This is not a certified compiler.** See [limitations.md](limitations.md).

## The I/O map

The `AT` clauses in the ST are the single source of truth for the address map.
`src/harness/io_map.py` reads them back out of the compiled program and derives
the Modbus tables; `scripts/render_io_map.py` renders
[io_map.md](io_map.md) from the same source and CI fails if the document and
the ST disagree. Adding a coil is one line of ST.

Only the `%M` area is writable by a client. Writing a `%Q` point would be
overwritten by the next scan, so the server returns an illegal data address
rather than pretending.

## The plant

`src/plant` is where the physics is. The circuit is twenty stirred cells in
series, which reproduces both the transport delay and the dispersion of a real
pipe run; soil is three pools with different chemistry; conductivity comes from
concentration through a curve the controller's straight-line calibration only
approximates, which is the point. See [model.md](model.md).

## Threads

The scan loop is single threaded. The Modbus and HTTP servers run on their own
threads and never touch the interpreter: they read a snapshot dictionary the
scan loop refreshes between scans and writes go into a queue the scan loop
drains before the next scan. A client therefore cannot observe a half-executed
scan and the interpreter needs no locks.
