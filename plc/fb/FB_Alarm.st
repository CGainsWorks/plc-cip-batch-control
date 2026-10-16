(*
    FB_Alarm.st: one alarm point, following the ISA-18.2 state model.

    States are held in the ST_Alarm structure the caller owns, so the whole
    alarm list is one array a SCADA client can read in a single request:

        xActive  the process condition is true right now
        xLatched the alarm has been raised and not yet cleared
        xAcked   the operator has acknowledged this occurrence

    An alarm returns to normal only when the condition has gone away *and* it
    has been acknowledged, which is what stops a fleeting condition from
    disappearing off the list before anybody has seen it.
*)

FUNCTION_BLOCK FB_Alarm
VAR_INPUT
    xCondition  : BOOL;
    xEnable     : BOOL := TRUE;
    xAck        : BOOL;
    iPriority   : INT := 3;
    tOnDelay    : TIME := T#0s;
    tOffDelay   : TIME := T#0s;
    rNowS       : REAL;
END_VAR
VAR_IN_OUT
    stAlarm     : ST_Alarm;
END_VAR
VAR_OUTPUT
    xNewAlarm   : BOOL;     (* one scan pulse when it first latches *)
END_VAR
VAR
    fbOn        : TON;
    fbOff       : TOF;
    fbRise      : R_TRIG;
END_VAR

stAlarm.iPriority := iPriority;

fbOn(IN := xCondition AND xEnable, PT := tOnDelay);
fbOff(IN := fbOn.Q, PT := tOffDelay);
stAlarm.xActive := fbOff.Q;

fbRise(CLK := stAlarm.xActive);
xNewAlarm := fbRise.Q;

IF xNewAlarm THEN
    stAlarm.xLatched := TRUE;
    stAlarm.xAcked := FALSE;
    stAlarm.rTimeInS := rNowS;
    stAlarm.iCount := stAlarm.iCount + 1;
END_IF

IF xAck AND stAlarm.xLatched THEN
    stAlarm.xAcked := TRUE;
END_IF

IF stAlarm.xLatched AND NOT stAlarm.xActive AND stAlarm.xAcked THEN
    stAlarm.xLatched := FALSE;
END_IF

END_FUNCTION_BLOCK
