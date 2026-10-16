"""The Structured Text front end.

These are the tests that decide whether the rest of the repository means
anything: if the interpreter gets TON, integer wrap-around or the sign of MOD
wrong, the controller behaves differently here from the way it would behave on
a real PLC and every number downstream is fiction.
"""

from __future__ import annotations

import math
import os

import pytest

from src.iec61131.address import parse_address
from src.iec61131.errors import STDeclarationError, STRuntimeError, STSyntaxError
from src.iec61131.lexer import tokenize
from src.iec61131.runtime import Runtime


def run(source: str, program: str = "PRG_Test", scans: int = 1, dt: float = 0.1) -> Runtime:
    runtime = Runtime()
    runtime.load_source(source, "test.st")
    runtime.build([program])
    for _ in range(scans):
        runtime.scan(dt)
    return runtime


def wrap(body: str, declarations: str = "", globals_: str = "") -> str:
    return """
VAR_GLOBAL
{0}
END_VAR
PROGRAM PRG_Test
VAR
{1}
END_VAR
{2}
END_PROGRAM
""".format(globals_ or "    gDummy : BOOL;", declarations, body)


# ---------------------------------------------------------------- lexer ----


def test_duration_literals_are_seconds():
    tokens = {t.text.upper(): t.value for t in tokenize("T#1s T#500ms T#1m30s T#1d2h TIME#250ms")}
    assert tokens["T#1S"] == 1.0
    assert tokens["T#500MS"] == 0.5
    assert tokens["T#1M30S"] == 90.0
    assert tokens["T#1D2H"] == 86400.0 + 7200.0
    assert tokens["TIME#250MS"] == 0.25


def test_based_and_typed_literals():
    values = [t.value for t in tokenize("16#FF 2#1010 8#17 INT#12 REAL#1.5 BOOL#TRUE") if t.kind != "EOF"]
    assert values == [255, 10, 15, 12, 1.5, True]


def test_comments_and_pragmas_are_skipped():
    tokens = tokenize("(* a *) x // b\n/* c */ y {attribute 'q'} z")
    assert [t.value for t in tokens if t.kind == "IDENT"] == ["X", "Y", "Z"]


def test_string_escapes_use_the_dollar_form():
    (token,) = [t for t in tokenize("'a$'b$52c$N'") if t.kind == "STRING"]
    assert token.value == "a'bRc\n"


def test_unterminated_comment_is_a_syntax_error():
    with pytest.raises(STSyntaxError):
        tokenize("(* never closed")


# --------------------------------------------------------------- parser ----


def test_case_labels_do_not_swallow_the_next_branch():
    runtime = run(
        wrap(
            """
CASE i OF
    1: r := 1.0;
    2, 3: r := 2.0;
    4..6: r := 3.0;
ELSE
    r := 9.0;
END_CASE
""",
            declarations="    i : INT := 5;\n    r : REAL;",
        )
    )
    assert runtime.get_path("PRG_TEST.r") == 3.0


def test_assignment_in_a_case_branch_is_not_read_as_a_label():
    runtime = run(
        wrap(
            """
CASE i OF
    1:
        r := 1.0;
        r := r + 1.0;
END_CASE
""",
            declarations="    i : INT := 1;\n    r : REAL;",
        )
    )
    assert runtime.get_path("PRG_TEST.r") == 2.0


def test_missing_end_if_is_reported_with_a_line():
    with pytest.raises(STSyntaxError) as excinfo:
        run(wrap("IF TRUE THEN\n    gDummy := TRUE;\n"))
    assert excinfo.value.line is not None


def test_unknown_type_is_a_declaration_error():
    with pytest.raises(STDeclarationError):
        run(wrap("gDummy := FALSE;", declarations="    x : T_Nope;"))


# ------------------------------------------------------------ arithmetic ----


def test_integer_division_truncates_towards_zero():
    runtime = run(
        wrap(
            "a := 7 / 2; b := -7 / 2; c := 7 / -2;",
            declarations="    a : DINT;\n    b : DINT;\n    c : DINT;",
        )
    )
    assert runtime.get_path("PRG_TEST.a") == 3
    assert runtime.get_path("PRG_TEST.b") == -3
    assert runtime.get_path("PRG_TEST.c") == -3


def test_mod_takes_the_sign_of_the_dividend():
    runtime = run(
        wrap(
            "a := 7 MOD 3; b := -7 MOD 3; c := 7 MOD -3;",
            declarations="    a : DINT;\n    b : DINT;\n    c : DINT;",
        )
    )
    assert runtime.get_path("PRG_TEST.a") == 1
    assert runtime.get_path("PRG_TEST.b") == -1
    assert runtime.get_path("PRG_TEST.c") == 1


def test_int_overflow_wraps_like_a_plc():
    runtime = run(wrap("a := 32767; a := a + 1;", declarations="    a : INT;"))
    assert runtime.get_path("PRG_TEST.a") == -32768


def test_real_to_int_rounds_to_nearest():
    runtime = run(
        wrap(
            "a := REAL_TO_INT(2.5); b := REAL_TO_INT(-2.5); c := REAL_TO_INT(2.4);",
            declarations="    a : INT;\n    b : INT;\n    c : INT;",
        )
    )
    assert (runtime.get_path("PRG_TEST.a"), runtime.get_path("PRG_TEST.b")) == (3, -3)
    assert runtime.get_path("PRG_TEST.c") == 2


def test_division_by_zero_raises_with_a_line_number():
    with pytest.raises(STRuntimeError) as excinfo:
        run(wrap("a := 1 / b;", declarations="    a : DINT;\n    b : DINT;"))
    assert "division by zero" in str(excinfo.value)
    assert excinfo.value.line is not None


def test_operator_precedence_matches_the_standard():
    runtime = run(
        wrap(
            "a := 2 + 3 * 4; b := SEL(TRUE, 0.0, 1.0); c := 2.0 ** 3.0 ** 2.0;",
            declarations="    a : DINT;\n    b : REAL;\n    c : REAL;",
        )
    )
    assert runtime.get_path("PRG_TEST.a") == 14
    assert runtime.get_path("PRG_TEST.b") == 1.0
    # ** is right associative: 2 ** (3 ** 2)
    assert runtime.get_path("PRG_TEST.c") == 512.0


def test_boolean_operators_are_logical_on_bools_and_bitwise_on_words():
    runtime = run(
        wrap(
            "x := TRUE AND FALSE; y := TRUE XOR FALSE; w := 12 AND 10;",
            declarations="    x : BOOL;\n    y : BOOL;\n    w : WORD;",
        )
    )
    assert runtime.get_path("PRG_TEST.x") is False
    assert runtime.get_path("PRG_TEST.y") is True
    assert runtime.get_path("PRG_TEST.w") == 8


# -------------------------------------------------------- standard blocks ----

TIMER_PROGRAM = """
VAR_GLOBAL
    gIn : BOOL;
    gQ : BOOL;
    gEt : TIME;
END_VAR
PROGRAM PRG_Test
VAR
    fb : {0};
END_VAR
fb(IN := gIn, PT := T#1s);
gQ := fb.Q;
gEt := fb.ET;
END_PROGRAM
"""


def test_ton_waits_for_the_preset_then_holds():
    runtime = Runtime()
    runtime.load_source(TIMER_PROGRAM.format("TON"), "t.st")
    runtime.build(["PRG_Test"])
    runtime.set_path("gIn", True)
    for _ in range(9):
        runtime.scan(0.1)
    assert runtime.get_path("gQ") is False
    for _ in range(3):
        runtime.scan(0.1)
    assert runtime.get_path("gQ") is True
    assert runtime.get_path("gEt") == pytest.approx(1.0)
    runtime.set_path("gIn", False)
    runtime.scan(0.1)
    assert runtime.get_path("gQ") is False
    assert runtime.get_path("gEt") == 0.0


def test_tof_holds_the_output_after_the_input_drops():
    runtime = Runtime()
    runtime.load_source(TIMER_PROGRAM.format("TOF"), "t.st")
    runtime.build(["PRG_Test"])
    runtime.set_path("gIn", True)
    runtime.scan(0.1)
    assert runtime.get_path("gQ") is True
    runtime.set_path("gIn", False)
    for _ in range(5):
        runtime.scan(0.1)
    assert runtime.get_path("gQ") is True
    for _ in range(7):
        runtime.scan(0.1)
    assert runtime.get_path("gQ") is False


def test_tp_produces_one_pulse_per_rising_edge():
    runtime = Runtime()
    runtime.load_source(TIMER_PROGRAM.format("TP"), "t.st")
    runtime.build(["PRG_Test"])
    runtime.set_path("gIn", True)
    runtime.scan(0.1)
    assert runtime.get_path("gQ") is True
    runtime.set_path("gIn", False)
    for _ in range(3):
        runtime.scan(0.1)
    # The pulse keeps running after IN drops.
    assert runtime.get_path("gQ") is True
    for _ in range(10):
        runtime.scan(0.1)
    assert runtime.get_path("gQ") is False


def test_timers_are_independent_of_the_scan_period():
    fast = Runtime()
    fast.load_source(TIMER_PROGRAM.format("TON"), "t.st")
    fast.build(["PRG_Test"])
    fast.set_path("gIn", True)
    slow = Runtime()
    slow.load_source(TIMER_PROGRAM.format("TON"), "t.st")
    slow.build(["PRG_Test"])
    slow.set_path("gIn", True)
    for _ in range(60):
        fast.scan(0.02)
    for _ in range(12):
        slow.scan(0.1)
    assert fast.get_path("gQ") == slow.get_path("gQ") is True


def test_edge_detection_and_counters():
    runtime = Runtime()
    runtime.load_source(
        """
VAR_GLOBAL
    gClk : BOOL;
    gRise : BOOL;
    gFall : BOOL;
    gCount : INT;
    gDone : BOOL;
END_VAR
PROGRAM PRG_Test
VAR
    r : R_TRIG;
    f : F_TRIG;
    c : CTU;
END_VAR
r(CLK := gClk);
f(CLK := gClk);
c(CU := r.Q, R := FALSE, PV := 3);
gRise := r.Q;
gFall := f.Q;
gCount := c.CV;
gDone := c.Q;
END_PROGRAM
""",
        "t.st",
    )
    runtime.build(["PRG_Test"])
    for _ in range(4):
        runtime.set_path("gClk", True)
        runtime.scan(0.1)
        assert runtime.get_path("gRise") is True
        runtime.scan(0.1)
        assert runtime.get_path("gRise") is False
        runtime.set_path("gClk", False)
        runtime.scan(0.1)
        assert runtime.get_path("gFall") is True
    assert runtime.get_path("gCount") == 4
    assert runtime.get_path("gDone") is True


def test_sr_is_set_dominant_and_rs_is_reset_dominant():
    runtime = Runtime()
    runtime.load_source(
        """
VAR_GLOBAL
    gSr : BOOL;
    gRs : BOOL;
END_VAR
PROGRAM PRG_Test
VAR
    sr : SR;
    rs : RS;
END_VAR
sr(SET1 := TRUE, RESET := TRUE);
rs(SET := TRUE, RESET1 := TRUE);
gSr := sr.Q1;
gRs := rs.Q1;
END_PROGRAM
""",
        "t.st",
    )
    runtime.build(["PRG_Test"])
    runtime.scan(0.1)
    assert runtime.get_path("gSr") is True
    assert runtime.get_path("gRs") is False


# --------------------------------------------- structures, arrays, calls ----


def test_structs_arrays_functions_and_in_out():
    runtime = Runtime()
    runtime.load_source(
        """
TYPE E_Mode : (M_OFF, M_ON); END_TYPE
TYPE ST_Item : STRUCT value : REAL := 1.0; flag : BOOL; END_STRUCT END_TYPE
VAR_GLOBAL
    gItems : ARRAY[1..3] OF ST_Item;
    gTotal : REAL;
    gMode : E_Mode;
    gScaled : REAL;
END_VAR
FUNCTION Scale : REAL
VAR_INPUT x : REAL; k : REAL; END_VAR
    Scale := x * k;
END_FUNCTION
FUNCTION_BLOCK FB_Accumulate
VAR_INPUT step : REAL; END_VAR
VAR_IN_OUT total : REAL; END_VAR
VAR_OUTPUT count : INT; END_VAR
    total := total + step;
    count := count + 1;
END_FUNCTION_BLOCK
PROGRAM PRG_Test
VAR
    i : INT;
    acc : FB_Accumulate;
END_VAR
FOR i := 1 TO 3 DO
    gItems[i].value := INT_TO_REAL(i) * 2.0;
END_FOR
gItems[2].flag := TRUE;
acc(step := gItems[3].value, total := gTotal);
gScaled := Scale(gTotal, 0.5);
gMode := M_ON;
END_PROGRAM
""",
        "t.st",
    )
    runtime.build(["PRG_Test"])
    runtime.scan(0.1)
    assert runtime.get_path("gItems[1].value") == 2.0
    assert runtime.get_path("gItems[2].flag") is True
    assert runtime.get_path("gItems[3].value") == 6.0
    assert runtime.get_path("gTotal") == 6.0
    assert runtime.get_path("gScaled") == 3.0
    assert runtime.get_path("gMode") == 1
    runtime.scan(0.1)
    # VAR_IN_OUT writes through to the caller's variable, so it keeps growing.
    assert runtime.get_path("gTotal") == 12.0
    assert runtime.get_path("PRG_TEST.acc.count") == 2


def test_struct_assignment_copies_by_value():
    runtime = Runtime()
    runtime.load_source(
        """
TYPE ST_Item : STRUCT value : REAL; END_STRUCT END_TYPE
VAR_GLOBAL
    gA : ST_Item;
    gB : ST_Item;
END_VAR
PROGRAM PRG_Test
gA.value := 5.0;
gB := gA;
gA.value := 7.0;
END_PROGRAM
""",
        "t.st",
    )
    runtime.build(["PRG_Test"])
    runtime.scan(0.1)
    assert runtime.get_path("gB.value") == 5.0
    assert runtime.get_path("gA.value") == 7.0


def test_array_index_out_of_range_is_caught():
    with pytest.raises(STRuntimeError) as excinfo:
        run(
            wrap(
                "i := 4; a[i] := 1.0;",
                declarations="    i : INT;\n    a : ARRAY[1..3] OF REAL;",
            )
        )
    assert "outside 1..3" in str(excinfo.value)


def test_enum_assignment_rejects_a_value_outside_the_type():
    with pytest.raises(STRuntimeError):
        run(
            """
TYPE E_Mode : (M_OFF, M_ON); END_TYPE
VAR_GLOBAL gMode : E_Mode; END_VAR
PROGRAM PRG_Test
VAR i : INT := 7; END_VAR
gMode := i;
END_PROGRAM
"""
        )


def test_loops_and_exit():
    runtime = run(
        wrap(
            """
total := 0;
i := 0;
WHILE i < 10 DO
    i := i + 1;
    IF i > 5 THEN
        EXIT;
    END_IF
    total := total + i;
END_WHILE
j := 0;
REPEAT
    j := j + 2;
UNTIL j >= 6
END_REPEAT
""",
            declarations="    i : INT;\n    j : INT;\n    total : INT;",
        )
    )
    assert runtime.get_path("PRG_TEST.total") == 15
    assert runtime.get_path("PRG_TEST.j") == 6


def test_standard_maths_functions():
    runtime = run(
        wrap(
            "a := SQRT(9.0); b := LIMIT(0.0, 12.0, 10.0); c := MAX(1.0, 7.0, 3.0); "
            "d := ABS(-4.5); e := EXPT(2.0, 10.0);",
            declarations="    a : REAL;\n    b : REAL;\n    c : REAL;\n    d : REAL;\n    e : REAL;",
        )
    )
    assert runtime.get_path("PRG_TEST.a") == 3.0
    assert runtime.get_path("PRG_TEST.b") == 10.0
    assert runtime.get_path("PRG_TEST.c") == 7.0
    assert runtime.get_path("PRG_TEST.d") == 4.5
    assert runtime.get_path("PRG_TEST.e") == pytest.approx(1024.0)


def test_time_to_real_reports_milliseconds():
    runtime = run(
        wrap("a := TIME_TO_REAL(T#1s500ms);", declarations="    a : REAL;"),
    )
    assert runtime.get_path("PRG_TEST.a") == pytest.approx(1500.0)


def test_time_in_seconds_does_not_depend_on_the_conversion_unit():
    """plc/fn/Fn_Time.st divides by the conversion of T#1s, so the answer is
    seconds here (milliseconds underneath) and under RuSTy (nanoseconds)."""
    fn_time = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plc", "fn", "Fn_Time.st")
    with open(fn_time, "r", encoding="utf-8") as handle:
        library = handle.read()
    runtime = run(
        library
        + wrap(
            "a := TimeToSeconds(T#2m30s); t := SecondsToTime(a * 2.0);",
            declarations="    a : REAL;\n    t : TIME;",
        )
    )
    assert runtime.get_path("PRG_TEST.a") == pytest.approx(150.0)
    assert runtime.get_path("PRG_TEST.t") == pytest.approx(300.0)


def test_to_int_publishes_an_enumeration_as_its_value():
    runtime = run(
        "TYPE E_Ph : (PH_IDLE := 0, PH_ACID_WASH := 4); END_TYPE\n"
        + wrap("i := TO_INT(PH_ACID_WASH);", declarations="    i : INT;")
    )
    assert runtime.get_path("PRG_TEST.i") == 4


def test_programs_execute_in_the_configured_order():
    source = """
VAR_GLOBAL gTrace : DINT; END_VAR
PROGRAM PRG_First
gTrace := gTrace * 10 + 1;
END_PROGRAM
PROGRAM PRG_Second
gTrace := gTrace * 10 + 2;
END_PROGRAM
"""
    forwards = Runtime()
    forwards.load_source(source, "t.st")
    forwards.build(["PRG_First", "PRG_Second"])
    forwards.scan(0.1)
    backwards = Runtime()
    backwards.load_source(source, "t.st")
    backwards.build(["PRG_Second", "PRG_First"])
    backwards.scan(0.1)
    assert forwards.get_path("gTrace") == 12
    assert backwards.get_path("gTrace") == 21


def test_simulated_clock_only_moves_when_a_scan_says_so():
    runtime = run(wrap("gDummy := TRUE;"), scans=0)
    assert runtime.now == 0.0
    runtime.scan(0.25)
    runtime.scan(0.25)
    assert runtime.now == pytest.approx(0.5)
    assert runtime.scan_count == 2


# -------------------------------------------------------------- addresses ----


@pytest.mark.parametrize(
    "text,prefix,size,word,bit",
    [
        ("%IX0.0", "I", "X", 0, 0),
        ("%IX1.5", "I", "X", 1, 5),
        ("%QX2.7", "Q", "X", 2, 7),
        ("%ID10", "I", "D", 10, 0),
        ("%MW3", "M", "W", 3, 0),
        ("%MD12", "M", "D", 12, 0),
    ],
)
def test_address_parsing(text, prefix, size, word, bit):
    address = parse_address(text)
    assert (address.prefix, address.size, address.word, address.bit) == (prefix, size, word, bit)


def test_bit_index_above_seven_is_rejected():
    with pytest.raises(STDeclarationError):
        parse_address("%IX0.8")


def test_located_variables_are_collected_from_the_declarations():
    runtime = run(
        """
VAR_GLOBAL
    xIn AT %IX0.3 : BOOL;
    rOut AT %QD2 : REAL;
    iWord AT %MW7 : INT;
END_VAR
PROGRAM PRG_Test
rOut := 1.0;
END_PROGRAM
"""
    )
    assert set(runtime.located) == {"XIN", "ROUT", "IWORD"}
    assert runtime.located["ROUT"].address.text == "%QD2"
    assert math.isclose(runtime.get_path("rOut"), 1.0)
