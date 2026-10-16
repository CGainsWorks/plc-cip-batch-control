# Limitations and what commissioning on real hardware would need

**There is no hardware.** The ST is real and it runs, on an interpreter
written for this repository and, compiled by RuSTy, as native code driven by a
harness written for this repository, in both cases against a model written for
this repository. That is worth being precise about, because the difference
between "this logic is sound" and "this logic is proven" is the whole of
commissioning.

## What is genuinely demonstrated

* The ST parses, resolves and executes as IEC 61131-3 Structured Text, with PLC
  semantics rather than Python ones: integers wrap at their declared width,
  integer division truncates towards zero, `MOD` takes the sign of the
  dividend, structures assign by value and the standard timers and counters
  behave as the standard says. The contract is
  [tests/test_st_language.py](../tests/test_st_language.py).
* An independent compiler accepts it. RuSTy 1.0.6 compiles all of `plc/` with
  no diagnostics and the compiled program passes the same end-to-end suite and
  agrees with the interpreter on eighteen scenarios, with the differences
  explained in [compiled_crosscheck.md](compiled_crosscheck.md).
* The sequence, the interlocks, the alarm state model and the release logic
  behave correctly against a plant that conserves mass and energy, has a real
  transport delay and fails in the ways plants fail.
* The I/O map, the Modbus map and the documentation all come from the same
  declarations and CI fails if they diverge.
* Every figure in the README is reproducible from the committed code with one
  command and CI re-runs one cell of the sweep on every push to prove the
  numbers still hold.

## What is not

### Neither execution is a PLC

`src/iec61131` implements the subset this project uses and no more. It has no
`SFC`, no `LD`, no `FBD`, no `ACTION`, no `METHOD`, no `INTERFACE`, no
`POINTER`, no `REF_TO`, no `ANY` generics, no `TIME_OF_DAY` or `DATE`, no
retained-variable semantics across a power cycle, no multi-task configuration
and no `VAR_EXTERNAL`. Its STRING handling is minimal. It holds REAL in double
precision where the standard says single; the compiled cross-check measured
what that changes. It has not been run against a conformance suite.

RuSTy is a real compiler. It is not a PLC runtime. The compiled program
runs under [src/harness/compiled.py](../src/harness/compiled.py), which
provides the I/O image, the task and a simulated clock. Real task scheduling,
jitter, I/O update timing, retentive memory and watchdogs are exercised by
neither execution.

The practical consequence: the ST is now compiled by an independent toolchain
but not by a vendor's. **Before anything is downloaded to a controller, the
whole of `plc/` has to be compiled by the target toolchain and the differences
dealt with.** The RuSTy build found the kinds of thing to expect: a TIME
conversion whose unit differs between compilers (now written to be
unit-independent), enumerations assigned to INT, a process image that does not
keep `%I` and `%Q` apart and a TON that is one scan later than the standard's
timing diagram at `PT = T#0s`. The `AT %...` convention itself also differs
between CODESYS-style indexing and Siemens-style byte offsets. A
[PLCopen XML export](plcopen.md) exists to start a vendor project from; it has
not been imported into CODESYS or TwinCAT.

MatIEC and the OpenPLC runtime, the route this page used to suggest, were not
attempted: MatIEC follows the standard's scoping, with globals in a
`CONFIGURATION` and `VAR_EXTERNAL` in every POU that uses them, while `plc/`
uses CODESYS-style global variable lists.

### Elapsed time is accumulated in REAL

The phase engine and the KPI program add the scan period to a REAL every scan.
In single precision that loses time: 25 additions of 0.2 make 4.9999995, so a
five-second step takes one scan too many. The cross-check measured up to 2.2 s
of drift over a full cycle. On a real controller elapsed time belongs in `TIME`
or a DINT scan count; changing it here would move every committed figure by a
scan or two and has been left as a known limitation.

### The model is first-principles in structure; its coefficients are not fitted

The structure of [model.py](../src/plant/model.py) is first-principles:
conservation of mass and energy, tanks in series, Arrhenius kinetics, a
Michaelis-type concentration term, a wall-shear term. The **coefficients** are
plausible figures chosen so that the model behaves the way CIP practice says
cleaning behaves: a 2 % caustic wash at 75 °C and 1.75 m/s cleans a normal
soil in about ten minutes, a cold water rinse removes loose soil and nothing
else, a 1 % acid wash removes scale. They are not fitted to measurements from a
particular plant and the removal rates in particular would need
re-identification against swab or riboflavin results on the real circuit.

So: the 23 % water saving is a property of this model. The *mechanism* (a
rinse that stops when the water is clean uses less water than a rinse sized for
the worst case) is general. The number is not.

### The release limits are assumptions, not standards

`RESIDUAL_LIMIT_G_M2 = 1.0` and `RESIDUAL_CHEMICAL_LIMIT_PPM = 50.0` are
working figures. Real cleaning validation is pass/fail against visually clean
plus an ATP or protein swab threshold and the rinse endpoint is usually
specified as a conductivity within some microsiemens of the feed water. The
numbers here are in the right territory and are stated so they can be
challenged but they are not drawn from 3-A, EHEDG or a site HACCP plan.

### The controller verifies the recipe, not the recipe's adequacy

This is the most important limitation and it is a real one, not an artefact of
simulation. The `fixed-short` recipe in the sweep completes with **every phase
passing its own criteria** and is still held, because its final rinse leaves
290 mg/L of detergent in the circuit and the conductivity check catches that.
But its caustic hold is also too short and nothing in the controller can know
that: it was asked for five minutes of verified hold at 2 % and 75 °C and it
delivered exactly that. On the heaviest soil it leaves 0.21 g/m² behind --
inside the limit used here but trending the wrong way and a further trim
would cross it with the controller still reporting every phase as passed.

What catches that in the real world is cleaning validation and what the
controller contributes is the batch record: the verified hold, minimum
temperature, mean concentration and minimum flow it actually achieved,
hash-chained, so a recipe change can be argued about with evidence. Automation
does not remove the validation step. It makes the validation argument
checkable.

### Not modelled at all

* **Neutralisation.** Caustic and acid are independent species; carryover does
  not neutralise. The drain-down keeps carryover under 0.2 % of a charge.
* **Effluent pH and the drain interceptor.** A real site needs both.
* **Scheduling.** One circuit, one unit. A real skid serves several circuits
  and the interesting problem becomes which to clean when.
* **Redeposition** of soil from a spent wash solution.
* **Steam-side dynamics**: the exchanger is duty against valve position.
* **A second conductivity element.** The release decision rests on one
  transmitter. The wash plausibility check catches the failure that matters
  but a real installation handling product contact would fit a second
  element and compare them.
* **Retentive memory and warm restart.** The controller has no notion of
  resuming a batch after a power cycle, which a real unit needs.
* **Change control.** A real installation needs version control on the recipe
  set with an approval trail. The recipes here are a JSON file.
* **Wall-clock time.** Batch records carry simulated time only, so a rerun
  reproduces them byte for byte. A real installation would stamp them from the
  controller clock and the chain would need a trusted time source.
* **Electronic signatures.** The batch records are hash-chained, not signed.
  The chain has no key: anyone who can rewrite the files can recompute it, so
  it shows tampering only against a latest hash kept somewhere else, and
  nothing in a record says who produced or approved it. Records in the 21 CFR Part 11 or EU Annex 11 sense
  would need a signing key per operator, held outside the controller and an
  approval step.
* **Security.** The Modbus server has no authentication, because Modbus TCP has
  none. On a real site it belongs on an isolated control network behind a
  firewall, not on an office LAN.

## If this were a real project

The order of work after the logic is written:

1. Compile the ST with the target vendor toolchain; fix what it rejects.
2. Simulate again on the vendor's own simulator, which exercises their runtime
   rather than this interpreter.
3. Loop checks and a point-to-point I/O test on the built skid.
4. Dry-run the sequence with water only, on every phase, with the fault
   injection list from [tests/test_faults.py](../tests/test_faults.py) applied
   by hand.
5. Identify the removal-rate coefficients against swab results on the real
   circuit; re-tune the loops against the real heat exchanger.
6. Cleaning validation of the final recipes.
7. Only then run the adaptive recipe in anger and only with the fixed recipe
   still available as a fallback.
