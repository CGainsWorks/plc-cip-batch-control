# Alarms

Thirteen points, implemented in [PRG_Alarms.st](../plc/prog/PRG_Alarms.st) on
top of [FB_Alarm.st](../plc/fb/FB_Alarm.st).

## The state model

ISA-18.2's first rule is that every alarm must require a response. The three
flags each point carries are kept in `ST_Alarm`, so the whole list is one array
a SCADA client reads in a single request:

| Flag | Meaning |
| --- | --- |
| `xActive` | the process condition is true right now |
| `xLatched` | it has been raised and not yet cleared off the list |
| `xAcked` | the operator has acknowledged **this** occurrence |

A point returns to normal only when the condition has gone away **and** it has
been acknowledged. That is what stops a fleeting condition disappearing off the
list before anybody has seen it and it is why acknowledging an alarm that is
still active does not clear it. A second occurrence re-arms the
acknowledgement and increments the counter.

`giFirstOut` captures the first point to latch while the list was quiet and
holds it until the list is quiet again, so the record says which condition
started the trip rather than which one is loudest by the time anyone looks.

## The list

| # | Tag | Priority | On delay | Condition | Why that delay |
| --- | --- | --- | --- | --- | --- |
| 1 | `SAFETY_TRIP` | 1 | none | safety circuit unhealthy, low-low float, dry run, or circuit released while running | a trip is a trip |
| 2 | `SUPPLY_PUMP` | 1 | 5 s | motor protection, or commanded with no run feedback | contactor travel |
| 3 | `RETURN_PUMP` | 2 | 5 s | as above | contactor travel |
| 4 | `BALANCE_LEVEL_LOW` | 2 | 3 s | level below the trip while the unit is not idle | the level chatters around the fill setpoint |
| 5 | `CAUSTIC_LEVEL_LOW` | 3 | 10 s | caustic store below 15 % | a transfer in progress is not an alarm |
| 6 | `ACID_LEVEL_LOW` | 3 | 10 s | acid store below 15 % | as above |
| 7 | `TEMPERATURE_LOW` | 2 | 12 min | circulating, heat demanded, return below the recipe minimum | a cold circuit and a 1200 L tank take about eight minutes to come up; anything shorter alarms on every normal start |
| 8 | `FLOW_LOW` | 2 | 30 s | circulating below the recipe minimum flow | rides through a valve change |
| 9 | `CIRCUIT_UNAVAILABLE` | 2 | 3 s | release withdrawn while the unit is not idle | a momentary dip in a release signal is not worth annunciating |
| 10 | `PHASE_TIMEOUT` | 1 | none | phase, charge or temperature timeout | already a considered decision by the time it fires |
| 11 | `RINSE_NOT_CLEAN` | 1 | none | adaptive rinse hit its maximum without reaching the endpoint | the one that holds the batch |
| 12 | `STEAM_UNAVAILABLE` | 3 | 10 s | heat demanded with no steam header | boiler cycling is normal |
| 13 | `CONDUCTIVITY_SENSOR` | 1 | none | a wash finished without the return element ever reading like detergent | a latch, not a delay: see the control narrative |

## Priorities

* **1**: stops the batch, or means the skid is not safe to run. Five points.
  A priority 1 alarm also blocks a start until it clears.
* **2**: the batch is at risk and someone has to look now. Five points.
* **3**: a consumable or utility needs attention before the next batch. Three
  points.

Thirteen points for a unit this size is deliberately sparse. The common failure
of an alarm system is not too few points but too many: a list of two hundred
nuisance alarms is a list nobody reads.

## The horn

`xHmiAlarmHorn` is true while any latched alarm is unacknowledged and
`giActiveAlarms` counts the latched points. Both are in the memory image, so a
panel or a DCS can drive its own annunciator from them without knowing anything
about the alarm logic.

## What is not an alarm

Several conditions deliberately do not annunciate:

* A phase ending early because its conductivity endpoint was met. That is the
  system working.
* A dose valve cycling to hold strength in band.
* The level control valve chattering around the fill setpoint, which it does
  by design: it is a bang-bang loop on a break tank.
* A derated heater. The wash simply takes longer and still earns its verified
  hold; [tests/test_faults.py](../tests/test_faults.py) asserts that it
  completes rather than alarming.
