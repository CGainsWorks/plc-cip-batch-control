# The plant model

There is no skid. The controller is real ST; what it is connected to is
[src/plant/model.py](../src/plant/model.py) and the honest reading of every
number in this repository depends on knowing what that model does and does not
claim. The short version: the structure is first-principles and the
coefficients are plausible-but-fitted. Treat the figures as a demonstration
that the control strategy works against a defensible physical model, not as a
prediction of what a particular dairy would save.

## Equipment

| Item | Figure | Note |
| --- | --- | --- |
| Balance tank | 1200 L | the working tank the supply pump draws from |
| Circuit holdup | 450 L | the vessel and pipework being cleaned |
| Wetted area | 42 m² | what the soil sits on |
| Pipe bore | 60.3 mm (DN65) | 1.75 m/s at 18 m³/h |
| Supply pump | 24 m³/h at 100 %, 1.5 s lag | variable speed |
| Plate heat exchanger | 550 kW, 95 °C outlet limit | steam heated |
| Caustic store | 1500 L, 1200 L at 2.05 %, held at 68 °C | recovered and reused |
| Acid store | 1000 L, 800 L at 1.05 %, held at 45 °C | recovered and reused |
| Recovered water | 1500 L, 900 L at 38 °C | last cycle's final rinse |
| Mains water | 12 °C, 0.50 mS/cm | |
| Caustic concentrate | 25 % w/w NaOH, 1.27 kg/L, 30 L/min | positive displacement |
| Acid concentrate | 30 % w/w HNO₃, 1.18 kg/L, 20 L/min | positive displacement |

The three figures the controller also holds -- balance tank volume and the two
dosing rates -- are asserted equal in
[tests/test_io_map.py](../tests/test_io_map.py). A controller that thinks the
tank is a different size from the tank is a real commissioning bug.

## Hydraulics and the circuit

The circuit is **twenty stirred cells in series**. Each step, the supply parcel
enters cell 1, each full cell mixes what arrives and displaces an equal volume
into the next and the last cell feeds the return line:

```
m_cell' = m_cell + m_in                 (mix first)
f        = V_in / (V_cell + V_in)
m_out    = f * m_cell'                  (then take the outflow from the mixture)
m_cell   = m_cell' - m_out
```

Order matters. Taking the outflow from the pre-mix contents instead leaks mass
on every step, which showed up as a circuit that swallowed caustic and returned
clean water; `test_a_closed_loop_conserves_volume_and_chemical` exists because
of it.

Twenty cells gives a transport delay of V/Q -- 90 s at 18 m³/h -- with roughly
1/√20 ≈ 22 % dispersion, so a step change at the heat exchanger arrives at the
return element spread over about twenty seconds rather than as a square edge.
The adaptive rinse logic terminates on exactly that signal, which is why the
dispersion is modelled rather than assumed away.

Cells fill before they flow: at the start of a phase the circuit has to be
displaced and the return flow is zero until it is full. Below 6 % level the
supply pump cavitates and the flow goes to zero.

## Heat

The exchanger adds `duty = steam_% / 100 × 550 kW` to the supply parcel,
capped at 95 °C and the energy actually delivered is integrated as ground
truth. Losses are `UA × (T − T_ambient)`:

| Surface | UA |
| --- | --- |
| Balance tank | 0.14 kW/K |
| Chemical and water stores | 0.10 kW/K |
| Circuit | 1.30 kW/K |

The circuit term is the one that matters: at a 75 °C wash it costs about 3.5 K
between supply and return, which is why the recipe's 70 °C minimum is measured
on the return rather than the supply.

The controller computes its own energy figure from the instruments it has --
`m³/h × 1000/3600 × 4.19 × (T_supply − T_tank)` -- and
`scripts/run_experiments.py` records both. They agree to about 0.1 %, which is
the check that the KPI code is reading the plant rather than inventing numbers.

## Conductivity

```
sigma = 0.50 + 53·c_NaOH − 3·c_NaOH²  +  48·c_acid − 2·c_acid²  +  0.35·soil_g_per_L
```

in mS/cm with concentrations in % w/w, temperature compensated to 25 °C by the
transmitter. The quadratic terms are the mobility roll-off that makes a
single-point calibration wrong at the other end of the range: a 1 % caustic
solution reads 50 mS/cm and a 2 % solution 94, not 106.

The controller does **not** know that curve. It divides by a two-point
calibration constant -- 47 mS/cm per % for caustic, 45 for acid -- which is
exactly right at the calibration point and increasingly wrong away from it.
That mismatch between the instrument and the controller's model of the
instrument is deliberate; it is the normal state of affairs on a real skid.

Soil is a weak electrolyte here at 0.35 mS/cm per g/L, which is what makes the
pre-rinse endpoint possible: dissolved soil raises the return conductivity
above the incoming water and the rinse is finished when it stops doing so.

## Soil

Three pools with different chemistry, distributed evenly over the cells:

| Pool | Removed by | Reference rate | At |
| --- | --- | --- | --- |
| Loose | water at velocity | 0.0130 s⁻¹ | 1.5 m/s |
| Bound (protein, fat) | hot caustic | 0.0088 s⁻¹ | 2 % NaOH, 75 °C, 1.5 m/s |
| Mineral (scale) | acid | 0.0075 s⁻¹ | 1 % HNO₃, 60 °C, 1.5 m/s |

Each follows first-order kinetics with three multiplicative terms -- the usual
time, action, concentration, temperature decomposition, written out:

```
dm/dt = -k0 · exp(-Ea/R · (1/T - 1/T_ref)) · [c/(K+c)] / [c_ref/(K+c_ref)] · (v/v_ref)^0.6 · m
```

with `Ea` = 45 kJ/mol, `K` = 0.5 % and the velocity exponent 0.6. The
activation energy gives roughly a factor of two per 15 K, which is the rule of
thumb cleaning practice uses. Caustic also removes some scale and acid some
protein, at 8 % of their own rate, so the phases are not independent.

The scenarios in [params.py](../src/plant/params.py) span 28 g/m² (a short run
on a clean product) to 277 g/m² (the case the fixed recipe was sized against).

## What the controller cannot see

Two quantities are tracked by the model and measured by no instrument on the
skid:

* **residual soil**, g/m² left on the wall;
* **residual detergent**, mg/L left in the circuit.

They appear in every batch record under `independent_check` and they are there
to test the controller's release decision, never to inform it. The working
limits -- 1 g/m² and 50 mg/L -- are stated as assumptions in
[limitations.md](limitations.md), not as standards.

## Instruments

Gaussian noise, seeded per run so a rerun is byte-identical: 4 µS/cm on
conductivity, 0.05 m³/h on flow, 0.05 K on temperature, 0.05 % on level.
Contactor feedback makes and breaks 400 ms after the command, which is what the
pump feedback supervision in [FB_Pump.st](../plc/fb/FB_Pump.st) is there to
catch.

## Fault injection

[Faults](../src/plant/model.py) injects, at a chosen simulated time: a supply
or return pump trip, loss of steam, a derated heater, loss of the circuit
release, an emergency stop and a conductivity transmitter frozen at its
current reading. [tests/test_faults.py](../tests/test_faults.py) asserts what
the controller does about each and `scripts/run_cycle.py --fault` runs them
one at a time.

## Known simplifications

* Caustic and acid are tracked as independent species: the model does not
  neutralise one with the other. The drain-down at the end of each wash keeps
  the carryover to about 60 L of a 720 L charge, so a phase starts with under
  0.2 % of the other chemical but a model that followed pH properly would
  carry a salt term as well.
* Density is 1 kg/L everywhere, so % w/w and % w/v are the same thing here. At
  2 % caustic the error is about 2 %.
* Soil is removed, not re-deposited. Real CIP can redeposit protein from a
  spent caustic solution, which is part of why the wash is recovered to its own
  store rather than recirculated indefinitely.
* The heat exchanger is a fixed duty against valve position rather than a
  steam-side model with condensate dynamics.
* One circuit. A real skid serves several and has to schedule them.
