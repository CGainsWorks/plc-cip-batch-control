(*
    PRG_Sequence.st: the ISA-88 unit procedure.

    This program owns the unit state machine and turns the recipe into phase
    parameters. It does not touch a single output. It produces a demand;
    PRG_Control decides whether the interlocks let that demand reach the
    field. Keeping the two apart is what makes the sequence readable and the
    interlocks auditable.

    Phase order is fixed; the recipe chooses which phases are in it:

        PRE_RINSE -> CAUSTIC -> INTERMEDIATE RINSE -> [ACID] ->
        FINAL RINSE -> [SANITISE] -> COMPLETE
*)

(*
    How long a rinse must run before its conductivity endpoint means anything.

    The return element sits downstream of the whole circuit, so a rinse that
    stops after twenty seconds has only measured the water that was already in
    the pipe. The floor is one and a half circuit turnovers at the recipe flow
    (135 seconds for a 450 litre circuit at 18 m3/h) or the recipe's own
    minimum, whichever is longer.
*)
FUNCTION RinseBlankTime : TIME
VAR_INPUT
    tFloor      : TIME;
    rFlowSpM3h  : REAL;
END_VAR
VAR
    rFlowLps    : REAL;
    rTurnoverS  : REAL;
END_VAR

rFlowLps := MAX(rFlowSpM3h, 1.0) * 1000.0 / 3600.0;
rTurnoverS := C_RINSE_TURNOVERS * C_CIRCUIT_VOLUME_L / rFlowLps;
RinseBlankTime := SecondsToTime(MAX(TimeToSeconds(tFloor), rTurnoverS));

END_FUNCTION


FUNCTION NextPhase : E_Phase
VAR_INPUT
    eCurrent     : E_Phase;
    xUseAcid     : BOOL;
    xUseSanitise : BOOL;
END_VAR

CASE eCurrent OF
    PH_IDLE:
        NextPhase := PH_PRE_RINSE;
    PH_PRE_RINSE:
        NextPhase := PH_CAUSTIC_WASH;
    PH_CAUSTIC_WASH:
        NextPhase := PH_INTERMEDIATE_RINSE;
    PH_INTERMEDIATE_RINSE:
        IF xUseAcid THEN
            NextPhase := PH_ACID_WASH;
        ELSE
            NextPhase := PH_FINAL_RINSE;
        END_IF
    PH_ACID_WASH:
        NextPhase := PH_FINAL_RINSE;
    PH_FINAL_RINSE:
        IF xUseSanitise THEN
            NextPhase := PH_SANITISE;
        ELSE
            NextPhase := PH_COMPLETE;
        END_IF
    PH_SANITISE:
        NextPhase := PH_COMPLETE;
ELSE
    NextPhase := PH_COMPLETE;
END_CASE

END_FUNCTION


PROGRAM PRG_Sequence
VAR
    fbUnit          : FB_UnitStateMachine;
    fbPhase         : FB_CipPhase;
    iPhaseIndex     : INT;
    iResultIndex    : INT;
    xReadyToStart   : BOOL;
    xProcComplete   : BOOL;
    xProcFailed     : BOOL;
    xPhaseGap       : BOOL;     (* one scan between phases, re-arms the engine *)
    xPhaseActive    : BOOL;     (* running or held: the engine keeps its state *)
    xExecutePhase   : BOOL;     (* running: accumulators and alarms advance *)
    ePrevPhase      : E_Phase;
END_VAR

(* ---------------------------------------------------------------------- *)
(* 1. Unit state machine                                                    *)
(* ---------------------------------------------------------------------- *)
xReadyToStart := (NOT gxSafetyTrip) AND xCircuitAvailable AND (NOT gxAlarmP1Active);

fbUnit(xStart              := xCmdStart,
       xHold               := xCmdHold,
       xRestart            := xCmdRestart,
       xAbort              := xCmdAbort,
       xReset              := xCmdReset,
       xReadyToStart       := xReadyToStart,
       xProcedureComplete  := xProcComplete,
       xProcedureFailed    := xProcFailed,
       xSafetyTrip         := gxSafetyTrip);

gUnitState := fbUnit.eState;

(* ---------------------------------------------------------------------- *)
(* 2. Start of batch                                                        *)
(* ---------------------------------------------------------------------- *)
gxCycleReset := FALSE;
IF fbUnit.xStartPulse THEN
    giBatchId := giBatchId + 1;
    gPhase := PH_PRE_RINSE;
    gFailCode := FC_NONE;
    xProcComplete := FALSE;
    xProcFailed := FALSE;
    xPhaseGap := FALSE;
    gxCycleReset := TRUE;
    gxCondSensorFault := FALSE;
    gxCondProven := FALSE;
    FOR iPhaseIndex := 1 TO C_PHASE_COUNT DO
        gResults[iPhaseIndex].ePhase := PH_IDLE;
        gResults[iPhaseIndex].xExecuted := FALSE;
        gResults[iPhaseIndex].xPass := FALSE;
        gResults[iPhaseIndex].eFail := FC_NONE;
        gResults[iPhaseIndex].rDurationS := 0.0;
        gResults[iPhaseIndex].rHoldAchievedS := 0.0;
        gResults[iPhaseIndex].rWaterL := 0.0;
        gResults[iPhaseIndex].rChemL := 0.0;
        gResults[iPhaseIndex].rEnergyKWh := 0.0;
    END_FOR
END_IF

IF fbUnit.eState = UST_IDLE THEN
    gPhase := PH_IDLE;
    gFailCode := FC_NONE;
END_IF

(* ---------------------------------------------------------------------- *)
(* 3. Phase parameters from the recipe                                      *)
(* ---------------------------------------------------------------------- *)
gPhaseParams.rFlowSp  := gRecipe.rFlowSp;
gPhaseParams.rFlowMin := gRecipe.rFlowMin;
gPhaseParams.tMaxPhase := gRecipe.tPhaseMax;
gPhaseParams.tMinPhase := T#30s;
gPhaseParams.xAdaptiveEnd := FALSE;
gPhaseParams.rEndConductivity := gRecipe.rRinseEndCond;
gPhaseParams.rConcSp := 0.0;
gPhaseParams.rConcMin := 0.0;
gPhaseParams.rConcMax := 0.0;
gPhaseParams.rTempSp := 0.0;
gPhaseParams.rTempMin := 0.0;
gPhaseParams.xHeat := FALSE;
gPhaseParams.xDose := FALSE;
gPhaseParams.xRecirculate := FALSE;

CASE gPhase OF

    PH_PRE_RINSE:
        (* Recovered final-rinse water if there is any, mains if not. This one
           line is most of the water saving: the pre-rinse is the biggest
           single-pass consumer in the cycle. *)
        IF rLevelRecoveryPct > 25.0 THEN
            gPhaseParams.eSource := SRC_RECOVERED;
        ELSE
            gPhaseParams.eSource := SRC_FRESH;
        END_IF
        gPhaseParams.eReturn := RET_DRAIN;
        gPhaseParams.tHold := gRecipe.tPreRinse;
        gPhaseParams.tMinPhase := RinseBlankTime(gRecipe.tRinseMin, gRecipe.rFlowSp);
        gPhaseParams.tRecover := T#20s;
        gPhaseParams.tMaxPhase := gRecipe.tRinseMax;
        gPhaseParams.xAdaptiveEnd := gRecipe.xAdaptive;
        (* Gross soil removal: a looser endpoint than the final rinse. *)
        gPhaseParams.rEndConductivity := gRecipe.rRinseEndCond * 3.0;

    PH_CAUSTIC_WASH:
        gPhaseParams.eSource := SRC_CAUSTIC;
        gPhaseParams.eReturn := RET_CAUSTIC;
        gPhaseParams.xRecirculate := TRUE;
        gPhaseParams.rTempSp := gRecipe.rCausticTempSp;
        gPhaseParams.rTempMin := gRecipe.rCausticTempMin;
        gPhaseParams.xHeat := TRUE;
        gPhaseParams.rConcSp := gRecipe.rCausticConcSp;
        gPhaseParams.rConcMin := gRecipe.rCausticConcSp - 0.25;
        gPhaseParams.rConcMax := gRecipe.rCausticConcSp + 0.50;
        gPhaseParams.xDose := TRUE;
        gPhaseParams.tHold := gRecipe.tCausticHold;
        gPhaseParams.tMinPhase := T#1m;
        gPhaseParams.tRecover := T#180s;

    PH_INTERMEDIATE_RINSE:
        gPhaseParams.eSource := SRC_FRESH;
        gPhaseParams.eReturn := RET_DRAIN;
        gPhaseParams.tHold := gRecipe.tIntRinse;
        gPhaseParams.tMinPhase := RinseBlankTime(gRecipe.tRinseMin, gRecipe.rFlowSp);
        gPhaseParams.tRecover := T#20s;
        gPhaseParams.tMaxPhase := gRecipe.tRinseMax;
        gPhaseParams.xAdaptiveEnd := gRecipe.xAdaptive;
        gPhaseParams.rEndConductivity := gRecipe.rRinseEndCond * 2.0;

    PH_ACID_WASH:
        gPhaseParams.eSource := SRC_ACID;
        gPhaseParams.eReturn := RET_ACID;
        gPhaseParams.xRecirculate := TRUE;
        gPhaseParams.rTempSp := gRecipe.rAcidTempSp;
        gPhaseParams.rTempMin := gRecipe.rAcidTempMin;
        gPhaseParams.xHeat := TRUE;
        gPhaseParams.rConcSp := gRecipe.rAcidConcSp;
        gPhaseParams.rConcMin := gRecipe.rAcidConcSp - 0.15;
        gPhaseParams.rConcMax := gRecipe.rAcidConcSp + 0.35;
        gPhaseParams.xDose := TRUE;
        gPhaseParams.tHold := gRecipe.tAcidHold;
        gPhaseParams.tMinPhase := T#1m;
        gPhaseParams.tRecover := T#180s;

    PH_FINAL_RINSE:
        gPhaseParams.eSource := SRC_FRESH;
        gPhaseParams.eReturn := RET_RECOVERY;
        gPhaseParams.tHold := gRecipe.tFinalRinse;
        gPhaseParams.tMinPhase := RinseBlankTime(gRecipe.tRinseMin, gRecipe.rFlowSp);
        gPhaseParams.tRecover := T#30s;
        gPhaseParams.tMaxPhase := gRecipe.tRinseMax;
        gPhaseParams.xAdaptiveEnd := gRecipe.xAdaptive;
        gPhaseParams.rEndConductivity := gRecipe.rRinseEndCond;

    PH_SANITISE:
        gPhaseParams.eSource := SRC_FRESH;
        gPhaseParams.eReturn := RET_BALANCE;
        gPhaseParams.xRecirculate := TRUE;
        gPhaseParams.rTempSp := gRecipe.rSanitiseTempSp;
        gPhaseParams.rTempMin := gRecipe.rSanitiseTempMin;
        gPhaseParams.xHeat := TRUE;
        gPhaseParams.tHold := gRecipe.tSanitiseHold;
        gPhaseParams.tMinPhase := T#1m;
        gPhaseParams.tRecover := T#30s;

ELSE
    gPhaseParams.eSource := SRC_NONE;
    gPhaseParams.eReturn := RET_DRAIN;
    gPhaseParams.tHold := T#0s;
    gPhaseParams.tRecover := T#0s;
END_CASE

(* ---------------------------------------------------------------------- *)
(* 3b. Derived measurements                                                 *)
(* ---------------------------------------------------------------------- *)
(* Both are computed here rather than in PRG_Control so the phase engine sees
   this scan's values instead of the previous scan's. The transmitter already
   compensates to 25 degC, so the calibration is a straight division. *)
grDeltaConductivity := rConductivityReturn - rConductivitySupply;

(* Strength is measured in the balance tank, not on the return line: during
   the charge step there is no established return flow to read. *)
CASE gPhaseParams.eSource OF
    SRC_CAUSTIC:
        grConcentrationPct := rConductivityTank / C_COND_K_CAUSTIC;
        grCondFloor := 0.5 * gPhaseParams.rConcSp * C_COND_K_CAUSTIC;
    SRC_ACID:
        grConcentrationPct := rConductivityTank / C_COND_K_ACID;
        grCondFloor := 0.5 * gPhaseParams.rConcSp * C_COND_K_ACID;
ELSE
    grConcentrationPct := 0.0;
    grCondFloor := 0.0;
END_CASE

(* ---------------------------------------------------------------------- *)
(* 4. Run the phase engine                                                  *)
(* ---------------------------------------------------------------------- *)
(* The engine stays executing through a hold so that it keeps its step, its
   elapsed time and its verified hold time. Dropping xExecute instead would
   look like the end of the phase and the restart would look like a fresh
   rising edge and begin the phase again from zero. *)
xPhaseActive := (fbUnit.xExecute OR fbUnit.xHolding)
                AND (gPhase <> PH_IDLE)
                AND (gPhase <> PH_COMPLETE)
                AND (NOT xPhaseGap);
xExecutePhase := xPhaseActive AND (NOT fbUnit.xHolding);

fbPhase(xExecute     := xPhaseActive,
        xHolding     := fbUnit.xHolding,
        ePhase       := gPhase,
        stParams     := gPhaseParams,
        rTempC       := rTempReturnC,
        rFlowM3h     := rFlowSupplyM3h,
        rConcPct     := grConcentrationPct,
        rDeltaCond   := grDeltaConductivity,
        rLevelPct    := rLevelBalanceTankPct,
        xPumpHealthy := NOT xSupplyPumpFault,
        rCycleS      := C_CYCLE_S);

gPhaseStep := fbPhase.eStep;
gDemand := fbPhase.stDemand;
grPhaseElapsedS := fbPhase.rElapsedS;
grHoldAchievedS := fbPhase.rHoldAchievedS;
gxPhaseExecute := xExecutePhase;
gxPhaseDone := fbPhase.xDone;
gxPhaseFailed := fbPhase.xFailed;

(*
    Plausibility check on the return conductivity element.

    Every rinse endpoint rests on this one transmitter; so does the
    release decision. The dangerous failure is not a dead reading but a
    reading stuck at something clean: an endpoint satisfied before the
    rinse began would pass a dirty circuit.

    There is one point in the cycle where the process guarantees a large
    signal. Circulating a wash at strength, the return line is full of
    detergent, so the element must at some point read at least half the
    expected value. A wash that finishes without it having done so means
    the element is not reading the process. Nothing may be released on its
    word.

    Written as a latch rather than a timer so that it holds for any hold
    time: a two minute wash proves the element as well as a twenty minute
    one. Both washes precede both rinses, so the check always runs before
    an endpoint is trusted.
*)
IF gxPhaseExecute
   AND (gPhaseStep = PS_CIRCULATE)
   AND (grCondFloor > 0.0)
   AND (rConductivityReturn >= grCondFloor) THEN
    gxCondProven := TRUE;
END_IF

(* ---------------------------------------------------------------------- *)
(* 5. Phase transitions and the batch record                                *)
(* ---------------------------------------------------------------------- *)
gxPhaseStartPulse := FALSE;
gxPhaseEndPulse := FALSE;
xPhaseGap := FALSE;

IF gPhase <> ePrevPhase THEN
    ePrevPhase := gPhase;
    IF (gPhase <> PH_IDLE) AND (gPhase <> PH_COMPLETE) THEN
        gxPhaseStartPulse := TRUE;
        gxCondProven := FALSE;
    END_IF
END_IF

iResultIndex := TO_INT(gPhase);

IF xExecutePhase AND fbPhase.xDone THEN
    gResults[iResultIndex] := fbPhase.stResult;
    gxPhaseEndPulse := TRUE;
    giPhaseIndex := iResultIndex;
    gPhase := NextPhase(gPhase, gRecipe.xUseAcid, gRecipe.xUseSanitise);
    xPhaseGap := TRUE;
END_IF

IF gxPhaseEndPulse AND (grCondFloor > 0.0) AND (NOT gxCondProven) THEN
    gxCondSensorFault := TRUE;
END_IF

IF gxCondSensorFault AND (gFailCode = FC_NONE) THEN
    gFailCode := FC_SENSOR_FAULT;
    xProcFailed := TRUE;
END_IF

IF xExecutePhase AND fbPhase.xFailed THEN
    gResults[iResultIndex] := fbPhase.stResult;
    gxPhaseEndPulse := TRUE;
    giPhaseIndex := iResultIndex;
    gFailCode := fbPhase.eFail;
    xProcFailed := TRUE;
END_IF

IF (gPhase = PH_COMPLETE) AND (fbUnit.eState = UST_RUNNING) THEN
    xProcComplete := TRUE;
END_IF

IF fbUnit.xAborting AND (gFailCode = FC_NONE) THEN
    IF gxSafetyTrip THEN
        gFailCode := FC_SAFETY_TRIP;
    ELSE
        gFailCode := FC_OPERATOR_ABORT;
    END_IF
END_IF

IF fbUnit.eState = UST_ABORTED THEN
    xProcFailed := FALSE;    (* consumed; the state machine is already there *)
END_IF

(* Momentary commands. The edges were taken at the top of this program, so the
   bits are cleared here and a client only ever has to set one. *)
xCmdStart := FALSE;
xCmdHold := FALSE;
xCmdRestart := FALSE;
xCmdAbort := FALSE;
xCmdReset := FALSE;

END_PROGRAM
