"""First-principles model of the CIP skid.

The circuit is twenty stirred cells in series, which reproduces both the
transport delay and the dispersion of a real pipe run: a step change in supply
conductivity arrives at the return sensor spread over roughly a quarter of the
holdup time, not as a square edge. That matters, because the adaptive rinse
logic terminates on exactly that signal.

Soil is three pools with different chemistry:

    loose     removed mechanically by any water at velocity
    bound     protein and fat, needs hot caustic
    mineral   scale, needs acid

Each pool follows first-order kinetics with an Arrhenius temperature term, a
Michaelis-type concentration term and a wall-shear term in velocity: the
usual time, action, concentration, temperature decomposition, written out.

Nothing in this module is visible to the PLC except through `sensors()`.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional

from .params import DEFAULT_PARAMS, SCENARIOS, PlantParams, SoilScenario

R_GAS = 8.314  # J/(mol K)


@dataclass
class Parcel:
    """A slug of liquid moving through the plant."""

    volume_l: float = 0.0
    temp_c: float = 20.0
    naoh_kg: float = 0.0
    acid_kg: float = 0.0
    soil_kg: float = 0.0

    def scaled(self, volume_l: float) -> "Parcel":
        if self.volume_l <= 1e-12:
            return Parcel(volume_l, self.temp_c, 0.0, 0.0, 0.0)
        f = volume_l / self.volume_l
        return Parcel(
            volume_l,
            self.temp_c,
            self.naoh_kg * f,
            self.acid_kg * f,
            self.soil_kg * f,
        )


class Vessel:
    """A well-mixed tank."""

    __slots__ = ("capacity_l", "volume_l", "temp_c", "naoh_kg", "acid_kg", "soil_kg")

    def __init__(
        self,
        capacity_l: float,
        volume_l: float = 0.0,
        temp_c: float = 20.0,
        naoh_kg: float = 0.0,
        acid_kg: float = 0.0,
        soil_kg: float = 0.0,
    ):
        self.capacity_l = capacity_l
        self.volume_l = volume_l
        self.temp_c = temp_c
        self.naoh_kg = naoh_kg
        self.acid_kg = acid_kg
        self.soil_kg = soil_kg

    @property
    def level_pct(self) -> float:
        return 100.0 * self.volume_l / self.capacity_l

    def add(self, parcel: Parcel) -> None:
        if parcel.volume_l <= 0.0:
            return
        total = self.volume_l + parcel.volume_l
        if total > 0.0:
            self.temp_c = (self.temp_c * self.volume_l + parcel.temp_c * parcel.volume_l) / total
        self.volume_l = total
        self.naoh_kg += parcel.naoh_kg
        self.acid_kg += parcel.acid_kg
        self.soil_kg += parcel.soil_kg

    def draw(self, volume_l: float) -> Parcel:
        volume_l = min(volume_l, self.volume_l)
        if volume_l <= 0.0 or self.volume_l <= 1e-9:
            return Parcel(0.0, self.temp_c)
        f = volume_l / self.volume_l
        parcel = Parcel(
            volume_l,
            self.temp_c,
            self.naoh_kg * f,
            self.acid_kg * f,
            self.soil_kg * f,
        )
        self.volume_l -= volume_l
        self.naoh_kg -= parcel.naoh_kg
        self.acid_kg -= parcel.acid_kg
        self.soil_kg -= parcel.soil_kg
        return parcel

    def lose_heat(self, ua_kw_k: float, ambient_c: float, dt: float, cp: float) -> None:
        if self.volume_l <= 1e-6:
            return
        heat_capacity_kj_k = self.volume_l * cp
        self.temp_c -= ua_kw_k * (self.temp_c - ambient_c) * dt / heat_capacity_kj_k


class Cell:
    """One stirred cell of the circuit, with its own share of the soil."""

    __slots__ = (
        "capacity_l",
        "volume_l",
        "temp_c",
        "naoh_kg",
        "acid_kg",
        "soil_kg",
        "loose_kg",
        "bound_kg",
        "mineral_kg",
    )

    def __init__(self, capacity_l: float):
        self.capacity_l = capacity_l
        self.volume_l = 0.0
        self.temp_c = 20.0
        self.naoh_kg = 0.0
        self.acid_kg = 0.0
        self.soil_kg = 0.0
        self.loose_kg = 0.0
        self.bound_kg = 0.0
        self.mineral_kg = 0.0

    def absorb(self, parcel: Parcel) -> None:
        total = self.volume_l + parcel.volume_l
        if total > 0.0:
            self.temp_c = (self.temp_c * self.volume_l + parcel.temp_c * parcel.volume_l) / total
        self.volume_l = total
        self.naoh_kg += parcel.naoh_kg
        self.acid_kg += parcel.acid_kg
        self.soil_kg += parcel.soil_kg

    def displace(self, parcel: Parcel) -> Parcel:
        """Mix `parcel` in and push an equal volume out (a CSTR step).

        Mix first, then take the outflow from the mixture: the cell briefly
        holds volume + inflow and the fraction leaving is inflow/(volume +
        inflow). Written the other way round, taking the outflow from the
        pre-mix contents, the chain leaks mass on every step, which shows up
        as a circuit that swallows caustic and returns clean water.
        """
        volume = parcel.volume_l
        if volume <= 0.0 or self.volume_l <= 1e-9:
            return Parcel(0.0, self.temp_c)
        total = self.volume_l + volume
        fraction = volume / total
        self.naoh_kg += parcel.naoh_kg
        self.acid_kg += parcel.acid_kg
        self.soil_kg += parcel.soil_kg
        self.temp_c += (parcel.temp_c - self.temp_c) * fraction
        out = Parcel(
            volume,
            self.temp_c,
            self.naoh_kg * fraction,
            self.acid_kg * fraction,
            self.soil_kg * fraction,
        )
        self.naoh_kg -= out.naoh_kg
        self.acid_kg -= out.acid_kg
        self.soil_kg -= out.soil_kg
        return out

    @property
    def naoh_pct(self) -> float:
        return 100.0 * self.naoh_kg / self.volume_l if self.volume_l > 1e-6 else 0.0

    @property
    def acid_pct(self) -> float:
        return 100.0 * self.acid_kg / self.volume_l if self.volume_l > 1e-6 else 0.0


@dataclass
class Outputs:
    """The PLC output image, as the plant sees it."""

    supply_pump_run: bool = False
    return_pump_run: bool = False
    pump_speed_pct: float = 0.0
    steam_valve_pct: float = 0.0
    steam_isolation: bool = False
    v01_fresh: bool = False
    v02_recovered: bool = False
    v03_caustic_dose: bool = False
    v04_acid_dose: bool = False
    v05_supply: bool = False
    v06_drain: bool = False
    v07_balance: bool = False
    v08_caustic: bool = False
    v09_recovery: bool = False
    v10_acid: bool = False
    v11_caustic_supply: bool = False
    v12_acid_supply: bool = False


@dataclass
class Faults:
    """Injected faults, each a simulated time in seconds or None."""

    supply_pump_trip_at_s: Optional[float] = None
    return_pump_trip_at_s: Optional[float] = None
    steam_lost_at_s: Optional[float] = None
    circuit_lost_at_s: Optional[float] = None
    circuit_restored_at_s: Optional[float] = None
    estop_at_s: Optional[float] = None
    conductivity_frozen_at_s: Optional[float] = None
    heater_derate_from_s: Optional[float] = None
    heater_derate_factor: float = 0.25


@dataclass
class Totals:
    """Ground truth, tracked by the model rather than by the instruments."""

    fresh_water_l: float = 0.0
    recovered_water_l: float = 0.0
    caustic_stock_l: float = 0.0
    acid_stock_l: float = 0.0
    effluent_l: float = 0.0
    steam_kwh: float = 0.0
    caustic_tank_draw_l: float = 0.0
    acid_tank_draw_l: float = 0.0


class CipPlant:
    def __init__(
        self,
        params: PlantParams = DEFAULT_PARAMS,
        scenario: SoilScenario = SCENARIOS["medium"],
        faults: Optional[Faults] = None,
    ):
        self.p = params
        self.scenario = scenario
        self.faults = faults or Faults()
        self.rng = random.Random(params.seed)
        self.time_s = 0.0

        self.balance = Vessel(
            params.balance_tank_l,
            params.balance_tank_start_l,
            params.ambient_c,
        )
        self.caustic = Vessel(
            params.caustic_tank_l,
            params.caustic_tank_start_l,
            params.caustic_tank_start_c,
            naoh_kg=params.caustic_tank_start_l * params.caustic_tank_start_pct / 100.0,
        )
        self.acid = Vessel(
            params.acid_tank_l,
            params.acid_tank_start_l,
            params.acid_tank_start_c,
            acid_kg=params.acid_tank_start_l * params.acid_tank_start_pct / 100.0,
        )
        self.recovery = Vessel(
            params.recovery_tank_l,
            params.recovery_tank_start_l,
            params.recovery_tank_start_c,
        )

        cell_volume = params.circuit_volume_l / params.circuit_cells
        self.cells: List[Cell] = [Cell(cell_volume) for _ in range(params.circuit_cells)]
        masses = scenario.masses_kg(params.circuit_area_m2)
        per_cell = params.circuit_cells
        for cell in self.cells:
            cell.loose_kg = masses["loose"] / per_cell
            cell.bound_kg = masses["bound"] / per_cell
            cell.mineral_kg = masses["mineral"] / per_cell
            cell.temp_c = params.ambient_c

        self.flow_m3h = 0.0
        self.makeup_m3h = 0.0
        self.supply_temp_c = params.ambient_c
        self.return_parcel = Parcel(0.0, params.ambient_c)
        self.supply_parcel = Parcel(0.0, params.ambient_c)
        self.totals = Totals()
        self.duty_kw = 0.0

        self._supply_fb_timer = 0.0
        self._return_fb_timer = 0.0
        self._supply_running = False
        self._return_running = False
        self._frozen_conductivity: Optional[float] = None
        self._last_outputs = Outputs()

    # -- helpers -----------------------------------------------------------

    def conductivity(self, volume_l: float, naoh_kg: float, acid_kg: float, soil_kg: float) -> float:
        if volume_l <= 1e-6:
            return self.p.mains_sigma
        naoh_pct = 100.0 * naoh_kg / volume_l
        acid_pct = 100.0 * acid_kg / volume_l
        soil_gl = 1000.0 * soil_kg / volume_l
        sigma = self.p.mains_sigma
        sigma += self.p.naoh_sigma_a * naoh_pct - self.p.naoh_sigma_b * naoh_pct * naoh_pct
        sigma += self.p.acid_sigma_a * acid_pct - self.p.acid_sigma_b * acid_pct * acid_pct
        sigma += self.p.soil_sigma_per_gl * soil_gl
        return max(sigma, 0.0)

    def _parcel_conductivity(self, parcel: Parcel) -> float:
        return self.conductivity(parcel.volume_l, parcel.naoh_kg, parcel.acid_kg, parcel.soil_kg)

    def _fault_active(self, at_s: Optional[float]) -> bool:
        return at_s is not None and self.time_s >= at_s

    @property
    def circuit_available(self) -> bool:
        if self._fault_active(self.faults.circuit_lost_at_s):
            restored = self.faults.circuit_restored_at_s
            if restored is None or self.time_s < restored:
                return False
        return True

    @property
    def residual_soil_g_m2(self) -> float:
        total = sum(c.loose_kg + c.bound_kg + c.mineral_kg for c in self.cells)
        return 1000.0 * total / self.p.circuit_area_m2

    @property
    def residual_chemical_ppm(self) -> float:
        """Detergent still in the circuit when the cycle ends, mg per litre.

        This is what a final rinse is actually for and unlike the residual
        soil the controller does have a proxy for it: the return conductivity.
        """
        volume = sum(cell.volume_l for cell in self.cells)
        if volume <= 1e-6:
            return 0.0
        mass = sum(cell.naoh_kg + cell.acid_kg for cell in self.cells)
        return 1e6 * mass / volume

    def residual_breakdown_g_m2(self) -> Dict[str, float]:
        area = self.p.circuit_area_m2
        return {
            "loose": 1000.0 * sum(c.loose_kg for c in self.cells) / area,
            "bound": 1000.0 * sum(c.bound_kg for c in self.cells) / area,
            "mineral": 1000.0 * sum(c.mineral_kg for c in self.cells) / area,
        }

    # -- the step ----------------------------------------------------------

    def step(self, dt: float, out: Outputs) -> None:
        p = self.p
        self.time_s += dt
        self._last_outputs = out

        pump_tripped = self._fault_active(self.faults.supply_pump_trip_at_s)
        return_tripped = self._fault_active(self.faults.return_pump_trip_at_s)
        estop = self._fault_active(self.faults.estop_at_s)

        # ---- pump and flow ----------------------------------------------
        can_pump = (
            out.supply_pump_run
            and not pump_tripped
            and not estop
            and self.balance.level_pct > p.cavitation_level_pct
        )
        target_m3h = (out.pump_speed_pct / 100.0) * p.supply_pump_max_m3h if can_pump else 0.0
        alpha = dt / max(p.supply_pump_tau_s, 1e-6)
        alpha = min(alpha, 1.0)
        self.flow_m3h += (target_m3h - self.flow_m3h) * alpha
        if self.flow_m3h < 0.02:
            self.flow_m3h = 0.0

        q_supply_l = self.flow_m3h * 1000.0 / 3600.0 * dt  # litres this step
        q_supply_l = min(q_supply_l, max(self.balance.volume_l - 1.0, 0.0))

        # ---- make-up and dosing -----------------------------------------
        makeup_source = None
        makeup_rate_m3h = 0.0
        if out.v01_fresh:
            makeup_source = "fresh"
            makeup_rate_m3h = p.makeup_valve_m3h
        elif out.v02_recovered:
            makeup_source = "recovered"
            makeup_rate_m3h = p.makeup_valve_m3h
        elif out.v11_caustic_supply:
            makeup_source = "caustic"
            makeup_rate_m3h = p.chem_supply_valve_m3h
        elif out.v12_acid_supply:
            makeup_source = "acid"
            makeup_rate_m3h = p.chem_supply_valve_m3h

        space_l = max(self.balance.capacity_l - self.balance.volume_l, 0.0)
        makeup_l = min(makeup_rate_m3h * 1000.0 / 3600.0 * dt, space_l)
        self.makeup_m3h = makeup_l / dt * 3600.0 / 1000.0 if dt > 0 else 0.0

        if makeup_l > 0.0 and makeup_source is not None:
            if makeup_source == "fresh":
                self.balance.add(Parcel(makeup_l, p.mains_temp_c))
                self.totals.fresh_water_l += makeup_l
            elif makeup_source == "recovered":
                taken = self.recovery.draw(makeup_l)
                self.balance.add(taken)
                self.totals.recovered_water_l += taken.volume_l
                self.makeup_m3h = taken.volume_l / dt * 3600.0 / 1000.0 if dt > 0 else 0.0
            elif makeup_source == "caustic":
                taken = self.caustic.draw(makeup_l)
                self.balance.add(taken)
                self.totals.caustic_tank_draw_l += taken.volume_l
                self.makeup_m3h = taken.volume_l / dt * 3600.0 / 1000.0 if dt > 0 else 0.0
            else:
                taken = self.acid.draw(makeup_l)
                self.balance.add(taken)
                self.totals.acid_tank_draw_l += taken.volume_l
                self.makeup_m3h = taken.volume_l / dt * 3600.0 / 1000.0 if dt > 0 else 0.0

        if out.v03_caustic_dose:
            dose_l = p.caustic_dose_lpm / 60.0 * dt
            self.balance.add(
                Parcel(
                    dose_l,
                    25.0,
                    naoh_kg=dose_l * p.caustic_stock_density * p.caustic_stock_pct / 100.0,
                )
            )
            self.totals.caustic_stock_l += dose_l
        if out.v04_acid_dose:
            dose_l = p.acid_dose_lpm / 60.0 * dt
            self.balance.add(
                Parcel(
                    dose_l,
                    25.0,
                    acid_kg=dose_l * p.acid_stock_density * p.acid_stock_pct / 100.0,
                )
            )
            self.totals.acid_stock_l += dose_l

        # ---- heat exchanger ---------------------------------------------
        supply = self.balance.draw(q_supply_l) if out.v05_supply else Parcel(0.0, self.balance.temp_c)
        if supply.volume_l <= 0.0 and q_supply_l > 0.0:
            # v05 shut: the pump is dead-heading, no flow reaches the circuit.
            self.flow_m3h = 0.0

        duty_kw = 0.0
        if out.steam_isolation and supply.volume_l > 0.0:
            duty_kw = out.steam_valve_pct / 100.0 * p.heater_max_kw
            if self._fault_active(self.faults.heater_derate_from_s):
                duty_kw *= self.faults.heater_derate_factor
            if self._fault_active(self.faults.steam_lost_at_s):
                duty_kw = 0.0
            heat_capacity_kj = supply.volume_l * p.cp_kj_kgk
            rise = duty_kw * dt / heat_capacity_kj if heat_capacity_kj > 0 else 0.0
            new_temp = min(supply.temp_c + rise, p.max_supply_temp_c)
            actual_rise = new_temp - supply.temp_c
            duty_kw = actual_rise * heat_capacity_kj / dt if dt > 0 else 0.0
            supply.temp_c = new_temp
        self.duty_kw = duty_kw
        self.totals.steam_kwh += duty_kw * dt / 3600.0

        self.supply_parcel = Parcel(
            supply.volume_l, supply.temp_c, supply.naoh_kg, supply.acid_kg, supply.soil_kg
        )

        # ---- circuit -----------------------------------------------------
        velocity = self.flow_m3h / 3600.0 / p.pipe_area_m2
        self._clean(dt, velocity)
        outflow = self._advance(supply)
        self.return_parcel = outflow

        for cell in self.cells:
            if cell.volume_l > 1e-6:
                heat_capacity = cell.volume_l * p.cp_kj_kgk
                ua = p.ua_circuit_kw_k / p.circuit_cells
                cell.temp_c -= ua * (cell.temp_c - p.ambient_c) * dt / heat_capacity

        # ---- return routing ---------------------------------------------
        if outflow.volume_l > 0.0:
            if return_tripped or estop:
                # No return pump: the circuit backs up and the phase will fail
                # on flow. Modelled as the liquid going to drain, which is what
                # the overflow does.
                self.totals.effluent_l += outflow.volume_l
            elif out.v06_drain:
                self.totals.effluent_l += outflow.volume_l
            elif out.v09_recovery:
                spill = max(
                    self.recovery.volume_l + outflow.volume_l - self.recovery.capacity_l, 0.0
                )
                if spill > 0.0:
                    self.totals.effluent_l += spill
                    self.recovery.add(outflow.scaled(outflow.volume_l - spill))
                else:
                    self.recovery.add(outflow)
            elif out.v08_caustic:
                self.caustic.add(outflow)
            elif out.v10_acid:
                self.acid.add(outflow)
            elif out.v07_balance:
                self.balance.add(outflow)
            else:
                # No return path selected: it goes over the drain weir.
                self.totals.effluent_l += outflow.volume_l

        # ---- tank heat losses -------------------------------------------
        self.balance.lose_heat(p.ua_balance_kw_k, p.ambient_c, dt, p.cp_kj_kgk)
        self.caustic.lose_heat(p.ua_chem_tank_kw_k, p.ambient_c, dt, p.cp_kj_kgk)
        self.acid.lose_heat(p.ua_chem_tank_kw_k, p.ambient_c, dt, p.cp_kj_kgk)
        self.recovery.lose_heat(p.ua_chem_tank_kw_k, p.ambient_c, dt, p.cp_kj_kgk)

        # ---- contactor feedback ------------------------------------------
        self._supply_running, self._supply_fb_timer = self._feedback(
            out.supply_pump_run and not pump_tripped and not estop,
            self._supply_running,
            self._supply_fb_timer,
            dt,
        )
        self._return_running, self._return_fb_timer = self._feedback(
            out.return_pump_run and not return_tripped and not estop,
            self._return_running,
            self._return_fb_timer,
            dt,
        )

    @staticmethod
    def _feedback(command: bool, state: bool, timer: float, dt: float) -> "tuple":
        """A contactor takes about 400 ms to make or break."""
        if command == state:
            return state, 0.0
        timer += dt
        if timer >= 0.4:
            return command, 0.0
        return state, timer

    def _advance(self, supply: Parcel) -> Parcel:
        carry = supply
        for cell in self.cells:
            if carry.volume_l <= 1e-12:
                return Parcel(0.0, cell.temp_c)
            space = cell.capacity_l - cell.volume_l
            if space > 1e-9:
                take = min(carry.volume_l, space)
                cell.absorb(carry.scaled(take))
                carry = carry.scaled(carry.volume_l - take) if carry.volume_l > take else Parcel(
                    0.0, carry.temp_c
                )
                if carry.volume_l <= 1e-12:
                    return Parcel(0.0, cell.temp_c)
            carry = cell.displace(carry)
        return carry

    def _clean(self, dt: float, velocity_ms: float) -> None:
        p = self.p
        if velocity_ms < 0.05:
            return
        shear = (velocity_ms / p.ref_velocity_ms) ** p.velocity_exponent
        for cell in self.cells:
            if cell.volume_l <= 1e-6:
                continue
            temp_k = cell.temp_c + 273.15
            naoh_pct = cell.naoh_pct
            acid_pct = cell.acid_pct

            removed = 0.0
            if cell.loose_kg > 0.0:
                # Mechanical, with a mild thermal assist.
                k = p.loose_rate_s * shear * (1.0 + 0.008 * (cell.temp_c - 20.0))
                gone = cell.loose_kg * (1.0 - math.exp(-max(k, 0.0) * dt))
                cell.loose_kg -= gone
                removed += gone

            if cell.bound_kg > 0.0:
                k = (
                    p.bound_rate_s
                    * shear
                    * _arrhenius(temp_k, p.bound_ref_temp_c, p.activation_kj_mol)
                    * _saturation(naoh_pct, p.conc_half_pct, 2.0)
                )
                k += (
                    p.bound_rate_s
                    * shear
                    * p.cross_removal_factor
                    * _arrhenius(temp_k, p.bound_ref_temp_c, p.activation_kj_mol)
                    * _saturation(acid_pct, p.conc_half_pct, 2.0)
                )
                gone = cell.bound_kg * (1.0 - math.exp(-max(k, 0.0) * dt))
                cell.bound_kg -= gone
                removed += gone

            if cell.mineral_kg > 0.0:
                k = (
                    p.mineral_rate_s
                    * shear
                    * _arrhenius(temp_k, p.mineral_ref_temp_c, p.activation_kj_mol)
                    * _saturation(acid_pct, p.conc_half_pct, 1.0)
                )
                k += (
                    p.mineral_rate_s
                    * shear
                    * p.cross_removal_factor
                    * _arrhenius(temp_k, p.mineral_ref_temp_c, p.activation_kj_mol)
                    * _saturation(naoh_pct, p.conc_half_pct, 1.0)
                )
                gone = cell.mineral_kg * (1.0 - math.exp(-max(k, 0.0) * dt))
                cell.mineral_kg -= gone
                removed += gone

            cell.soil_kg += removed

    # -- instruments -------------------------------------------------------

    def sensors(self) -> Dict[str, float]:
        p = self.p
        rng = self.rng

        return_sigma = self._parcel_conductivity(self.return_parcel)
        if self.return_parcel.volume_l <= 1e-9:
            # Nothing is coming back; the cell in front of the sensor is what
            # it reads.
            last = self.cells[-1]
            return_sigma = self.conductivity(
                last.volume_l, last.naoh_kg, last.acid_kg, last.soil_kg
            )
        if self._fault_active(self.faults.conductivity_frozen_at_s):
            if self._frozen_conductivity is None:
                self._frozen_conductivity = return_sigma
            return_sigma = self._frozen_conductivity

        supply_sigma = self._parcel_conductivity(self.supply_parcel)
        if self.supply_parcel.volume_l <= 1e-9:
            supply_sigma = self.conductivity(
                self.balance.volume_l, self.balance.naoh_kg, self.balance.acid_kg, self.balance.soil_kg
            )

        return_temp = self.cells[-1].temp_c if self.cells[-1].volume_l > 1e-6 else self.balance.temp_c
        supply_temp = (
            self.supply_parcel.temp_c if self.supply_parcel.volume_l > 1e-9 else self.balance.temp_c
        )

        estop = self._fault_active(self.faults.estop_at_s)

        return {
            "rLevelBalanceTankPct": _clip(
                self.balance.level_pct + rng.gauss(0.0, p.level_noise_pct), 0.0, 100.0
            ),
            "rTempSupplyC": supply_temp + rng.gauss(0.0, p.temp_noise_c),
            "rTempReturnC": return_temp + rng.gauss(0.0, p.temp_noise_c),
            "rTempBalanceTankC": self.balance.temp_c + rng.gauss(0.0, p.temp_noise_c),
            "rFlowSupplyM3h": max(self.flow_m3h + rng.gauss(0.0, p.flow_noise_m3h), 0.0),
            "rFlowMakeupM3h": max(self.makeup_m3h + rng.gauss(0.0, p.flow_noise_m3h), 0.0),
            "rConductivityReturn": max(return_sigma + rng.gauss(0.0, p.conductivity_noise), 0.0),
            "rConductivitySupply": max(supply_sigma + rng.gauss(0.0, p.conductivity_noise), 0.0),
            "rConductivityTank": max(
                self.conductivity(
                    self.balance.volume_l,
                    self.balance.naoh_kg,
                    self.balance.acid_kg,
                    self.balance.soil_kg,
                )
                + rng.gauss(0.0, p.conductivity_noise),
                0.0,
            ),
            "rPressureSupplyBar": 0.006 * self.flow_m3h * self.flow_m3h,
            "rLevelCausticPct": self.caustic.level_pct,
            "rLevelAcidPct": self.acid.level_pct,
            "rLevelRecoveryPct": self.recovery.level_pct,
            "xSafetyOk": not estop,
            "xSupplyPumpRunning": self._supply_running,
            "xReturnPumpRunning": self._return_running,
            "xSupplyPumpFault": self._fault_active(self.faults.supply_pump_trip_at_s),
            "xReturnPumpFault": self._fault_active(self.faults.return_pump_trip_at_s),
            "xCircuitAvailable": self.circuit_available,
            "xSteamAvailable": not self._fault_active(self.faults.steam_lost_at_s),
            "xBalanceTankLowLow": self.balance.level_pct < 5.0,
        }


def _arrhenius(temp_k: float, ref_temp_c: float, activation_kj_mol: float) -> float:
    ref_k = ref_temp_c + 273.15
    exponent = -(activation_kj_mol * 1000.0 / R_GAS) * (1.0 / temp_k - 1.0 / ref_k)
    # A cold circuit should not produce a numerically silly rate either way.
    return math.exp(max(min(exponent, 3.0), -20.0))


def _saturation(conc_pct: float, half_pct: float, ref_pct: float) -> float:
    if conc_pct <= 0.0:
        return 0.0
    ref = ref_pct / (half_pct + ref_pct)
    return (conc_pct / (half_pct + conc_pct)) / ref


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
