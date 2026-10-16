(*
    GVL_Io.st: the I/O image.

    These declarations are the single source of truth for the address map. The
    simulated field bus and the Modbus TCP server both build their tables by
    reading the AT addresses back out of the compiled program, so a tag cannot
    drift away from the address the logic uses. See docs/io_map.md.

    Address convention: %IXb.i is byte b bit i; %ID/%QD/%MD n is double word n.

    Inputs and outputs never share a number: inputs own bytes 0 and 1 and
    double words 0 to 15, outputs start at byte 2 and double word 16. IEC
    61131-3 keeps %I and %Q apart, but some runtimes back both with one
    process image keyed by the number alone (RuSTy does, see
    docs/compiled_crosscheck.md), and there %IX0.0 and %QX0.0 would be the same
    bit. Keeping the numbers disjoint costs nothing and works on both kinds.
*)

VAR_GLOBAL
    (* ---- digital inputs: plant status ------------------------------- *)
    xSafetyOk               AT %IX0.0 : BOOL;   (* safety relay healthy, E-stop clear *)
    xSupplyPumpRunning      AT %IX0.1 : BOOL;
    xReturnPumpRunning      AT %IX0.2 : BOOL;
    xSupplyPumpFault        AT %IX0.3 : BOOL;
    xReturnPumpFault        AT %IX0.4 : BOOL;
    xCircuitAvailable       AT %IX0.5 : BOOL;   (* the vessel is released for cleaning *)
    xSteamAvailable         AT %IX0.6 : BOOL;
    xBalanceTankLowLow      AT %IX0.7 : BOOL;   (* hard-wired float switch *)

    (* ---- operator commands -------------------------------------------
       These live in the memory area, not the input image, because they arrive
       from a panel or a SCADA client over the network rather than from a
       hard-wired button. The controller clears each one once it has taken the
       edge, so a client that writes a coil and forgets it still gets a pulse. *)
    xCmdStart               AT %MX0.0 : BOOL;
    xCmdHold                AT %MX0.1 : BOOL;
    xCmdRestart             AT %MX0.2 : BOOL;
    xCmdAbort               AT %MX0.3 : BOOL;
    xCmdReset               AT %MX0.4 : BOOL;
    xCmdAckAlarms           AT %MX0.5 : BOOL;

    (* ---- analogue inputs -------------------------------------------- *)
    rLevelBalanceTankPct    AT %ID0  : REAL;
    rTempSupplyC            AT %ID1  : REAL;    (* after the heat exchanger *)
    rTempReturnC            AT %ID2  : REAL;    (* back from the circuit *)
    rFlowSupplyM3h          AT %ID3  : REAL;
    rConductivityReturn     AT %ID4  : REAL;    (* mS/cm, temperature compensated *)
    rPressureSupplyBar      AT %ID5  : REAL;
    rLevelCausticPct        AT %ID6  : REAL;
    rLevelAcidPct           AT %ID7  : REAL;
    rLevelRecoveryPct       AT %ID8  : REAL;
    rConductivitySupply     AT %ID9  : REAL;    (* mS/cm, feed side *)
    rFlowMakeupM3h          AT %ID10 : REAL;    (* make-up into the balance tank *)
    rTempBalanceTankC       AT %ID11 : REAL;
    rConductivityTank       AT %ID12 : REAL;    (* CT-03, in the balance tank *)

    (* ---- digital outputs: valve matrix ------------------------------ *)
    xV01FreshWater          AT %QX2.0 : BOOL;
    xV02RecoveredWater      AT %QX2.1 : BOOL;
    xV03CausticDose         AT %QX2.2 : BOOL;
    xV04AcidDose            AT %QX2.3 : BOOL;
    xV05SupplyToCircuit     AT %QX2.4 : BOOL;
    xV06ReturnToDrain       AT %QX2.5 : BOOL;
    xV07ReturnToBalance     AT %QX2.6 : BOOL;
    xV08ReturnToCaustic     AT %QX2.7 : BOOL;
    xV09ReturnToRecovery    AT %QX3.0 : BOOL;
    xV10ReturnToAcid        AT %QX3.1 : BOOL;
    xSupplyPumpRun          AT %QX3.2 : BOOL;
    xReturnPumpRun          AT %QX3.3 : BOOL;
    xSteamIsolation         AT %QX3.4 : BOOL;
    xV11CausticSupply       AT %QX3.5 : BOOL;   (* caustic tank -> balance tank *)
    xV12AcidSupply          AT %QX3.6 : BOOL;

    (* ---- analogue outputs ------------------------------------------- *)
    rSupplyPumpSpeedPct     AT %QD16 : REAL;
    rSteamValvePct          AT %QD17 : REAL;

    (* ---- memory image published to SCADA ---------------------------- *)
    iHmiUnitState           AT %MW0 : INT;
    iHmiPhase               AT %MW1 : INT;
    iHmiPhaseStep           AT %MW2 : INT;
    iHmiFailCode            AT %MW3 : INT;
    iHmiActiveAlarms        AT %MW4 : INT;
    iHmiFirstOutAlarm       AT %MW5 : INT;
    iHmiBatchId             AT %MW6 : INT;

    rHmiFreshWaterL         AT %MD10 : REAL;
    rHmiRecoveredWaterL     AT %MD11 : REAL;
    rHmiEffluentL           AT %MD12 : REAL;
    rHmiCausticL            AT %MD13 : REAL;
    rHmiAcidL               AT %MD14 : REAL;
    rHmiEnergyKWh           AT %MD15 : REAL;
    rHmiCycleTimeS          AT %MD16 : REAL;
    rHmiPhaseElapsedS       AT %MD17 : REAL;
    rHmiHoldAchievedS       AT %MD18 : REAL;
    rHmiConcentrationPct    AT %MD19 : REAL;

    xHmiVerifiedClean       AT %MX30.0 : BOOL;
    xHmiCycleComplete       AT %MX30.1 : BOOL;
    xHmiCycleAborted        AT %MX30.2 : BOOL;
    xHmiAlarmHorn           AT %MX30.3 : BOOL;
    xHmiHeating             AT %MX30.4 : BOOL;
END_VAR
