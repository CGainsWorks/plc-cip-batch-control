(*
    FB_Pid.st -- PI(D) controller with clamping anti-windup.

    The integral only accumulates while the output is off its limit, which is
    what keeps the steam valve from winding up during the long fill step and
    then overshooting the wash temperature when circulation finally starts.

    rTi and rTd are in seconds. rTi = 0 disables the integral term.
*)

FUNCTION_BLOCK FB_Pid
VAR_INPUT
    xEnable     : BOOL;
    rPv         : REAL;
    rSp         : REAL;
    rKp         : REAL := 1.0;
    rTi         : REAL := 0.0;
    rTd         : REAL := 0.0;
    rOutMin     : REAL := 0.0;
    rOutMax     : REAL := 100.0;
    rCycleS     : REAL := 0.1;
END_VAR
VAR_OUTPUT
    rOut        : REAL;
    rError      : REAL;
    xSaturated  : BOOL;
END_VAR
VAR
    rIntegral   : REAL;
    rPrevPv     : REAL;
    xFirstScan  : BOOL := TRUE;
    rDerivative : REAL;
    rRaw        : REAL;
END_VAR

IF NOT xEnable THEN
    (* Bumpless enough for this duty: park the integrator and the output. *)
    rIntegral := 0.0;
    rOut := rOutMin;
    rError := 0.0;
    xSaturated := FALSE;
    xFirstScan := TRUE;
    rPrevPv := rPv;
    RETURN;
END_IF

rError := rSp - rPv;

(* Derivative on the measurement, so a setpoint step does not kick the valve. *)
IF xFirstScan THEN
    rDerivative := 0.0;
    xFirstScan := FALSE;
ELSIF rCycleS > 0.0 THEN
    rDerivative := (rPrevPv - rPv) / rCycleS;
ELSE
    rDerivative := 0.0;
END_IF
rPrevPv := rPv;

rRaw := rKp * rError + rIntegral + rKp * rTd * rDerivative;

(* Clamping anti-windup: integrate only if that does not push further into a
   limit the output is already sitting on. *)
IF rTi > 0.0 THEN
    IF (rRaw < rOutMax) AND (rRaw > rOutMin) THEN
        rIntegral := rIntegral + (rKp / rTi) * rError * rCycleS;
    ELSIF (rRaw >= rOutMax) AND (rError < 0.0) THEN
        rIntegral := rIntegral + (rKp / rTi) * rError * rCycleS;
    ELSIF (rRaw <= rOutMin) AND (rError > 0.0) THEN
        rIntegral := rIntegral + (rKp / rTi) * rError * rCycleS;
    END_IF
    rIntegral := LIMIT(rOutMin - rOutMax, rIntegral, rOutMax - rOutMin);
END_IF

rRaw := rKp * rError + rIntegral + rKp * rTd * rDerivative;
rOut := LIMIT(rOutMin, rRaw, rOutMax);
xSaturated := (rRaw > rOutMax) OR (rRaw < rOutMin);

END_FUNCTION_BLOCK
