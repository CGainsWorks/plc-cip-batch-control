(*
    Types_Cip.st: data types for the CIP skid controller.

    Naming follows the usual IEC prefixes: x BOOL, i INT, r REAL, t TIME,
    s STRING, e enumeration, st structure, fb function block instance.

    Enumeration members are prefixed because IEC 61131-3 puts them in the
    global namespace and PH_IDLE and UST_IDLE are different things.
*)

TYPE
    (* ISA-88 procedural state model, trimmed to the states this unit uses. *)
    E_UnitState : (
        UST_IDLE        := 0,
        UST_STARTING    := 1,
        UST_RUNNING     := 2,
        UST_HOLDING     := 3,
        UST_HELD        := 4,
        UST_RESTARTING  := 5,
        UST_COMPLETING  := 6,
        UST_COMPLETE    := 7,
        UST_ABORTING    := 8,
        UST_ABORTED     := 9
    );

    (* The CIP procedure. Phases run in this order; the recipe may skip some. *)
    E_Phase : (
        PH_IDLE                := 0,
        PH_PRE_RINSE           := 1,
        PH_CAUSTIC_WASH        := 2,
        PH_INTERMEDIATE_RINSE  := 3,
        PH_ACID_WASH           := 4,
        PH_FINAL_RINSE         := 5,
        PH_SANITISE            := 6,
        PH_COMPLETE            := 7
    );

    (* Every phase runs the same four steps; only its parameters differ. *)
    E_PhaseStep : (
        PS_IDLE      := 0,
        PS_LINEUP    := 1,
        PS_CHARGE    := 2,
        PS_CIRCULATE := 3,
        PS_RECOVER   := 4,
        PS_DONE      := 5
    );

    E_Source : (
        SRC_NONE       := 0,
        SRC_FRESH      := 1,
        SRC_RECOVERED  := 2,
        SRC_CAUSTIC    := 3,
        SRC_ACID       := 4
    );

    E_Return : (
        RET_DRAIN     := 0,
        RET_RECOVERY  := 1,
        RET_CAUSTIC   := 2,
        RET_ACID      := 3,
        RET_BALANCE   := 4
    );

    E_FailCode : (
        FC_NONE             := 0,
        FC_LINEUP_TIMEOUT   := 1,
        FC_CHARGE_TIMEOUT   := 2,
        FC_TEMP_TIMEOUT     := 3,
        FC_PHASE_TIMEOUT    := 4,
        FC_FLOW_LOW         := 5,
        FC_PUMP_FAULT       := 6,
        FC_OPERATOR_ABORT   := 7,
        FC_SAFETY_TRIP      := 8,
        FC_RINSE_NOT_CLEAN  := 9,
        FC_LEVEL_LOW        := 10,
        FC_SENSOR_FAULT     := 11
    );
END_TYPE

TYPE
    (*
        What one phase has to achieve. PRG_Sequence fills this from the recipe
        and hands it to the single phase engine, so adding a phase is a matter
        of parameters rather than another copy of the same logic.
    *)
    ST_PhaseParams : STRUCT
        eSource         : E_Source;
        eReturn         : E_Return;
        rTempSp         : REAL;     (* heat setpoint, degC *)
        rTempMin        : REAL;     (* below this the hold timer does not count *)
        rConcSp         : REAL;     (* chemical strength, % w/w *)
        rConcMin        : REAL;
        rConcMax        : REAL;
        rFlowSp         : REAL;     (* m3/h *)
        rFlowMin        : REAL;     (* below this the hold timer does not count *)
        tHold           : TIME;     (* verified hold required to pass *)
        tMinPhase       : TIME;
        tMaxPhase       : TIME;
        tRecover        : TIME;     (* push the circuit contents back or to drain *)
        xHeat           : BOOL;
        xDose           : BOOL;
        xRecirculate    : BOOL;     (* closed loop through the balance tank *)
        xAdaptiveEnd    : BOOL;     (* terminate on the return conductivity *)
        rEndConductivity : REAL;    (* mS/cm, adaptive rinses only *)
    END_STRUCT

    (* What the phase engine asks the control layer to do this scan. *)
    ST_Demand : STRUCT
        eSource      : E_Source;
        eReturn      : E_Return;
        rFlowSp      : REAL;
        rTempSp      : REAL;
        xHeat        : BOOL;
        xDose        : BOOL;
        xSupplyPump  : BOOL;
        xReturnPump  : BOOL;
        xFillValve   : BOOL;
    END_STRUCT

    (* One line of the electronic batch record. *)
    ST_PhaseResult : STRUCT
        ePhase           : E_Phase;
        xExecuted        : BOOL;
        xPass            : BOOL;
        eFail            : E_FailCode;
        rDurationS       : REAL;
        rHoldAchievedS   : REAL;
        rHoldRequiredS   : REAL;    (* zero when the endpoint is the conductivity *)
        xAdaptiveEnd     : BOOL;
        rMinTempC        : REAL := 999.0;
        rMaxTempC        : REAL;
        rMeanConcPct     : REAL;
        rMinFlow         : REAL := 999.0;
        rWaterL          : REAL;
        rChemL           : REAL;
        rEnergyKWh       : REAL;
        rEndConductivity : REAL;
    END_STRUCT

    ST_Recipe : STRUCT
        sName            : STRING[24] := 'default';
        xUseAcid         : BOOL := TRUE;
        xUseSanitise     : BOOL := FALSE;
        xAdaptive        : BOOL := FALSE;
        rFlowSp          : REAL := 18.0;
        tPreRinse        : TIME := T#3m;
        rCausticTempSp   : REAL := 75.0;
        rCausticTempMin  : REAL := 70.0;
        rCausticConcSp   : REAL := 2.0;
        tCausticHold     : TIME := T#10m;
        tIntRinse        : TIME := T#2m;
        rAcidTempSp      : REAL := 60.0;
        rAcidTempMin     : REAL := 55.0;
        rAcidConcSp      : REAL := 1.0;
        tAcidHold        : TIME := T#5m;
        tFinalRinse      : TIME := T#3m;
        rSanitiseTempSp  : REAL := 85.0;
        rSanitiseTempMin : REAL := 80.0;
        tSanitiseHold    : TIME := T#10m;
        rRinseEndCond    : REAL := 0.10;
        tPhaseMax        : TIME := T#45m;
        tRinseMax        : TIME := T#12m;   (* adaptive rinses fail rather than run on *)
        tRinseMin        : TIME := T#60s;   (* floor under the transport-delay blanking *)
        rFlowMin         : REAL := 14.0;
    END_STRUCT

    ST_Kpi : STRUCT
        rFreshWaterL     : REAL;
        rRecoveredWaterL : REAL;
        rEffluentL       : REAL;
        rCausticL        : REAL;
        rAcidL           : REAL;
        rEnergyKWh       : REAL;
        rCycleTimeS      : REAL;
        xVerifiedClean   : BOOL;
    END_STRUCT

    ST_Alarm : STRUCT
        xActive   : BOOL;
        xLatched  : BOOL;
        xAcked    : BOOL;
        iPriority : INT;
        rTimeInS  : REAL;
        iCount    : INT;
    END_STRUCT
END_TYPE
