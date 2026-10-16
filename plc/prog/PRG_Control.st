(*
    PRG_Control.st: the only program that writes to the field outputs.

    It takes the demand PRG_Sequence produced, applies the permissives
    PRG_Safety produced and drives the valve matrix, the two motor starters
    and the two closed loops. Because it is the single writer, the question
    "what could possibly open the steam valve?" has exactly one answer.

    Loops:
      flow  -> supply pump speed, PI. Slow, because the pump is a VSD against
               a fixed circuit resistance and there is nothing to gain from
               being quick.
      temperature -> steam valve, PI on the supply temperature. Tuned soft:
               the plate pack has a couple of seconds of transport delay and
               the balance tank behind it is a 1200 litre integrator.
*)

PROGRAM PRG_Control
VAR
    fbFlowPid       : FB_Pid;
    fbTempPid       : FB_Pid;
    fbSupplyLimit   : FB_Pid;
    fbSupplyPump    : FB_Pump;
    fbReturnPump    : FB_Pump;
    xRunSupply      : BOOL;
    xRunReturn      : BOOL;
    xHeatDemand     : BOOL;
    xDoseDemand     : BOOL;
END_VAR

(* ---- interlocked demands --------------------------------------------- *)
xRunSupply  := gDemand.xSupplyPump AND gxPermitRun;
xRunReturn  := gDemand.xReturnPump AND gxPermitRun;
xHeatDemand := gDemand.xHeat AND gxPermitHeat;
xDoseDemand := gDemand.xDose AND gxPermitDose;

(* ---- motor starters --------------------------------------------------- *)
fbSupplyPump(xStart       := xRunSupply,
             xInterlockOk := gxPermitRun,
             xRunFeedback := xSupplyPumpRunning,
             xFault       := xSupplyPumpFault,
             xReset       := xCmdReset);

fbReturnPump(xStart       := xRunReturn,
             xInterlockOk := gxPermitRun,
             xRunFeedback := xReturnPumpRunning,
             xFault       := xReturnPumpFault,
             xReset       := xCmdReset);

xSupplyPumpRun := fbSupplyPump.xRun;
xReturnPumpRun := fbReturnPump.xRun;

(* ---- flow loop -------------------------------------------------------- *)
fbFlowPid(xEnable := fbSupplyPump.xRun,
          rPv     := rFlowSupplyM3h,
          rSp     := gDemand.rFlowSp,
          rKp     := 3.0,
          rTi     := 8.0,
          rOutMin := C_PUMP_SPEED_MIN,
          rOutMax := C_PUMP_SPEED_MAX,
          rCycleS := C_CYCLE_S);

IF fbSupplyPump.xRun THEN
    rSupplyPumpSpeedPct := fbFlowPid.rOut;
ELSE
    rSupplyPumpSpeedPct := 0.0;
END_IF

(* ---- temperature loop -------------------------------------------------- *)
(* The controlled variable is the return temperature, not the supply. The
   recipe specifies a temperature at the soil and the coldest point of the
   circuit is the one that has to reach it; controlling the supply instead
   throttles the steam as soon as one pass through the plate pack looks hot
   enough and the loop then takes three times as long to bring the balance
   tank up.

   Tuning is lambda tuning for an integrator with dead time: the circuit holds
   450 litres, so at 18 m3/h a change at the plate pack takes about 90 seconds
   to reach the return element. Kp 5 %/K, Ti 600 s. *)
fbTempPid(xEnable := xHeatDemand,
          rPv     := rTempReturnC,
          rSp     := gDemand.rTempSp,
          rKp     := 5.0,
          rTi     := 600.0,
          rOutMin := 0.0,
          rOutMax := 100.0,
          rCycleS := C_CYCLE_S);

(* Override: a proportional-only high limit on the plate-pack outlet, low
   selected against the main loop. Proportional only on purpose, because an
   integrator on the losing half of a selector winds up. *)
fbSupplyLimit(xEnable := xHeatDemand,
              rPv     := rTempSupplyC,
              rSp     := C_SUPPLY_TEMP_LIMIT,
              rKp     := 25.0,
              rTi     := 0.0,
              rOutMin := 0.0,
              rOutMax := 100.0,
              rCycleS := C_CYCLE_S);

xSteamIsolation := xHeatDemand;
IF xHeatDemand THEN
    rSteamValvePct := MIN(fbTempPid.rOut, fbSupplyLimit.rOut);
ELSE
    rSteamValvePct := 0.0;
END_IF

(* ---- valve matrix ----------------------------------------------------- *)
(* Source selection. Only one inlet is ever open and the fill valve gates
   the whole group so a phase can hold its line-up without taking liquid. *)
xV01FreshWater     := gDemand.xFillValve AND (gDemand.eSource = SRC_FRESH);
xV02RecoveredWater := gDemand.xFillValve AND (gDemand.eSource = SRC_RECOVERED);
xV11CausticSupply  := gDemand.xFillValve AND (gDemand.eSource = SRC_CAUSTIC);
xV12AcidSupply     := gDemand.xFillValve AND (gDemand.eSource = SRC_ACID);

(* Dosing is concentrate trim on top of whatever the tank already holds, so it
   is gated on the phase's dose demand rather than on the fill valve. *)
xV03CausticDose    := xDoseDemand AND (gDemand.eSource = SRC_CAUSTIC);
xV04AcidDose       := xDoseDemand AND (gDemand.eSource = SRC_ACID);

xV08ReturnToCaustic := FALSE;
xV10ReturnToAcid := FALSE;

(* Return destination. Exactly one of these is open whenever the return pump
   is running, so the circuit always has somewhere to discharge. *)
xV06ReturnToDrain    := FALSE;
xV07ReturnToBalance  := FALSE;
xV09ReturnToRecovery := FALSE;

CASE gDemand.eReturn OF
    RET_DRAIN:
        xV06ReturnToDrain := TRUE;
    RET_RECOVERY:
        xV09ReturnToRecovery := TRUE;
    RET_CAUSTIC:
        xV08ReturnToCaustic := TRUE;
    RET_ACID:
        xV10ReturnToAcid := TRUE;
    RET_BALANCE:
        xV07ReturnToBalance := TRUE;
END_CASE

xV05SupplyToCircuit := fbSupplyPump.xRun;

END_PROGRAM
