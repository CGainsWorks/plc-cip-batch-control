(*
    FB_Totaliser.st: rectangular integration of a rate against the scan
    period, with a low-flow cut-off.

    The cut-off matters: a flow transmitter parked at 0.3 m3/h of noise while
    the pump is stopped would otherwise book about 7 litres an hour of water
    that never moved and those litres end up in the batch record.
*)

FUNCTION_BLOCK FB_Totaliser
VAR_INPUT
    xEnable     : BOOL;
    rRatePerHour : REAL;
    rCutOff     : REAL := 0.0;
    rCycleS     : REAL := 0.1;
    xReset      : BOOL;
END_VAR
VAR_OUTPUT
    rTotal      : REAL;
    rRateUsed   : REAL;
END_VAR

IF xReset THEN
    rTotal := 0.0;
END_IF

IF xEnable AND (rRatePerHour > rCutOff) THEN
    rRateUsed := rRatePerHour;
    rTotal := rTotal + rRatePerHour * rCycleS / 3600.0;
ELSE
    rRateUsed := 0.0;
END_IF

END_FUNCTION_BLOCK
