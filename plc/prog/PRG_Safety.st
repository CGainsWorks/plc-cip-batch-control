(*
    PRG_Safety.st: interlocks and permissives. First program in the task, so
    every other program sees a permit that was evaluated against this scan's
    inputs rather than the previous one's.

    Nothing here is a substitute for the hard-wired safety circuit. The E-stop
    drops the contactors and the steam solenoid directly; xSafetyOk is only the
    controller's view of that circuit, used to sequence a clean shutdown.

    Two interlocks earn their place:

      * no steam without proven flow. A steam-heated plate pack with no flow
        through it will boil the film against the plate, bake the soil on and
        eventually damage the gaskets.
      * no dosing without a running supply pump and a source that has stock in
        it, so concentrate cannot sit undiluted in the balance tank.
*)

PROGRAM PRG_Safety
VAR CONSTANT
    C_MIN_FLOW_HEAT : REAL := 8.0;      (* m3/h before steam may be admitted *)
    C_DRY_RUN_FLOW  : REAL := 2.0;      (* m3/h with the pump running is a dry run *)
END_VAR
VAR
    fbDryRun        : TON;
    fbCircuitLost   : TON;
    xChemStockOk    : BOOL;
END_VAR

grPlcTimeS := grPlcTimeS + C_CYCLE_S;

(* A running pump moving nothing is either dry or dead-headed. Either way it
   destroys the seal in minutes, so it is a latch-free hard stop. *)
fbDryRun(IN := xSupplyPumpRunning AND (rFlowSupplyM3h < C_DRY_RUN_FLOW), PT := T#20s);

fbCircuitLost(IN := NOT xCircuitAvailable, PT := T#3s);

gxSafetyTrip := (NOT xSafetyOk)
                OR xBalanceTankLowLow
                OR fbDryRun.Q
                OR (gUnitState <> UST_IDLE AND fbCircuitLost.Q);

gxPermitRun := xSafetyOk
               AND (NOT xBalanceTankLowLow)
               AND (NOT fbDryRun.Q)
               AND xCircuitAvailable
               AND (rLevelBalanceTankPct > C_LEVEL_TRIP_PCT);

gxPermitHeat := gxPermitRun
                AND xSteamAvailable
                AND xSupplyPumpRunning
                AND (rFlowSupplyM3h >= C_MIN_FLOW_HEAT);

(* Only the tank the current phase is drawing from has to have stock. *)
CASE gDemand.eSource OF
    SRC_CAUSTIC:
        xChemStockOk := rLevelCausticPct > C_CHEM_LEVEL_MIN;
    SRC_ACID:
        xChemStockOk := rLevelAcidPct > C_CHEM_LEVEL_MIN;
ELSE
    xChemStockOk := TRUE;
END_CASE

gxPermitDose := gxPermitRun AND xSupplyPumpRunning AND xChemStockOk;

END_PROGRAM
