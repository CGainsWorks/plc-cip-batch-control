"""Physical parameters for the simulated skid.

Every number here is a plant property, not a control parameter: the PLC never
reads this file. The few figures that appear on both sides (balance tank
capacity, dosing pump rate) are checked against the ST constants by
tests/test_io_map.py, because a controller that thinks the tank is a different
size from the tank is a real and unpleasant class of bug.

Sources for the physical constants are given in docs/model.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class PlantParams:
    # ---- geometry -------------------------------------------------------
    balance_tank_l: float = 1200.0
    caustic_tank_l: float = 1500.0
    acid_tank_l: float = 1000.0
    recovery_tank_l: float = 1500.0
    circuit_volume_l: float = 450.0
    circuit_cells: int = 20
    circuit_area_m2: float = 42.0  # wetted surface being cleaned
    pipe_area_m2: float = 0.002856  # DN65, 60.3 mm bore

    # ---- pumps and valves ----------------------------------------------
    supply_pump_max_m3h: float = 24.0
    supply_pump_tau_s: float = 1.5
    makeup_valve_m3h: float = 22.0
    chem_supply_valve_m3h: float = 20.0
    caustic_dose_lpm: float = 30.0
    acid_dose_lpm: float = 20.0
    cavitation_level_pct: float = 6.0

    # ---- chemicals ------------------------------------------------------
    caustic_stock_pct: float = 25.0  # w/w NaOH concentrate
    caustic_stock_density: float = 1.27  # kg/L
    acid_stock_pct: float = 30.0  # w/w HNO3 concentrate
    acid_stock_density: float = 1.18

    # ---- thermal --------------------------------------------------------
    heater_max_kw: float = 550.0
    max_supply_temp_c: float = 95.0
    ambient_c: float = 18.0
    mains_temp_c: float = 12.0
    cp_kj_kgk: float = 4.19
    ua_balance_kw_k: float = 0.14
    ua_chem_tank_kw_k: float = 0.10
    ua_circuit_kw_k: float = 1.30

    # ---- conductivity ---------------------------------------------------
    # sigma = a*c - b*c^2, c in % w/w, sigma in mS/cm. The quadratic term is
    # the mobility roll-off that makes a single-point calibration wrong at the
    # other end of the range.
    naoh_sigma_a: float = 53.0
    naoh_sigma_b: float = 3.0
    acid_sigma_a: float = 48.0
    acid_sigma_b: float = 2.0
    soil_sigma_per_gl: float = 0.35  # mS/cm per g/L of dissolved soil
    mains_sigma: float = 0.50
    conductivity_noise: float = 0.004  # mS/cm, 1 sigma
    flow_noise_m3h: float = 0.05
    temp_noise_c: float = 0.05
    level_noise_pct: float = 0.05

    # ---- soil removal kinetics -----------------------------------------
    # Rates are per second at the reference condition stated beside each one.
    loose_rate_s: float = 0.0130  # water only, 1.5 m/s
    bound_rate_s: float = 0.0088  # 2 % NaOH, 75 degC, 1.5 m/s
    mineral_rate_s: float = 0.0075  # 1 % HNO3, 60 degC, 1.5 m/s
    activation_kj_mol: float = 45.0
    bound_ref_temp_c: float = 75.0
    mineral_ref_temp_c: float = 60.0
    conc_half_pct: float = 0.5  # Michaelis-type half-saturation
    ref_velocity_ms: float = 1.5
    velocity_exponent: float = 0.6
    # Caustic does remove some mineral scale and acid some protein but an
    # order of magnitude slower. Without this the phases look independent,
    # which they are not.
    cross_removal_factor: float = 0.08

    # ---- starting tank contents ----------------------------------------
    caustic_tank_start_pct: float = 2.05
    acid_tank_start_pct: float = 1.05
    caustic_tank_start_l: float = 1200.0
    acid_tank_start_l: float = 800.0
    recovery_tank_start_l: float = 900.0
    recovery_tank_start_c: float = 38.0
    # Detergent tanks are held warm between cycles; that is most of the reason
    # a recovered-chemical skid heats faster than a make-up-every-time one.
    caustic_tank_start_c: float = 68.0
    acid_tank_start_c: float = 45.0
    balance_tank_start_l: float = 150.0

    seed: int = 20260822


@dataclass(frozen=True)
class SoilScenario:
    """How dirty the circuit is when the cycle starts."""

    name: str = "medium"
    loose_g_m2: float = 48.0
    bound_g_m2: float = 26.0
    mineral_g_m2: float = 6.0
    description: str = ""

    def masses_kg(self, area_m2: float) -> Dict[str, float]:
        return {
            "loose": self.loose_g_m2 * area_m2 / 1000.0,
            "bound": self.bound_g_m2 * area_m2 / 1000.0,
            "mineral": self.mineral_g_m2 * area_m2 / 1000.0,
        }

    @property
    def total_g_m2(self) -> float:
        return self.loose_g_m2 + self.bound_g_m2 + self.mineral_g_m2


SCENARIOS: Dict[str, SoilScenario] = {
    "light": SoilScenario(
        name="light",
        loose_g_m2=18.0,
        bound_g_m2=8.0,
        mineral_g_m2=2.0,
        description="short run on a clean product, cleaned within the hour",
    ),
    "medium": SoilScenario(
        name="medium",
        loose_g_m2=48.0,
        bound_g_m2=26.0,
        mineral_g_m2=6.0,
        description="a normal production shift",
    ),
    "heavy": SoilScenario(
        name="heavy",
        loose_g_m2=95.0,
        bound_g_m2=62.0,
        mineral_g_m2=18.0,
        description="a long run with heat-set protein and scale build-up",
    ),
    "worst": SoilScenario(
        name="worst",
        loose_g_m2=140.0,
        bound_g_m2=105.0,
        mineral_g_m2=32.0,
        description="the case the fixed-time recipe was sized against",
    ),
}

# Release limit for the residual soil the plant model tracks but no instrument
# on the skid can see. Riboflavin and swab tests in dairy practice are pass or
# fail against visually clean plus an ATP threshold; 1 g/m2 is the working
# equivalent used here and stated as an assumption, not a standard.
RESIDUAL_LIMIT_G_M2: float = 1.0

# Detergent left in the circuit after the final rinse. 50 mg/L is the working
# figure used here; it corresponds to roughly 0.03 mS/cm of caustic above the
# incoming water, so it sits just outside the 0.10 mS/cm release threshold the
# recipe uses. Stated as an assumption, not a standard.
RESIDUAL_CHEMICAL_LIMIT_PPM: float = 50.0

DEFAULT_PARAMS = PlantParams()
