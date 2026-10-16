# Control narrative

The document a process engineer signs off before anyone writes code. It says
what the skid does, under what conditions it refuses and what evidence it
produces. The ST in [plc/](../plc) implements exactly this and nothing else.

## 1. Scope

Unit **CIP-01** cleans one circuit at a time (**TANK-CIRCUIT-A**: a vessel plus
its associated pipework, 450 L holdup, 42 m² wetted surface). The skid has a
1200 L balance tank, a variable-speed supply pump, a steam-heated plate heat
exchanger, a return pump, stores of recovered caustic and acid, a recovered
water tank and a valve matrix.

## 2. Unit states

The unit follows the ISA-88 procedural state model, implemented in
[FB_UnitStateMachine.st](../plc/fb/FB_UnitStateMachine.st):

```
IDLE -> STARTING -> RUNNING -> COMPLETING -> COMPLETE -> IDLE
                      |  ^
                  HOLDING |
                      |  RESTARTING
                      v  |
                    HELD +

any active state -> ABORTING -> ABORTED -> IDLE
```

* Commands are edge triggered and cleared by the controller once taken, so a
  SCADA client that sets a coil and forgets it still produces a single pulse.
* **START** is refused unless the safety circuit is healthy, the circuit has
  been released for cleaning and no priority 1 alarm is active.
* **HOLD** freezes the phase. The line-up is kept so the circuit stays full
  but circulation, heating and dosing stop and neither the elapsed time nor
  the verified hold time advances. A held phase earns no credit.
* **ABORT**, a safety trip, or a failed phase all drive the unit through
  ABORTING, which runs for five seconds so the control layer can shut the steam
  valve and stop the pumps in order before the unit is declared ABORTED.
* Leaving ABORTED requires a reset **and** the absence of the trip.

## 3. The procedure

Phases run in a fixed order; the recipe selects which are in it.

| # | Phase | Source | Returns to | Ends when |
| --- | --- | --- | --- | --- |
| 1 | Pre-rinse | recovered water, mains if the recovery tank is low | drain | fixed time, or return conductivity within limit |
| 2 | Caustic wash | caustic store, trimmed with concentrate | balance tank, then caustic store | verified hold time reached |
| 3 | Intermediate rinse | mains | drain | fixed time, or conductivity |
| 4 | Acid wash (optional) | acid store, trimmed with concentrate | balance tank, then acid store | verified hold time reached |
| 5 | Final rinse | mains | recovered water tank | fixed time, or conductivity |
| 6 | Sanitise (optional) | mains, heated | balance tank | verified hold time reached |

## 4. What every phase does

One engine, [FB_CipPhase.st](../plc/fb/FB_CipPhase.st), runs all of them:

1. **LINE-UP**: set the valve matrix, start the return pump, five seconds to
   let the valves travel.
2. **CHARGE**: fill the balance tank from the phase's source. A recirculating
   phase fills to 60 % and closes the loop through the balance tank while it
   trims the strength with concentrate; a single-pass phase runs at 13 %, the
   lowest level the suction will stand, because the rinse has to wash out the
   tank as well as the circuit and a small tank washes out quickly. The supply
   pump starts as soon as there is suction head, which is also what satisfies
   the no-dosing-without-flow interlock.
3. **CIRCULATE**: hold the wash at temperature, flow and strength, or rinse to
   the conductivity endpoint.
4. **RECOVER**: push the contents back to the source store, or to drain,
   emptying the balance tank down to the pump start level so the next phase's
   charge is not diluted by the last one's chemical.

## 5. Verified hold time

The hold timer advances **only** while every criterion is met at once:

* return temperature at or above the recipe minimum (for a heated phase),
* supply flow at or above the recipe minimum,
* strength inside the recipe band (for a dosed phase).

Ten minutes of verified hold therefore means ten minutes of cleaning that met
the specification, not ten minutes on the clock. What goes in the batch
record is that accumulated figure, together with the minimum temperature,
mean concentration and minimum flow behind it. Time, action, concentration and temperature: the
four things a cleaning validation asks about, each measured rather than
assumed.

## 6. Rinse endpoints

A fixed rinse runs for its recipe time. An adaptive rinse stops when the return
conductivity has been within its limit of the incoming water for ten
continuous seconds, subject to two guards:

* **A blanking time.** The return element sits downstream of the whole circuit,
  so a rinse that stops after twenty seconds has only measured the water that
  was already in the pipe. The minimum is one and a half circuit turnovers at
  the recipe flow (135 seconds for this circuit at 18 m³/h), computed by the
  controller from the circuit volume rather than typed into the recipe.
* **A maximum time.** If the endpoint is not reached, the phase fails with
  `RINSE_NOT_CLEAN` rather than running on, so a conductivity element stuck at
  a dirty reading holds the batch.

Endpoints are set per phase: three times the release limit for the pre-rinse,
twice for the intermediate rinse and the release limit itself for the final
rinse.

### The element stuck at a clean reading

Neither guard catches the dangerous failure. An element frozen at something
clean satisfies its endpoint the instant the blanking time expires and a
dirty circuit is released. The guard against it is a plausibility check and
it exists because a fault test caught the hole in the version without one.

There is exactly one point in the cycle where the process guarantees a large
signal: circulating a wash at strength, the return line is full of detergent.
So the controller latches whether the return element ever read at least half
the expected value during a wash and a wash that finishes without that latch
set declares `FC_SENSOR_FAULT` and aborts the batch. Written as a latch rather
than a timer, it holds for any hold time: a two minute wash proves the element
as well as a twenty minute one. Both washes precede both rinses, so the check
always runs before an endpoint is trusted.

## 7. Interlocks and permissives

Evaluated in [PRG_Safety.st](../plc/prog/PRG_Safety.st), which runs first in
the task so every other program sees this scan's permits.

| Permit | Requires |
| --- | --- |
| `gxPermitRun` | safety circuit healthy, low-low float switch clear, no dry-run trip, circuit released, balance tank above the trip level |
| `gxPermitHeat` | permit to run, steam available, supply pump running, flow at or above 8 m³/h |
| `gxPermitDose` | permit to run, supply pump running, stock in the tank being drawn from |

Two of these earn their place:

* **No steam without proven flow.** A steam-heated plate pack with no flow
  boils the film against the plate, bakes the soil on and eventually takes the
  gaskets with it.
* **No dosing without a running pump.** Concentrate must not sit undiluted in
  the balance tank.

The dry-run detector stops the supply pump after twenty seconds of running with
less than 2 m³/h. A pump moving nothing is either dry or dead-headed and
either destroys the seal in minutes.

None of this replaces the hard-wired safety circuit. The emergency stop drops
the contactors and the steam solenoid directly; `xSafetyOk` is only the
controller's view of that circuit, used to sequence a clean shutdown.

## 8. Loops

| Loop | PV | MV | Tuning | Why |
| --- | --- | --- | --- | --- |
| Flow | supply flow | pump speed | Kp 3 %/(m³/h), Ti 8 s | a VSD against a fixed circuit resistance; there is nothing to gain from being quick |
| Temperature | **return** temperature | steam valve | Kp 5 %/K, Ti 600 s | lambda tuning for an integrator with 90 s of dead time |
| Supply limit | supply temperature | steam valve | Kp 25 %/K, proportional only | high-limit override, low selected against the temperature loop |

The temperature loop controls the **return**, not the supply. The recipe
specifies a temperature at the soil and the coldest point of the circuit is
the one that has to reach it. Controlling the supply instead throttles the
steam as soon as one pass through the plate pack looks hot enough and the
balance tank then takes three times as long to come up. The override is
proportional only on purpose: an integrator on the losing half of a selector
winds up.

## 9. Release

A batch is released only if:

* every phase that ran met its own criteria and
* the final rinse reached the conductivity endpoint.

Otherwise the batch is **HELD** and the record says why. The controller
verifies conformance to the recipe; it cannot tell that a recipe is
inadequate. The `fixed-short` recipe in the sweep completes with every phase
passing and is still held, because the rinse it specifies leaves detergent in
the circuit. See [limitations.md](limitations.md).

## 10. Alarms

Thirteen points, ISA-18.2 state model, first-out captured and held until the list
goes quiet. See [alarms.md](alarms.md).
