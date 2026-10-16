(*
    GVL_Cip.st: internal state shared between the five programs.

    Nothing here is located: these are the controller's own working data, not
    field I/O. PRG_Sequence owns the state and phase variables, PRG_Control owns
    the outputs, PRG_Alarms owns the alarm array and PRG_Kpi owns the totals.
    Every other program reads them.
*)

VAR_GLOBAL CONSTANT
    C_ALARM_COUNT       : INT  := 13;
    C_PHASE_COUNT       : INT  := 6;

    (* Alarm identifiers, used as the index into gAlarms. *)
    C_ALM_SAFETY        : INT  := 1;
    C_ALM_SUPPLY_PUMP   : INT  := 2;
    C_ALM_RETURN_PUMP   : INT  := 3;
    C_ALM_LEVEL_LOW     : INT  := 4;
    C_ALM_CAUSTIC_LOW   : INT  := 5;
    C_ALM_ACID_LOW      : INT  := 6;
    C_ALM_TEMP_LOW      : INT  := 7;
    C_ALM_FLOW_LOW      : INT  := 8;
    C_ALM_CIRCUIT_BUSY  : INT  := 9;
    C_ALM_PHASE_TIMEOUT : INT  := 10;
    C_ALM_NOT_CLEAN     : INT  := 11;
    C_ALM_NO_STEAM      : INT  := 12;
    C_ALM_COND_SENSOR   : INT  := 13;

    (* Skid geometry. The plant model holds the same figures; docs/model.md
       explains why they are stated twice and how the test keeps them equal. *)
    C_BALANCE_TANK_L    : REAL := 1200.0;
    C_CIRCUIT_VOLUME_L  : REAL := 450.0;    (* holdup of the circuit being cleaned *)
    (* A rinse endpoint cannot be believed until the return sensor has seen
       what was in the circuit when the rinse started. One and a half circuit
       turnovers is the blanking time. *)
    C_RINSE_TURNOVERS   : REAL := 1.5;
    C_FILL_TARGET_PCT   : REAL := 60.0;     (* recirculating phases *)
    (* Single-pass phases run the tank as a break volume at the lowest level
       the suction will stand. The rinse has to wash the tank out as well as
       the circuit and the tank washes out with a time constant of
       volume/flow: 156 litres at 18 m3/h is half a minute, 360 litres is
       nearly two and that difference is most of the rinse. *)
    C_RINSE_LEVEL_PCT   : REAL := 13.0;
    C_PUMP_START_PCT    : REAL := 10.0;     (* suction head to start the pump *)
    C_LEVEL_TRIP_PCT    : REAL := 7.0;
    C_CHEM_LEVEL_MIN    : REAL := 15.0;

    (* Two-point conductivity calibrations, mS/cm per % w/w, compensated to
       25 degC by the transmitter. Caustic is calibrated at 2 %, acid at 1 %. *)
    C_COND_K_CAUSTIC    : REAL := 47.0;
    C_COND_K_ACID       : REAL := 45.0;

    (* Positive-displacement dosing pumps, so the delivered volume is the
       valve-open time multiplied by a constant. *)
    C_CAUSTIC_DOSE_LPM  : REAL := 30.0;
    C_ACID_DOSE_LPM     : REAL := 20.0;

    C_TEMP_TOL          : REAL := 1.0;
    (* Plate-pack outlet limit. Above this the gaskets suffer and dissolved
       soil starts to bake onto the plates. *)
    C_SUPPLY_TEMP_LIMIT : REAL := 90.0;
    (* Task period. The harness asserts that the scan it runs matches. *)
    C_CYCLE_S           : REAL := 0.2;

    C_PUMP_SPEED_MIN    : REAL := 25.0;
    C_PUMP_SPEED_MAX    : REAL := 100.0;
END_VAR

VAR_GLOBAL
    (* ---- recipe and procedure state --------------------------------- *)
    gRecipe         : ST_Recipe;
    gUnitState      : E_UnitState;
    gPhase          : E_Phase;
    gPhaseStep      : E_PhaseStep;
    gFailCode       : E_FailCode;
    gPhaseParams    : ST_PhaseParams;
    gDemand         : ST_Demand;
    gResults        : ARRAY[1..6] OF ST_PhaseResult;
    gKpi            : ST_Kpi;
    giBatchId       : INT;

    (* ---- requests between programs ---------------------------------- *)
    gxSafetyTrip    : BOOL;     (* PRG_Safety -> everyone *)
    gxPermitRun     : BOOL;     (* PRG_Safety: it is safe to circulate *)
    gxPermitHeat    : BOOL;     (* PRG_Safety: steam may be admitted *)
    gxPermitDose    : BOOL;     (* PRG_Safety: chemical may be dosed *)
    gxHoldRequest   : BOOL;
    gxAbortRequest  : BOOL;
    gxPhaseExecute  : BOOL;
    gxPhaseDone     : BOOL;
    gxPhaseFailed   : BOOL;
    gxCycleReset    : BOOL;     (* one scan at the start of a batch *)
    gxPhaseStartPulse : BOOL;
    gxPhaseEndPulse : BOOL;
    giPhaseIndex    : INT;      (* which gResults entry the pulse refers to *)

    (* ---- derived measurements --------------------------------------- *)
    grConcentrationPct : REAL;  (* from the return conductivity *)
    grDeltaConductivity : REAL; (* return minus supply, the rinse criterion *)
    grCycleElapsedS : REAL;
    grPhaseElapsedS : REAL;
    grHoldAchievedS : REAL;
    grPlcTimeS      : REAL;     (* controller uptime, used to stamp alarms *)
    grCondFloor     : REAL;     (* reading the return must exceed in a wash *)
    gxCondSensorFault : BOOL;   (* the return element is not reading the process *)
    gxCondProven    : BOOL;     (* it saw the wash during this phase *)

    (* ---- alarms ------------------------------------------------------ *)
    gAlarms         : ARRAY[1..13] OF ST_Alarm;
    giFirstOut      : INT;
    giActiveAlarms  : INT;
    gxAlarmP1Active : BOOL;
END_VAR
