"""The plant model.

The controller is only worth testing against a model that conserves what it
should. These check the conservation laws, the instrument calibrations and the
direction of every physical effect the cleaning logic relies on.
"""

from __future__ import annotations

import pytest

from src.plant.model import CipPlant, Faults, Outputs, Parcel
from src.plant.params import SCENARIOS, PlantParams

CIRCULATE = Outputs(
    supply_pump_run=True,
    return_pump_run=True,
    pump_speed_pct=75.0,
    v05_supply=True,
    v07_balance=True,
)


def circulating_plant(**kwargs) -> CipPlant:
    plant = CipPlant(PlantParams(conductivity_noise=0.0, flow_noise_m3h=0.0, **kwargs))
    return plant


def prime(plant: CipPlant, seconds: float = 240.0, outputs: Outputs = CIRCULATE) -> None:
    for _ in range(int(seconds / 0.2)):
        plant.step(0.2, outputs)


def total_volume(plant: CipPlant) -> float:
    return (
        plant.balance.volume_l
        + plant.caustic.volume_l
        + plant.acid.volume_l
        + plant.recovery.volume_l
        + sum(cell.volume_l for cell in plant.cells)
    )


def total_naoh(plant: CipPlant) -> float:
    return (
        plant.balance.naoh_kg
        + plant.caustic.naoh_kg
        + plant.acid.naoh_kg
        + plant.recovery.naoh_kg
        + sum(cell.naoh_kg for cell in plant.cells)
    )


def test_a_closed_loop_conserves_volume_and_chemical():
    """The bug this was written for: an arithmetic slip in the cell chain let
    the circuit swallow caustic and return clean water."""
    plant = circulating_plant()
    plant.balance.add(Parcel(600.0, 60.0, naoh_kg=12.0))
    volume_before = total_volume(plant)
    naoh_before = total_naoh(plant)
    prime(plant, 600.0)
    assert total_volume(plant) == pytest.approx(volume_before, rel=1e-9)
    assert total_naoh(plant) == pytest.approx(naoh_before, rel=1e-9)


def test_chemical_reaches_the_return_line_after_the_transport_delay():
    plant = circulating_plant()
    plant.balance.add(Parcel(600.0, 60.0, naoh_kg=12.0))
    prime(plant, 30.0)
    early = plant.sensors()["rConductivityReturn"]
    prime(plant, 400.0)
    late = plant.sensors()["rConductivityReturn"]
    assert early < 5.0, "the circuit is still full of the water it started with"
    assert late > 50.0, "after several holdup volumes the return sees the caustic"


def test_conductivity_calibration_matches_the_controller_constant():
    """The PLC divides the reading by 47 mS/cm per %; the model has to put a
    2 % solution close enough to 94 for that to give 2.00 back."""
    plant = circulating_plant()
    sigma = plant.conductivity(1000.0, naoh_kg=20.0, acid_kg=0.0, soil_kg=0.0)
    assert sigma - plant.p.mains_sigma == pytest.approx(94.0, abs=0.5)
    assert (sigma - plant.p.mains_sigma) / 47.0 == pytest.approx(2.0, abs=0.02)

    acid_sigma = plant.conductivity(1000.0, naoh_kg=0.0, acid_kg=10.0, soil_kg=0.0)
    assert (acid_sigma - plant.p.mains_sigma) / 45.0 == pytest.approx(1.0, abs=0.03)


def test_dissolved_soil_raises_the_conductivity():
    plant = circulating_plant()
    clean = plant.conductivity(1000.0, 0.0, 0.0, 0.0)
    dirty = plant.conductivity(1000.0, 0.0, 0.0, soil_kg=2.0)
    assert dirty > clean


def test_water_alone_removes_loose_soil_but_not_the_bound_pool():
    plant = CipPlant(scenario=SCENARIOS["medium"])
    plant.balance.add(Parcel(600.0, 40.0))
    before = plant.residual_breakdown_g_m2()
    prime(plant, 400.0)
    after = plant.residual_breakdown_g_m2()
    assert after["loose"] < before["loose"] * 0.2
    assert after["bound"] == pytest.approx(before["bound"], rel=1e-6)
    assert after["mineral"] == pytest.approx(before["mineral"], rel=1e-6)


def test_hot_caustic_removes_the_bound_pool_and_faster_when_hotter():
    def remaining(temp_c: float) -> float:
        plant = CipPlant(scenario=SCENARIOS["medium"])
        plant.balance.add(Parcel(600.0, temp_c, naoh_kg=12.0))
        for _ in range(int(600 / 0.2)):
            # Hold the tank temperature: this test is about the kinetics, not
            # about the heat exchanger.
            plant.balance.temp_c = temp_c
            for cell in plant.cells:
                cell.temp_c = temp_c
            plant.step(0.2, CIRCULATE)
        return plant.residual_breakdown_g_m2()["bound"]

    hot = remaining(75.0)
    warm = remaining(55.0)
    assert hot < warm, "Arrhenius: hotter cleans faster"
    # Ten minutes at 75 degC and 2 % takes 26 g/m2 down to well under the
    # 1 g/m2 release limit, allowing for the first minute and a half spent
    # displacing the water the circuit started full of.
    assert hot < 0.5


def test_acid_is_what_removes_the_mineral_pool():
    def remaining(acid_kg: float) -> float:
        plant = CipPlant(scenario=SCENARIOS["heavy"])
        plant.balance.add(Parcel(600.0, 60.0, acid_kg=acid_kg))
        for _ in range(int(600 / 0.2)):
            plant.balance.temp_c = 60.0
            for cell in plant.cells:
                cell.temp_c = 60.0
            plant.step(0.2, CIRCULATE)
        return plant.residual_breakdown_g_m2()["mineral"]

    assert remaining(6.0) < remaining(0.0) * 0.2


def test_higher_velocity_cleans_faster():
    def remaining(speed_pct: float) -> float:
        plant = CipPlant(scenario=SCENARIOS["medium"])
        plant.balance.add(Parcel(700.0, 40.0))
        outputs = Outputs(
            supply_pump_run=True,
            return_pump_run=True,
            pump_speed_pct=speed_pct,
            v05_supply=True,
            v07_balance=True,
        )
        for _ in range(int(300 / 0.2)):
            plant.step(0.2, outputs)
        return plant.residual_breakdown_g_m2()["loose"]

    assert remaining(90.0) < remaining(45.0)


def test_the_pump_stops_pumping_when_the_tank_runs_dry():
    plant = circulating_plant()
    plant.balance.volume_l = 10.0
    plant.step(0.2, CIRCULATE)
    assert plant.flow_m3h == 0.0


def test_heating_raises_the_supply_temperature_and_books_the_energy():
    plant = circulating_plant()
    plant.balance.add(Parcel(700.0, 20.0))
    hot = Outputs(
        supply_pump_run=True,
        return_pump_run=True,
        pump_speed_pct=75.0,
        steam_isolation=True,
        steam_valve_pct=100.0,
        v05_supply=True,
        v07_balance=True,
    )
    prime(plant, 300.0, hot)
    assert plant.balance.temp_c > 40.0
    assert plant.totals.steam_kwh > 5.0
    assert plant.supply_parcel.temp_c <= plant.p.max_supply_temp_c + 1e-6


def test_a_tripped_pump_stops_the_flow():
    plant = CipPlant(faults=Faults(supply_pump_trip_at_s=1.0))
    plant.balance.add(Parcel(700.0, 20.0))
    prime(plant, 10.0)
    assert plant.flow_m3h == 0.0
    assert plant.sensors()["xSupplyPumpFault"] is True


def test_a_frozen_conductivity_transmitter_stops_moving():
    plant = CipPlant(
        PlantParams(conductivity_noise=0.0), faults=Faults(conductivity_frozen_at_s=1.0)
    )
    plant.balance.add(Parcel(700.0, 20.0))
    prime(plant, 5.0)
    stuck = plant.sensors()["rConductivityReturn"]
    plant.balance.add(Parcel(50.0, 20.0, naoh_kg=10.0))
    prime(plant, 400.0)
    assert plant.sensors()["rConductivityReturn"] == pytest.approx(stuck)


def test_contactor_feedback_lags_the_command():
    plant = circulating_plant()
    plant.balance.add(Parcel(700.0, 20.0))
    plant.step(0.2, CIRCULATE)
    assert plant.sensors()["xSupplyPumpRunning"] is False
    prime(plant, 1.0)
    assert plant.sensors()["xSupplyPumpRunning"] is True


def test_residual_chemical_is_reported_in_ppm():
    plant = circulating_plant()
    plant.balance.add(Parcel(600.0, 40.0, naoh_kg=6.0))
    prime(plant, 600.0)
    # 6 kg in about 1050 litres of system inventory, so the circuit sits near
    # 5700 mg/L; the exact figure depends on the split, the order does not.
    assert 1000.0 < plant.residual_chemical_ppm < 20000.0


def test_the_model_is_reproducible():
    def run() -> float:
        plant = CipPlant(scenario=SCENARIOS["heavy"])
        plant.balance.add(Parcel(600.0, 50.0, naoh_kg=12.0))
        prime(plant, 200.0)
        return plant.sensors()["rConductivityReturn"]

    assert run() == run()
