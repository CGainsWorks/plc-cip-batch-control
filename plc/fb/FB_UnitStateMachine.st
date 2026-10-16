(*
    FB_UnitStateMachine.st: the ISA-88 unit procedural state model.

    Commands are edge triggered, so a stuck HMI bit cannot hold the unit in a
    transition. ABORTING is deliberately not instantaneous: it runs for
    tAbortSettle so the control layer can shut the steam valve and stop the
    pumps in a defined order before the unit is declared ABORTED.

        IDLE -> STARTING -> RUNNING -> COMPLETING -> COMPLETE -> IDLE
                              |  ^
                          HOLDING |
                              |  RESTARTING
                              v  |
                            HELD +
        any active state -> ABORTING -> ABORTED -> IDLE
*)

FUNCTION_BLOCK FB_UnitStateMachine
VAR_INPUT
    xStart              : BOOL;
    xHold               : BOOL;
    xRestart            : BOOL;
    xAbort              : BOOL;
    xReset              : BOOL;
    xReadyToStart       : BOOL;
    xProcedureComplete  : BOOL;
    xProcedureFailed    : BOOL;
    xSafetyTrip         : BOOL;
    tAbortSettle        : TIME := T#5s;
    tHoldSettle         : TIME := T#2s;
END_VAR
VAR_OUTPUT
    eState              : E_UnitState;
    xExecute            : BOOL;     (* the phase engine may run *)
    xHolding            : BOOL;     (* freeze the phase, keep the line-up *)
    xAborting           : BOOL;
    xStartPulse         : BOOL;     (* one scan, initialise the procedure *)
    xActive             : BOOL;     (* anything other than IDLE/COMPLETE/ABORTED *)
END_VAR
VAR
    fbStart     : R_TRIG;
    fbHold      : R_TRIG;
    fbRestart   : R_TRIG;
    fbAbort     : R_TRIG;
    fbReset     : R_TRIG;
    fbAbortTmr  : TON;
    fbHoldTmr   : TON;
END_VAR

fbStart(CLK := xStart);
fbHold(CLK := xHold);
fbRestart(CLK := xRestart);
fbAbort(CLK := xAbort);
fbReset(CLK := xReset);

xStartPulse := FALSE;

(* An abort request or a safety trip pre-empts every other transition. *)
IF (fbAbort.Q OR xSafetyTrip OR xProcedureFailed)
   AND (eState <> UST_IDLE) AND (eState <> UST_COMPLETE)
   AND (eState <> UST_ABORTED) AND (eState <> UST_ABORTING) THEN
    eState := UST_ABORTING;
END_IF

CASE eState OF
    UST_IDLE:
        (* Park both settle timers so a previous batch cannot leave one
           already elapsed and collapse the next HOLDING into one scan. *)
        fbHoldTmr(IN := FALSE, PT := tHoldSettle);
        fbAbortTmr(IN := FALSE, PT := tAbortSettle);
        IF fbStart.Q AND xReadyToStart THEN
            eState := UST_STARTING;
        END_IF

    UST_STARTING:
        xStartPulse := TRUE;
        eState := UST_RUNNING;

    UST_RUNNING:
        IF xProcedureComplete THEN
            eState := UST_COMPLETING;
        ELSIF fbHold.Q THEN
            eState := UST_HOLDING;
        END_IF

    UST_HOLDING:
        fbHoldTmr(IN := TRUE, PT := tHoldSettle);
        IF fbHoldTmr.Q THEN
            fbHoldTmr(IN := FALSE, PT := tHoldSettle);
            eState := UST_HELD;
        END_IF

    UST_HELD:
        IF fbRestart.Q THEN
            eState := UST_RESTARTING;
        END_IF

    UST_RESTARTING:
        eState := UST_RUNNING;

    UST_COMPLETING:
        eState := UST_COMPLETE;

    UST_COMPLETE:
        IF fbReset.Q THEN
            eState := UST_IDLE;
        END_IF

    UST_ABORTING:
        fbAbortTmr(IN := TRUE, PT := tAbortSettle);
        IF fbAbortTmr.Q THEN
            fbAbortTmr(IN := FALSE, PT := tAbortSettle);
            eState := UST_ABORTED;
        END_IF

    UST_ABORTED:
        IF fbReset.Q AND NOT xSafetyTrip THEN
            eState := UST_IDLE;
        END_IF
END_CASE

xExecute  := (eState = UST_RUNNING);
(* RESTARTING counts as holding. It lasts one scan, and the phase has not
   resumed yet, so the phase engine must keep its state through it. Leaving it
   out of both outputs gives the engine one scan in which it is neither
   executing nor holding, which it reads as the phase having ended; the next
   scan then looks like a fresh start and the phase begins again from zero. *)
xHolding  := (eState = UST_HOLDING) OR (eState = UST_HELD) OR (eState = UST_RESTARTING);
xAborting := (eState = UST_ABORTING);
xActive   := (eState <> UST_IDLE) AND (eState <> UST_COMPLETE) AND (eState <> UST_ABORTED);

END_FUNCTION_BLOCK
