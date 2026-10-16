"""The simulated cell: one PLC, one plant, one scan loop.

Each cycle of `step()` is a real PLC scan in the right order (read the input
image, execute the task, write the output image) followed by one integration
step of the plant. The PLC never touches the model directly; everything it
knows comes through the located variables listed in GVL_Io.st.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Union

from ..iec61131.runtime import Runtime, load
from ..plant.model import CipPlant, Faults, Outputs
from ..plant.params import DEFAULT_PARAMS, SCENARIOS, PlantParams, SoilScenario
from .io_map import IoMap, build_io_map

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLC_DIR = os.path.join(REPO_ROOT, "plc")
CONFIG_DIR = os.path.join(REPO_ROOT, "config")

PROGRAM_ORDER = ("PRG_Safety", "PRG_Sequence", "PRG_Control", "PRG_Alarms", "PRG_Kpi")

# What executes the ST. "interpreter" is src/iec61131; "rusty" is the same
# source compiled to machine code by RuSTy (see src/harness/compiled.py).
BACKENDS = ("interpreter", "rusty")


def make_runtime(backend: str = "interpreter", plc_dir: str = PLC_DIR) -> Any:
    if backend == "interpreter":
        return load(plc_dir, PROGRAM_ORDER)
    if backend == "rusty":
        from .compiled import CompiledRuntime

        return CompiledRuntime(plc_dir, PROGRAM_ORDER)
    raise ValueError("unknown backend {0!r}; expected one of {1}".format(backend, BACKENDS))

# PLC output tag -> field of the plant's Outputs image.
OUTPUT_BINDING = {
    "XSUPPLYPUMPRUN": "supply_pump_run",
    "XRETURNPUMPRUN": "return_pump_run",
    "RSUPPLYPUMPSPEEDPCT": "pump_speed_pct",
    "RSTEAMVALVEPCT": "steam_valve_pct",
    "XSTEAMISOLATION": "steam_isolation",
    "XV01FRESHWATER": "v01_fresh",
    "XV02RECOVEREDWATER": "v02_recovered",
    "XV03CAUSTICDOSE": "v03_caustic_dose",
    "XV04ACIDDOSE": "v04_acid_dose",
    "XV05SUPPLYTOCIRCUIT": "v05_supply",
    "XV06RETURNTODRAIN": "v06_drain",
    "XV07RETURNTOBALANCE": "v07_balance",
    "XV08RETURNTOCAUSTIC": "v08_caustic",
    "XV09RETURNTORECOVERY": "v09_recovery",
    "XV10RETURNTOACID": "v10_acid",
    "XV11CAUSTICSUPPLY": "v11_caustic_supply",
    "XV12ACIDSUPPLY": "v12_acid_supply",
}

# Recipe key in config/recipes.json -> (ST member, unit conversion)
RECIPE_BINDING = {
    "use_acid": "xUseAcid",
    "use_sanitise": "xUseSanitise",
    "adaptive": "xAdaptive",
    "flow_sp_m3h": "rFlowSp",
    "flow_min_m3h": "rFlowMin",
    "pre_rinse_s": "tPreRinse",
    "caustic_temp_sp_c": "rCausticTempSp",
    "caustic_temp_min_c": "rCausticTempMin",
    "caustic_conc_sp_pct": "rCausticConcSp",
    "caustic_hold_s": "tCausticHold",
    "int_rinse_s": "tIntRinse",
    "acid_temp_sp_c": "rAcidTempSp",
    "acid_temp_min_c": "rAcidTempMin",
    "acid_conc_sp_pct": "rAcidConcSp",
    "acid_hold_s": "tAcidHold",
    "final_rinse_s": "tFinalRinse",
    "sanitise_temp_sp_c": "rSanitiseTempSp",
    "sanitise_temp_min_c": "rSanitiseTempMin",
    "sanitise_hold_s": "tSanitiseHold",
    "rinse_end_cond_ms_cm": "rRinseEndCond",
    "phase_max_s": "tPhaseMax",
    "rinse_max_s": "tRinseMax",
    "rinse_min_s": "tRinseMin",
}

PHASE_NAMES = {
    0: "IDLE",
    1: "PRE_RINSE",
    2: "CAUSTIC_WASH",
    3: "INTERMEDIATE_RINSE",
    4: "ACID_WASH",
    5: "FINAL_RINSE",
    6: "SANITISE",
    7: "COMPLETE",
}

UNIT_STATE_NAMES = {
    0: "IDLE",
    1: "STARTING",
    2: "RUNNING",
    3: "HOLDING",
    4: "HELD",
    5: "RESTARTING",
    6: "COMPLETING",
    7: "COMPLETE",
    8: "ABORTING",
    9: "ABORTED",
}

STEP_NAMES = {0: "IDLE", 1: "LINEUP", 2: "CHARGE", 3: "CIRCULATE", 4: "RECOVER", 5: "DONE"}

FAIL_NAMES = {
    0: "NONE",
    1: "LINEUP_TIMEOUT",
    2: "CHARGE_TIMEOUT",
    3: "TEMP_TIMEOUT",
    4: "PHASE_TIMEOUT",
    5: "FLOW_LOW",
    6: "PUMP_FAULT",
    7: "OPERATOR_ABORT",
    8: "SAFETY_TRIP",
    9: "RINSE_NOT_CLEAN",
    10: "LEVEL_LOW",
    11: "SENSOR_FAULT",
}

ALARM_TAGS = {
    1: "SAFETY_TRIP",
    2: "SUPPLY_PUMP",
    3: "RETURN_PUMP",
    4: "BALANCE_LEVEL_LOW",
    5: "CAUSTIC_LEVEL_LOW",
    6: "ACID_LEVEL_LOW",
    7: "TEMPERATURE_LOW",
    8: "FLOW_LOW",
    9: "CIRCUIT_UNAVAILABLE",
    10: "PHASE_TIMEOUT",
    11: "RINSE_NOT_CLEAN",
    12: "STEAM_UNAVAILABLE",
    13: "CONDUCTIVITY_SENSOR",
}

COMMAND_TAGS = {
    "start": "xCmdStart",
    "hold": "xCmdHold",
    "restart": "xCmdRestart",
    "abort": "xCmdAbort",
    "reset": "xCmdReset",
    "ack": "xCmdAckAlarms",
}


def load_recipes(path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    path = path or os.path.join(CONFIG_DIR, "recipes.json")
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def fast_recipe(base: str = "adaptive", **overrides):
    """A cut-down recipe so a sequencing test is seconds rather than a minute.

    Holds and temperatures are trimmed, not removed: the point is to exercise
    the phase order, the interlocks and the fault paths without simulating a
    forty-five minute heat-up every time. The consumption figures in results/
    come from the real recipes in config/recipes.json.
    """
    recipe = dict(load_recipes()[base])
    recipe.update(
        {
            "name": "test-fast",
            "pre_rinse_s": 60,
            "caustic_temp_sp_c": 45.0,
            "caustic_temp_min_c": 40.0,
            "caustic_hold_s": 60,
            "int_rinse_s": 60,
            "acid_temp_sp_c": 40.0,
            "acid_temp_min_c": 35.0,
            "acid_hold_s": 60,
            "final_rinse_s": 60,
            "sanitise_temp_sp_c": 50.0,
            "sanitise_temp_min_c": 45.0,
            "sanitise_hold_s": 60,
            "rinse_max_s": 420,
            "phase_max_s": 1200,
        }
    )
    recipe.update(overrides)
    return recipe


@dataclass
class Sample:
    t_s: float
    unit_state: int
    phase: int
    step: int
    level_pct: float
    flow_m3h: float
    temp_supply_c: float
    temp_return_c: float
    conductivity_return: float
    conductivity_delta: float
    concentration_pct: float
    steam_pct: float
    pump_speed_pct: float
    hold_achieved_s: float
    fresh_water_l: float
    energy_kwh: float
    residual_g_m2: float


@dataclass
class CycleResult:
    recipe: str
    scenario: str
    completed: bool
    aborted: bool
    verified_clean: bool
    fail_code: str
    batch_id: int
    cycle_time_s: float
    fresh_water_l: float
    recovered_water_l: float
    effluent_l: float
    caustic_l: float
    acid_l: float
    energy_kwh: float
    residual_g_m2: float
    residual_chemical_ppm: float
    residual_breakdown: Dict[str, float] = field(default_factory=dict)
    phases: List[Dict[str, Any]] = field(default_factory=list)
    alarms: List[Dict[str, Any]] = field(default_factory=list)
    plant_totals: Dict[str, float] = field(default_factory=dict)
    scans: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CipCell:
    def __init__(
        self,
        recipe: Dict[str, Any],
        scenario: SoilScenario = SCENARIOS["medium"],
        params: PlantParams = DEFAULT_PARAMS,
        faults: Optional[Faults] = None,
        plc_dir: str = PLC_DIR,
        sample_every_s: float = 5.0,
        external_commands: bool = False,
        backend: Optional[str] = None,
    ):
        # CIP_BACKEND=rusty runs anything built on CipCell, the whole slow test
        # suite included, against the compiled program instead.
        backend = backend or os.environ.get("CIP_BACKEND", "interpreter")
        self.backend = backend
        self.runtime: Union[Runtime, Any] = make_runtime(backend, plc_dir)
        self.io_map: IoMap = build_io_map(self.runtime)
        self.plant = CipPlant(params, scenario, faults)
        self.recipe = recipe
        self.scenario = scenario
        # C_CYCLE_S is a REAL. Compiled, that is 0.2000000030; the harness runs
        # the period the constant means, not its single-precision image.
        self.scan_s = round(float(self.runtime.get_path("C_CYCLE_S")), 6)
        self.outputs = Outputs()
        self.samples: List[Sample] = []
        self._sample_every = max(int(round(sample_every_s / self.scan_s)), 1)
        self._commands = dict.fromkeys(COMMAND_TAGS, False)
        self._pulse: Dict[str, int] = {}
        # When something else drives the command coils (the Modbus bridge or
        # the HMI) the harness must not write them every scan or it would
        # stamp on whatever the client just wrote.
        self.external_commands = external_commands
        self._apply_recipe(recipe)

    # -- configuration -----------------------------------------------------

    def _apply_recipe(self, recipe: Dict[str, Any]) -> None:
        rt = self.runtime
        rt.set_path("gRecipe.sName", str(recipe.get("name", "recipe"))[:24])
        for key, member in RECIPE_BINDING.items():
            if key not in recipe:
                continue
            value = recipe[key]
            rt.set_path("gRecipe." + member, float(value) if not isinstance(value, bool) else value)

    # -- operator interface -------------------------------------------------

    def press(self, command: str, scans: int = 1) -> None:
        """Hold a command bit for a few scans, the way a button does."""
        if command not in COMMAND_TAGS:
            raise KeyError(command)
        self._pulse[command] = scans

    def hold_command(self, command: str, value: bool) -> None:
        self._commands[command] = value

    # -- the scan ----------------------------------------------------------

    def step(self) -> None:
        rt = self.runtime
        sensors = self.plant.sensors()
        for tag, value in sensors.items():
            rt.set_path(tag, value)
        if not self.external_commands:
            for command, tag in COMMAND_TAGS.items():
                pressed = self._commands[command]
                if self._pulse.get(command, 0) > 0:
                    pressed = True
                    self._pulse[command] -= 1
                rt.set_path(tag, pressed)

        rt.scan(self.scan_s)

        out = self.outputs
        for tag, attribute in OUTPUT_BINDING.items():
            setattr(out, attribute, rt.get_path(tag))
        self.plant.step(self.scan_s, out)

        if rt.scan_count % self._sample_every == 0:
            self.samples.append(self._sample())

    def _sample(self) -> Sample:
        rt = self.runtime
        return Sample(
            t_s=round(rt.now, 3),
            unit_state=int(rt.get_path("gUnitState")),
            phase=int(rt.get_path("gPhase")),
            step=int(rt.get_path("gPhaseStep")),
            level_pct=round(float(rt.get_path("rLevelBalanceTankPct")), 3),
            flow_m3h=round(float(rt.get_path("rFlowSupplyM3h")), 3),
            temp_supply_c=round(float(rt.get_path("rTempSupplyC")), 3),
            temp_return_c=round(float(rt.get_path("rTempReturnC")), 3),
            conductivity_return=round(float(rt.get_path("rConductivityReturn")), 4),
            conductivity_delta=round(float(rt.get_path("grDeltaConductivity")), 4),
            concentration_pct=round(float(rt.get_path("grConcentrationPct")), 4),
            steam_pct=round(float(rt.get_path("rSteamValvePct")), 2),
            pump_speed_pct=round(float(rt.get_path("rSupplyPumpSpeedPct")), 2),
            hold_achieved_s=round(float(rt.get_path("grHoldAchievedS")), 1),
            fresh_water_l=round(float(rt.get_path("rHmiFreshWaterL")), 2),
            energy_kwh=round(float(rt.get_path("rHmiEnergyKWh")), 4),
            residual_g_m2=round(self.plant.residual_soil_g_m2, 5),
        )

    # -- running a whole cycle ---------------------------------------------

    def run_cycle(self, max_minutes: float = 90.0, start_after_s: float = 2.0) -> CycleResult:
        rt = self.runtime
        max_scans = int(max_minutes * 60.0 / self.scan_s)
        started = False
        for _ in range(max_scans):
            if not started and rt.now >= start_after_s:
                self.press("start")
                started = True
            self.step()
            state = int(rt.get_path("gUnitState"))
            if started and state in (7, 9):  # COMPLETE or ABORTED
                break
        return self.result()

    def result(self) -> CycleResult:
        rt = self.runtime
        state = int(rt.get_path("gUnitState"))
        phases = []
        for index in range(1, 7):
            base = "gResults[{0}].".format(index)
            if not bool(rt.get_path(base + "xExecuted")):
                continue
            phases.append(
                {
                    "index": index,
                    "phase": PHASE_NAMES[int(rt.get_path(base + "ePhase"))],
                    "pass": bool(rt.get_path(base + "xPass")),
                    "fail_code": FAIL_NAMES[int(rt.get_path(base + "eFail"))],
                    "duration_s": round(float(rt.get_path(base + "rDurationS")), 1),
                    "hold_achieved_s": round(float(rt.get_path(base + "rHoldAchievedS")), 1),
                    "hold_required_s": round(float(rt.get_path(base + "rHoldRequiredS")), 1),
                    "endpoint": "conductivity"
                    if bool(rt.get_path(base + "xAdaptiveEnd"))
                    else "verified hold",
                    "min_temp_c": round(float(rt.get_path(base + "rMinTempC")), 2),
                    "max_temp_c": round(float(rt.get_path(base + "rMaxTempC")), 2),
                    "mean_conc_pct": round(float(rt.get_path(base + "rMeanConcPct")), 3),
                    "min_flow_m3h": round(float(rt.get_path(base + "rMinFlow")), 2),
                    "water_l": round(float(rt.get_path(base + "rWaterL")), 1),
                    "chemical_l": round(float(rt.get_path(base + "rChemL")), 2),
                    "energy_kwh": round(float(rt.get_path(base + "rEnergyKWh")), 3),
                    "end_conductivity_ms_cm": round(
                        float(rt.get_path(base + "rEndConductivity")), 4
                    ),
                }
            )

        alarms = []
        for index in range(1, len(ALARM_TAGS) + 1):
            base = "gAlarms[{0}].".format(index)
            if int(rt.get_path(base + "iCount")) == 0:
                continue
            alarms.append(
                {
                    "id": index,
                    "tag": ALARM_TAGS[index],
                    "priority": int(rt.get_path(base + "iPriority")),
                    "count": int(rt.get_path(base + "iCount")),
                    "latched": bool(rt.get_path(base + "xLatched")),
                    "first_in_s": round(float(rt.get_path(base + "rTimeInS")), 1),
                }
            )

        totals = self.plant.totals
        return CycleResult(
            recipe=str(self.recipe.get("name", "recipe")),
            scenario=self.scenario.name,
            completed=state == 7,
            aborted=state == 9,
            verified_clean=bool(rt.get_path("gKpi.xVerifiedClean")),
            fail_code=FAIL_NAMES[int(rt.get_path("gFailCode"))],
            batch_id=int(rt.get_path("giBatchId")),
            cycle_time_s=round(float(rt.get_path("gKpi.rCycleTimeS")), 1),
            fresh_water_l=round(float(rt.get_path("gKpi.rFreshWaterL")), 1),
            recovered_water_l=round(float(rt.get_path("gKpi.rRecoveredWaterL")), 1),
            effluent_l=round(float(rt.get_path("gKpi.rEffluentL")), 1),
            caustic_l=round(float(rt.get_path("gKpi.rCausticL")), 2),
            acid_l=round(float(rt.get_path("gKpi.rAcidL")), 2),
            energy_kwh=round(float(rt.get_path("gKpi.rEnergyKWh")), 3),
            residual_g_m2=round(self.plant.residual_soil_g_m2, 4),
            residual_chemical_ppm=round(self.plant.residual_chemical_ppm, 2),
            residual_breakdown={
                key: round(value, 4)
                for key, value in self.plant.residual_breakdown_g_m2().items()
            },
            phases=phases,
            alarms=alarms,
            plant_totals={
                "fresh_water_l": round(totals.fresh_water_l, 1),
                "recovered_water_l": round(totals.recovered_water_l, 1),
                "effluent_l": round(totals.effluent_l, 1),
                "caustic_stock_l": round(totals.caustic_stock_l, 2),
                "acid_stock_l": round(totals.acid_stock_l, 2),
                "steam_kwh": round(totals.steam_kwh, 3),
            },
            scans=rt.scan_count,
        )

    def samples_as_dicts(self) -> List[Dict[str, Any]]:
        return [asdict(sample) for sample in self.samples]
