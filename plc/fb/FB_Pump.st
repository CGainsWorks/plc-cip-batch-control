(*
    FB_Pump.st: motor starter with interlock, feedback supervision and a
    latched trip.

    A pump that is commanded to run but reports no feedback inside
    tFeedbackTimeout latches a trip. Clearing it needs an explicit reset, not
    just the disappearance of the fault, so an intermittent contactor cannot
    silently restart the skid mid-wash.
*)

FUNCTION_BLOCK FB_Pump
VAR_INPUT
    xStart              : BOOL;
    xInterlockOk        : BOOL;
    xRunFeedback        : BOOL;
    xFault              : BOOL;     (* motor protection relay *)
    xReset              : BOOL;
    tFeedbackTimeout    : TIME := T#5s;
END_VAR
VAR_OUTPUT
    xRun                : BOOL;     (* to the field *)
    xTripped            : BOOL;
    xFeedbackFault      : BOOL;
    xHealthy            : BOOL;
END_VAR
VAR
    fbFeedback  : TON;
    fbFault     : R_TRIG;
END_VAR

fbFault(CLK := xFault);

IF fbFault.Q THEN
    xTripped := TRUE;
END_IF

IF xReset AND NOT xFault THEN
    xTripped := FALSE;
    xFeedbackFault := FALSE;
END_IF

xRun := xStart AND xInterlockOk AND NOT xTripped AND NOT xFeedbackFault;

(* Supervise only the commanded direction: a pump that keeps running after the
   command drops is a separate condition, alarmed in PRG_Alarms. *)
fbFeedback(IN := xRun AND NOT xRunFeedback, PT := tFeedbackTimeout);
IF fbFeedback.Q THEN
    xFeedbackFault := TRUE;
    xRun := FALSE;
END_IF

xHealthy := NOT xTripped AND NOT xFeedbackFault AND NOT xFault;

END_FUNCTION_BLOCK
