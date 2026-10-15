(*
    PRG_Alarms.st -- the alarm list.

    Twelve points, each with a priority and an on-delay chosen so that the
    operator is not told about things that resolve themselves. ISA-18.2's first
    rule is that every alarm must require a response; a low chemical level that
    clears in two seconds does not.

    First-out is captured and held until the list goes quiet, so the record
    says which condition started the trip rather than which one is loudest by
    the time anyone looks.
*)

PROGRAM PRG_Alarms
VAR
    fbSafety        : FB_Alarm;
    fbSupplyPump    : FB_Alarm;
    fbReturnPump    : FB_Alarm;
    fbLevelLow      : FB_Alarm;
    fbCausticLow    : FB_Alarm;
    fbAcidLow       : FB_Alarm;
    fbTempLow       : FB_Alarm;
    fbFlowLow       : FB_Alarm;
    fbCircuitBusy   : FB_Alarm;
    fbPhaseTimeout  : FB_Alarm;
    fbNotClean      : FB_Alarm;
    fbNoSteam       : FB_Alarm;
    fbCondSensor    : FB_Alarm;
    i               : INT;
    xAnyLatched     : BOOL;
    xCirculating    : BOOL;
END_VAR

xCirculating := gxPhaseExecute AND (gPhaseStep = PS_CIRCULATE);

fbSafety(xCondition := gxSafetyTrip,
         iPriority  := 1,
         xAck       := xCmdAckAlarms,
         rNowS      := grPlcTimeS,
         stAlarm    := gAlarms[C_ALM_SAFETY]);

fbSupplyPump(xCondition := xSupplyPumpFault OR (xSupplyPumpRun AND NOT xSupplyPumpRunning),
             iPriority  := 1,
             tOnDelay   := T#5s,
             xAck       := xCmdAckAlarms,
             rNowS      := grPlcTimeS,
             stAlarm    := gAlarms[C_ALM_SUPPLY_PUMP]);

fbReturnPump(xCondition := xReturnPumpFault OR (xReturnPumpRun AND NOT xReturnPumpRunning),
             iPriority  := 2,
             tOnDelay   := T#5s,
             xAck       := xCmdAckAlarms,
             rNowS      := grPlcTimeS,
             stAlarm    := gAlarms[C_ALM_RETURN_PUMP]);

fbLevelLow(xCondition := (rLevelBalanceTankPct < C_LEVEL_TRIP_PCT) AND (gUnitState <> UST_IDLE),
           iPriority  := 2,
           tOnDelay   := T#3s,
           xAck       := xCmdAckAlarms,
           rNowS      := grPlcTimeS,
           stAlarm    := gAlarms[C_ALM_LEVEL_LOW]);

fbCausticLow(xCondition := rLevelCausticPct < C_CHEM_LEVEL_MIN,
             iPriority  := 3,
             tOnDelay   := T#10s,
             xAck       := xCmdAckAlarms,
             rNowS      := grPlcTimeS,
             stAlarm    := gAlarms[C_ALM_CAUSTIC_LOW]);

fbAcidLow(xCondition := rLevelAcidPct < C_CHEM_LEVEL_MIN,
          iPriority  := 3,
          tOnDelay   := T#10s,
          xAck       := xCmdAckAlarms,
          rNowS      := grPlcTimeS,
          stAlarm    := gAlarms[C_ALM_ACID_LOW]);

(* Twelve minutes: a cold circuit and a 1200 litre balance tank take about
   eight to come up to a wash temperature, so anything shorter alarms on every
   normal start. It still catches a steam valve that never opened. *)
fbTempLow(xCondition := xCirculating AND gPhaseParams.xHeat
                        AND (rTempReturnC < gPhaseParams.rTempMin),
          iPriority  := 2,
          tOnDelay   := T#12m,
          xAck       := xCmdAckAlarms,
          rNowS      := grPlcTimeS,
          stAlarm    := gAlarms[C_ALM_TEMP_LOW]);

fbFlowLow(xCondition := xCirculating AND (rFlowSupplyM3h < gPhaseParams.rFlowMin),
          iPriority  := 2,
          tOnDelay   := T#30s,
          xAck       := xCmdAckAlarms,
          rNowS      := grPlcTimeS,
          stAlarm    := gAlarms[C_ALM_FLOW_LOW]);

fbCircuitBusy(xCondition := (NOT xCircuitAvailable) AND (gUnitState <> UST_IDLE),
              iPriority  := 2,
              tOnDelay   := T#3s,
              xAck       := xCmdAckAlarms,
              rNowS      := grPlcTimeS,
              stAlarm    := gAlarms[C_ALM_CIRCUIT_BUSY]);

fbPhaseTimeout(xCondition := (gFailCode = FC_PHASE_TIMEOUT) OR (gFailCode = FC_CHARGE_TIMEOUT)
                             OR (gFailCode = FC_TEMP_TIMEOUT),
               iPriority  := 1,
               xAck       := xCmdAckAlarms,
               rNowS      := grPlcTimeS,
               stAlarm    := gAlarms[C_ALM_PHASE_TIMEOUT]);

fbNotClean(xCondition := gFailCode = FC_RINSE_NOT_CLEAN,
           iPriority  := 1,
           xAck       := xCmdAckAlarms,
           rNowS      := grPlcTimeS,
           stAlarm    := gAlarms[C_ALM_NOT_CLEAN]);

fbNoSteam(xCondition := (NOT xSteamAvailable) AND gPhaseParams.xHeat AND gxPhaseExecute,
          iPriority  := 3,
          tOnDelay   := T#10s,
          xAck       := xCmdAckAlarms,
          rNowS      := grPlcTimeS,
          stAlarm    := gAlarms[C_ALM_NO_STEAM]);

fbCondSensor(xCondition := gxCondSensorFault,
             iPriority  := 1,
             xAck       := xCmdAckAlarms,
             rNowS      := grPlcTimeS,
             stAlarm    := gAlarms[C_ALM_COND_SENSOR]);

(* ---- summary ---------------------------------------------------------- *)
giActiveAlarms := 0;
gxAlarmP1Active := FALSE;
xAnyLatched := FALSE;
xHmiAlarmHorn := FALSE;

FOR i := 1 TO C_ALARM_COUNT DO
    IF gAlarms[i].xLatched THEN
        xAnyLatched := TRUE;
        giActiveAlarms := giActiveAlarms + 1;
        IF gAlarms[i].xActive AND (gAlarms[i].iPriority = 1) THEN
            gxAlarmP1Active := TRUE;
        END_IF
        IF NOT gAlarms[i].xAcked THEN
            xHmiAlarmHorn := TRUE;
        END_IF
        IF giFirstOut = 0 THEN
            giFirstOut := i;
        END_IF
    END_IF
END_FOR

IF NOT xAnyLatched THEN
    giFirstOut := 0;
END_IF

(* Acknowledge is momentary too and this is the last program that reads it. *)
xCmdAckAlarms := FALSE;

END_PROGRAM
