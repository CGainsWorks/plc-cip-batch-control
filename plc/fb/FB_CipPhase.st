(*
    FB_CipPhase.st -- the phase engine.

    Every CIP phase is the same four steps with different parameters, so there
    is one engine and the recipe supplies ST_PhaseParams:

        LINEUP     set the valve matrix, prove the circuit is released
        CHARGE     fill the balance tank from the source, dose to strength
        CIRCULATE  hold the wash at temperature and flow, or rinse to a
                   conductivity endpoint
        RECOVER    push the circuit contents back to the source tank or to
                   drain, then stop

    The important idea is in CIRCULATE. The hold timer only advances while
    temperature, flow and concentration are all inside their limits, so ten
    minutes of "hold" means ten minutes of cleaning that actually met the
    specification, not ten minutes on the clock. That accumulated figure is
    what goes in the batch record.
*)

FUNCTION_BLOCK FB_CipPhase
VAR_INPUT
    xExecute    : BOOL;
    xHolding    : BOOL;
    ePhase      : E_Phase;
    stParams    : ST_PhaseParams;
    rTempC      : REAL;     (* return temperature: the coldest point of the circuit *)
    rFlowM3h    : REAL;
    rConcPct    : REAL;
    rDeltaCond  : REAL;     (* return minus supply conductivity, mS/cm *)
    rLevelPct   : REAL;
    xPumpHealthy : BOOL := TRUE;
    rCycleS     : REAL := 0.1;
END_VAR
VAR_OUTPUT
    eStep       : E_PhaseStep;
    stDemand    : ST_Demand;
    xDone       : BOOL;
    xFailed     : BOOL;
    eFail       : E_FailCode;
    rElapsedS   : REAL;
    rHoldAchievedS : REAL;
    stResult    : ST_PhaseResult;
    xInSpec     : BOOL;     (* all cleaning criteria met right now *)
END_VAR
VAR
    fbStart         : R_TRIG;
    fbEndStable     : TON;
    fbFlowLow       : TON;
    fbTempSlow      : TON;
    rStepElapsedS   : REAL;
    rConcSum        : REAL;
    rConcSamples    : REAL;
    rHoldRequiredS  : REAL;
    rMinPhaseS      : REAL;
    rMaxPhaseS      : REAL;
    rRecoverS       : REAL;
    rFillTargetPct  : REAL;
    xSinglePass     : BOOL;
    eActiveReturn   : E_Return;
    xCharged        : BOOL;
    xTempOk         : BOOL;
    xFlowOk         : BOOL;
    xConcOk         : BOOL;
    xDoseDemand     : BOOL;
END_VAR

fbStart(CLK := xExecute);

(* ---- initialise on the rising edge of the execute request ------------- *)
IF fbStart.Q THEN
    eStep := PS_LINEUP;
    rElapsedS := 0.0;
    rStepElapsedS := 0.0;
    rHoldAchievedS := 0.0;
    rConcSum := 0.0;
    rConcSamples := 0.0;
    xCharged := FALSE;
    xDone := FALSE;
    xFailed := FALSE;
    eFail := FC_NONE;
    stResult.ePhase := ePhase;
    stResult.xExecuted := TRUE;
    stResult.xPass := FALSE;
    stResult.eFail := FC_NONE;
    stResult.rDurationS := 0.0;
    stResult.rHoldAchievedS := 0.0;
    stResult.rMinTempC := 999.0;
    stResult.rMaxTempC := -999.0;
    stResult.rMinFlow := 999.0;
    stResult.rMeanConcPct := 0.0;
    stResult.rEndConductivity := 0.0;
    stResult.xAdaptiveEnd := FALSE;
    stResult.rWaterL := 0.0;
    stResult.rChemL := 0.0;
    stResult.rEnergyKWh := 0.0;
END_IF

rHoldRequiredS := TIME_TO_REAL(stParams.tHold) / 1000.0;
rMinPhaseS     := TIME_TO_REAL(stParams.tMinPhase) / 1000.0;
rMaxPhaseS     := TIME_TO_REAL(stParams.tMaxPhase) / 1000.0;
rRecoverS      := TIME_TO_REAL(stParams.tRecover) / 1000.0;

(* An adaptive phase is judged on the conductivity endpoint, so the hold
   requirement does not apply and the record should not imply that it does. *)
stResult.xAdaptiveEnd := stParams.xAdaptiveEnd;
IF stParams.xAdaptiveEnd THEN
    stResult.rHoldRequiredS := 0.0;
ELSE
    stResult.rHoldRequiredS := rHoldRequiredS;
END_IF

(* A recirculating phase makes its charge up in a closed loop through the
   balance tank and only sends the liquid to eReturn when it recovers at the
   end. A single-pass phase runs from the source straight to eReturn, on a
   small working volume so the tank washes out in about a minute. *)
xSinglePass := NOT stParams.xRecirculate;
IF xSinglePass THEN
    rFillTargetPct := C_RINSE_LEVEL_PCT;
ELSE
    rFillTargetPct := C_FILL_TARGET_PCT;
END_IF

IF stParams.xRecirculate AND (eStep <> PS_RECOVER) THEN
    eActiveReturn := RET_BALANCE;
ELSE
    eActiveReturn := stParams.eReturn;
END_IF

(* ---- idle: hold everything closed ------------------------------------ *)
IF NOT xExecute THEN
    stDemand.eSource := SRC_NONE;
    stDemand.eReturn := RET_DRAIN;
    stDemand.rFlowSp := 0.0;
    stDemand.rTempSp := 0.0;
    stDemand.xHeat := FALSE;
    stDemand.xDose := FALSE;
    stDemand.xSupplyPump := FALSE;
    stDemand.xReturnPump := FALSE;
    stDemand.xFillValve := FALSE;
    xInSpec := FALSE;
    RETURN;
END_IF

(* ---- criteria evaluated once, used by several steps ------------------- *)
xTempOk := (NOT stParams.xHeat) OR (rTempC >= stParams.rTempMin);
xFlowOk := rFlowM3h >= stParams.rFlowMin;
xConcOk := (NOT stParams.xDose) OR
           ((rConcPct >= stParams.rConcMin) AND (rConcPct <= stParams.rConcMax));
xInSpec := xTempOk AND xFlowOk AND xConcOk;

(* Dose whenever the strength has drifted below setpoint. Above the band the
   valve simply stays shut; the phase dilutes back with the next charge. *)
xDoseDemand := stParams.xDose AND (rConcPct < stParams.rConcSp);

(* ---- the line-up is common to every running step ---------------------- *)
stDemand.eSource := stParams.eSource;
stDemand.eReturn := eActiveReturn;
stDemand.rTempSp := stParams.rTempSp;
stDemand.rFlowSp := stParams.rFlowSp;

IF xHolding THEN
    (* Freeze: keep the valve matrix so the circuit stays full but stop
       circulating, stop heating and stop dosing. Accumulators do not advance,
       so a held phase does not earn hold time. *)
    stDemand.xSupplyPump := FALSE;
    stDemand.xReturnPump := TRUE;
    stDemand.xHeat := FALSE;
    stDemand.xDose := FALSE;
    stDemand.xFillValve := FALSE;
    RETURN;
END_IF

rElapsedS := rElapsedS + rCycleS;
rStepElapsedS := rStepElapsedS + rCycleS;
stResult.rDurationS := rElapsedS;

(* ---- the step machine ------------------------------------------------- *)
CASE eStep OF

    PS_LINEUP:
        stDemand.xSupplyPump := FALSE;
        stDemand.xReturnPump := TRUE;
        stDemand.xHeat := FALSE;
        stDemand.xDose := FALSE;
        stDemand.xFillValve := TRUE;
        IF rStepElapsedS >= 5.0 THEN
            eStep := PS_CHARGE;
            rStepElapsedS := 0.0;
        ELSIF rStepElapsedS > 60.0 THEN
            eFail := FC_LINEUP_TIMEOUT;
        END_IF

    PS_CHARGE:
        stDemand.xReturnPump := TRUE;
        stDemand.xFillValve := rLevelPct < rFillTargetPct;
        (* Start circulating as soon as there is enough suction head; the
           remaining fill and the dose trim happen while the pump runs, which
           is also what satisfies the no-dosing-without-flow interlock. *)
        stDemand.xSupplyPump := rLevelPct >= C_PUMP_START_PCT;
        stDemand.xHeat := stParams.xHeat AND stDemand.xSupplyPump;
        stDemand.xDose := xDoseDemand AND stDemand.xSupplyPump;
        xCharged := (rLevelPct >= rFillTargetPct) AND xConcOk;
        IF xCharged THEN
            eStep := PS_CIRCULATE;
            rStepElapsedS := 0.0;
        ELSIF rStepElapsedS > 600.0 THEN
            eFail := FC_CHARGE_TIMEOUT;
        END_IF

    PS_CIRCULATE:
        stDemand.xSupplyPump := TRUE;
        stDemand.xReturnPump := TRUE;
        stDemand.xHeat := stParams.xHeat;
        stDemand.xDose := xDoseDemand;
        (* Single-pass phases keep drawing fresh liquid; recirculating phases
           only top up if the circuit has swallowed some of the charge. *)
        stDemand.xFillValve := rLevelPct < rFillTargetPct;

        IF xInSpec THEN
            rHoldAchievedS := rHoldAchievedS + rCycleS;
        END_IF
        stResult.rHoldAchievedS := rHoldAchievedS;

        (* Statistics start once the loop has settled, otherwise every phase
           records the pump ramp as its minimum flow. *)
        IF rStepElapsedS > 15.0 THEN
            IF rTempC < stResult.rMinTempC THEN
                stResult.rMinTempC := rTempC;
            END_IF
            IF rTempC > stResult.rMaxTempC THEN
                stResult.rMaxTempC := rTempC;
            END_IF
            IF rFlowM3h < stResult.rMinFlow THEN
                stResult.rMinFlow := rFlowM3h;
            END_IF
            rConcSum := rConcSum + rConcPct;
            rConcSamples := rConcSamples + 1.0;
            stResult.rMeanConcPct := rConcSum / rConcSamples;
        END_IF
        stResult.rEndConductivity := rDeltaCond;

        (* Endpoint. An adaptive rinse stops when the return water is as clean
           as the water going in; everything else stops on verified hold time. *)
        fbEndStable(IN := stParams.xAdaptiveEnd AND (rDeltaCond <= stParams.rEndConductivity)
                          AND (rElapsedS >= rMinPhaseS),
                    PT := T#10s);

        IF stParams.xAdaptiveEnd THEN
            IF fbEndStable.Q THEN
                eStep := PS_RECOVER;
                rStepElapsedS := 0.0;
            ELSIF rElapsedS >= rMaxPhaseS THEN
                eFail := FC_RINSE_NOT_CLEAN;
            END_IF
        ELSE
            IF rHoldAchievedS >= rHoldRequiredS THEN
                eStep := PS_RECOVER;
                rStepElapsedS := 0.0;
            ELSIF rElapsedS >= rMaxPhaseS THEN
                eFail := FC_PHASE_TIMEOUT;
            END_IF
        END_IF

        (* Sustained excursions are faults, not just missing hold time. *)
        fbFlowLow(IN := NOT xFlowOk, PT := T#60s);
        IF fbFlowLow.Q THEN
            eFail := FC_FLOW_LOW;
        END_IF
        fbTempSlow(IN := stParams.xHeat AND NOT xTempOk, PT := T#8m);
        IF fbTempSlow.Q THEN
            eFail := FC_TEMP_TIMEOUT;
        END_IF
        IF rLevelPct < C_LEVEL_TRIP_PCT THEN
            eFail := FC_LEVEL_LOW;
        END_IF

    PS_RECOVER:
        (* Push the contents back where they came from. Heat and dose are off
           and the fill valve is shut, so the balance tank drains as the pump
           empties it into the return line. Draining it properly is what keeps
           the next phase's charge from being diluted by the last one's
           chemical and what keeps the following rinse short. *)
        stDemand.xSupplyPump := (rStepElapsedS < rRecoverS)
                                AND (rLevelPct > C_PUMP_START_PCT);
        stDemand.xReturnPump := TRUE;
        stDemand.xHeat := FALSE;
        stDemand.xDose := FALSE;
        stDemand.xFillValve := FALSE;
        IF (rStepElapsedS >= rRecoverS) OR (rLevelPct <= C_PUMP_START_PCT) THEN
            eStep := PS_DONE;
            rStepElapsedS := 0.0;
        END_IF

    PS_DONE:
        stDemand.xSupplyPump := FALSE;
        stDemand.xReturnPump := FALSE;
        stDemand.xHeat := FALSE;
        stDemand.xDose := FALSE;
        stDemand.xFillValve := FALSE;
        stDemand.eSource := SRC_NONE;
        xDone := TRUE;
        stResult.xPass := TRUE;
END_CASE

IF NOT xPumpHealthy THEN
    eFail := FC_PUMP_FAULT;
END_IF

IF eFail <> FC_NONE THEN
    xFailed := TRUE;
    xDone := FALSE;
    eStep := PS_DONE;
    stResult.xPass := FALSE;
    stResult.eFail := eFail;
    stDemand.xSupplyPump := FALSE;
    stDemand.xReturnPump := FALSE;
    stDemand.xHeat := FALSE;
    stDemand.xDose := FALSE;
    stDemand.xFillValve := FALSE;
END_IF

END_FUNCTION_BLOCK
