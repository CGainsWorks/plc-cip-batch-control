(*
    PRG_Kpi.st: consumption totals, the batch record and the SCADA image.

    Everything here is computed from instruments the skid actually has: the
    make-up flow meter, the supply flow meter, the two temperature elements
    either side of the heat exchanger and the dosing pump run time. Nothing
    is inferred from the model, so the same code would produce the same batch
    record on real hardware.

    Steam energy is the enthalpy the water gained across the plate pack:

        kW = m3/h * 1000 / 3600 [kg/s] * 4.19 [kJ/kg.K] * (Tsupply - Ttank) [K]

    which under-reports the boiler's fuel by the efficiency of the pack and
    the condensate return. It is the number the site can act on and it is
    the one the utility meter can be reconciled against.
*)

PROGRAM PRG_Kpi
VAR
    fbFresh         : FB_Totaliser;
    fbRecovered     : FB_Totaliser;
    fbEffluent      : FB_Totaliser;
    fbCaustic       : FB_Totaliser;
    fbAcid          : FB_Totaliser;
    fbEnergy        : FB_Totaliser;
    rEnergyKW       : REAL;
    rSnapWater      : REAL;
    rSnapChem       : REAL;
    rSnapEnergy     : REAL;
    i               : INT;
    xAllPass        : BOOL;
    xFinalRinseOk   : BOOL;
    xUnitActive     : BOOL;
END_VAR

xUnitActive := (gUnitState <> UST_IDLE)
               AND (gUnitState <> UST_COMPLETE)
               AND (gUnitState <> UST_ABORTED);

(* ---- instantaneous steam duty ---------------------------------------- *)
IF xSteamIsolation AND (rTempSupplyC > rTempBalanceTankC) THEN
    rEnergyKW := rFlowSupplyM3h * 1000.0 / 3600.0 * 4.19 * (rTempSupplyC - rTempBalanceTankC);
ELSE
    rEnergyKW := 0.0;
END_IF

(* ---- totalisers, all reset together at the start of a batch ------------
    Each is gated on the line-up rather than on a valve command. That
    distinction matters more than it looks.

    Make-up is bang-bang level control on a break tank, so V01 and V02 toggle
    every scan or two. The flow transmitter is read at the top of the scan and
    therefore reports the previous scan's flow, so gating the totaliser on the
    valve command aliases the two signals: on the scans the valve is open the
    meter is still reporting the scan it was shut, so the total comes out
    about a fifth low. The source selection only changes at a phase boundary
    and the meter reads zero when the valve is shut in any case. Gating on
    the source and letting the low-flow cut-off reject the noise measures
    what actually went through the meter.
*)
fbFresh(xEnable := gDemand.eSource = SRC_FRESH,
        rRatePerHour := rFlowMakeupM3h * 1000.0,
        rCutOff := 50.0,
        rCycleS := C_CYCLE_S,
        xReset := gxCycleReset);

fbRecovered(xEnable := gDemand.eSource = SRC_RECOVERED,
            rRatePerHour := rFlowMakeupM3h * 1000.0,
            rCutOff := 50.0,
            rCycleS := C_CYCLE_S,
            xReset := gxCycleReset);

fbEffluent(xEnable := xV06ReturnToDrain,
           rRatePerHour := rFlowSupplyM3h * 1000.0,
           rCutOff := 50.0,
           rCycleS := C_CYCLE_S,
           xReset := gxCycleReset);

fbCaustic(xEnable := xV03CausticDose,
          rRatePerHour := C_CAUSTIC_DOSE_LPM * 60.0,
          rCycleS := C_CYCLE_S,
          xReset := gxCycleReset);

fbAcid(xEnable := xV04AcidDose,
       rRatePerHour := C_ACID_DOSE_LPM * 60.0,
       rCycleS := C_CYCLE_S,
       xReset := gxCycleReset);

fbEnergy(xEnable := xSteamIsolation,
         rRatePerHour := rEnergyKW,
         rCycleS := C_CYCLE_S,
         xReset := gxCycleReset);

gKpi.rFreshWaterL     := fbFresh.rTotal;
gKpi.rRecoveredWaterL := fbRecovered.rTotal;
gKpi.rEffluentL       := fbEffluent.rTotal;
gKpi.rCausticL        := fbCaustic.rTotal;
gKpi.rAcidL           := fbAcid.rTotal;
gKpi.rEnergyKWh       := fbEnergy.rTotal;

IF gxCycleReset THEN
    grCycleElapsedS := 0.0;
    gKpi.xVerifiedClean := FALSE;
    rSnapWater := 0.0;
    rSnapChem := 0.0;
    rSnapEnergy := 0.0;
END_IF

IF xUnitActive THEN
    grCycleElapsedS := grCycleElapsedS + C_CYCLE_S;
END_IF
gKpi.rCycleTimeS := grCycleElapsedS;

(* ---- per-phase consumption, by difference ----------------------------- *)
IF gxPhaseStartPulse THEN
    rSnapWater  := fbFresh.rTotal + fbRecovered.rTotal;
    rSnapChem   := fbCaustic.rTotal + fbAcid.rTotal;
    rSnapEnergy := fbEnergy.rTotal;
END_IF

IF gxPhaseEndPulse AND (giPhaseIndex >= 1) AND (giPhaseIndex <= C_PHASE_COUNT) THEN
    gResults[giPhaseIndex].rWaterL := fbFresh.rTotal + fbRecovered.rTotal - rSnapWater;
    gResults[giPhaseIndex].rChemL := fbCaustic.rTotal + fbAcid.rTotal - rSnapChem;
    gResults[giPhaseIndex].rEnergyKWh := fbEnergy.rTotal - rSnapEnergy;
END_IF

(* ---- release criteria -------------------------------------------------- *)
(* A batch is only released if every phase that ran met its own criteria and
   the final rinse came back as clean as the water going in. A fixed-time
   recipe can complete without satisfying the second test and that is the
   whole argument for the adaptive one. *)
xAllPass := TRUE;
FOR i := 1 TO C_PHASE_COUNT DO
    IF gResults[i].xExecuted AND NOT gResults[i].xPass THEN
        xAllPass := FALSE;
    END_IF
END_FOR

xFinalRinseOk := gResults[5].xExecuted
                 AND gResults[5].xPass
                 AND (gResults[5].rEndConductivity <= gRecipe.rRinseEndCond);

IF gUnitState = UST_COMPLETE THEN
    gKpi.xVerifiedClean := xAllPass AND xFinalRinseOk;
END_IF

(* ---- SCADA image ------------------------------------------------------- *)
iHmiUnitState        := TO_INT(gUnitState);
iHmiPhase            := TO_INT(gPhase);
iHmiPhaseStep        := TO_INT(gPhaseStep);
iHmiFailCode         := TO_INT(gFailCode);
iHmiActiveAlarms     := giActiveAlarms;
iHmiFirstOutAlarm    := giFirstOut;
iHmiBatchId          := giBatchId;

rHmiFreshWaterL      := gKpi.rFreshWaterL;
rHmiRecoveredWaterL  := gKpi.rRecoveredWaterL;
rHmiEffluentL        := gKpi.rEffluentL;
rHmiCausticL         := gKpi.rCausticL;
rHmiAcidL            := gKpi.rAcidL;
rHmiEnergyKWh        := gKpi.rEnergyKWh;
rHmiCycleTimeS       := gKpi.rCycleTimeS;
rHmiPhaseElapsedS    := grPhaseElapsedS;
rHmiHoldAchievedS    := grHoldAchievedS;
rHmiConcentrationPct := grConcentrationPct;

xHmiVerifiedClean    := gKpi.xVerifiedClean;
xHmiCycleComplete    := gUnitState = UST_COMPLETE;
xHmiCycleAborted     := gUnitState = UST_ABORTED;
xHmiHeating          := xSteamIsolation;

END_PROGRAM
