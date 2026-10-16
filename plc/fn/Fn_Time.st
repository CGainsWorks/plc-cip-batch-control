(*
    Fn_Time.st: TIME to seconds and back, written so it means the same thing
    on every toolchain.

    IEC 61131-3 leaves the unit of a TIME to number conversion to the
    implementation. CODESYS and TwinCAT count milliseconds, RuSTy counts
    nanoseconds and other runtimes differ again, so TIME_TO_LREAL(t) / 1000.0
    is seconds on one compiler and microseconds on the next. Dividing by the
    same conversion of T#1s cancels the unit whatever it is.
*)

FUNCTION TimeToSeconds : REAL
VAR_INPUT
    tIn : TIME;
END_VAR

TimeToSeconds := LREAL_TO_REAL(TIME_TO_LREAL(tIn) / TIME_TO_LREAL(T#1s));

END_FUNCTION


FUNCTION SecondsToTime : TIME
VAR_INPUT
    rSeconds : REAL;
END_VAR

SecondsToTime := LREAL_TO_TIME(REAL_TO_LREAL(rSeconds) * TIME_TO_LREAL(T#1s));

END_FUNCTION
