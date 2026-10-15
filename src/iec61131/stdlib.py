"""The standard function blocks and functions the ST programs rely on.

Timers read the runtime clock rather than integrating a scan delta, so a timer
behaves identically whether the harness runs a 20 ms or a 100 ms scan. TIME
values are seconds internally; TIME_TO_REAL still reports milliseconds, which is
what the vendor toolchains do.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from .errors import STRuntimeError
from .types import ElementaryType, Type, coerce

_TYPE_CACHE: Dict[str, Type] = {}


def elem(name: str) -> Type:
    if name not in _TYPE_CACHE:
        _TYPE_CACHE[name] = ElementaryType(name)
    return _TYPE_CACHE[name]


class NativeFB:
    """Base class for a standard function block instance."""

    NAME = ""
    INPUTS: Dict[str, str] = {}
    OUTPUTS: Dict[str, str] = {}

    def __init__(self) -> None:
        self.values: Dict[str, Any] = {}
        for name, type_name in self.INPUTS.items():
            self.values[name] = elem(type_name).default()
        for name, type_name in self.OUTPUTS.items():
            self.values[name] = elem(type_name).default()

    # The runtime treats a native block like any other FB instance.
    def member_type(self, name: str) -> Type:
        if name in self.INPUTS:
            return elem(self.INPUTS[name])
        if name in self.OUTPUTS:
            return elem(self.OUTPUTS[name])
        raise STRuntimeError("{0} has no member {1}".format(self.NAME, name))

    def get(self, name: str) -> Any:
        if name not in self.values:
            raise STRuntimeError("{0} has no member {1}".format(self.NAME, name))
        return self.values[name]

    def set(self, name: str, value: Any) -> None:
        self.values[name] = coerce(self.member_type(name), value, where=self.NAME + "." + name)

    def positional_inputs(self) -> List[str]:
        return list(self.INPUTS.keys())

    def execute(self, rt: Any) -> None:
        raise NotImplementedError


class TON(NativeFB):
    NAME = "TON"
    INPUTS = {"IN": "BOOL", "PT": "TIME"}
    OUTPUTS = {"Q": "BOOL", "ET": "TIME"}

    def __init__(self) -> None:
        super().__init__()
        self._start = None

    def execute(self, rt: Any) -> None:
        if not self.values["IN"]:
            self._start = None
            self.values["Q"] = False
            self.values["ET"] = 0.0
            return
        if self._start is None:
            self._start = rt.now
        elapsed = min(rt.now - self._start, self.values["PT"])
        self.values["ET"] = elapsed
        self.values["Q"] = elapsed >= self.values["PT"]


class TOF(NativeFB):
    NAME = "TOF"
    INPUTS = {"IN": "BOOL", "PT": "TIME"}
    OUTPUTS = {"Q": "BOOL", "ET": "TIME"}

    def __init__(self) -> None:
        super().__init__()
        self._start = None

    def execute(self, rt: Any) -> None:
        if self.values["IN"]:
            self._start = None
            self.values["Q"] = True
            self.values["ET"] = 0.0
            return
        if self.values["Q"] and self._start is None:
            self._start = rt.now
        if self._start is None:
            return
        elapsed = min(rt.now - self._start, self.values["PT"])
        self.values["ET"] = elapsed
        if elapsed >= self.values["PT"]:
            self.values["Q"] = False


class TP(NativeFB):
    NAME = "TP"
    INPUTS = {"IN": "BOOL", "PT": "TIME"}
    OUTPUTS = {"Q": "BOOL", "ET": "TIME"}

    def __init__(self) -> None:
        super().__init__()
        self._start = None
        self._prev_in = False

    def execute(self, rt: Any) -> None:
        in_now = bool(self.values["IN"])
        if self._start is not None:
            elapsed = min(rt.now - self._start, self.values["PT"])
            self.values["ET"] = elapsed
            if elapsed >= self.values["PT"]:
                self.values["Q"] = False
                self._start = None
                if not in_now:
                    self.values["ET"] = 0.0
        else:
            if in_now and not self._prev_in:
                self._start = rt.now
                self.values["Q"] = True
                self.values["ET"] = 0.0
            elif not in_now:
                self.values["ET"] = 0.0
        self._prev_in = in_now


class R_TRIG(NativeFB):
    NAME = "R_TRIG"
    INPUTS = {"CLK": "BOOL"}
    OUTPUTS = {"Q": "BOOL"}

    def __init__(self) -> None:
        super().__init__()
        self._prev = False

    def execute(self, rt: Any) -> None:
        clk = bool(self.values["CLK"])
        self.values["Q"] = clk and not self._prev
        self._prev = clk


class F_TRIG(NativeFB):
    NAME = "F_TRIG"
    INPUTS = {"CLK": "BOOL"}
    OUTPUTS = {"Q": "BOOL"}

    def __init__(self) -> None:
        super().__init__()
        self._prev = False

    def execute(self, rt: Any) -> None:
        clk = bool(self.values["CLK"])
        self.values["Q"] = self._prev and not clk
        self._prev = clk


class CTU(NativeFB):
    NAME = "CTU"
    INPUTS = {"CU": "BOOL", "R": "BOOL", "PV": "INT"}
    OUTPUTS = {"Q": "BOOL", "CV": "INT"}

    def __init__(self) -> None:
        super().__init__()
        self._prev_cu = False

    def execute(self, rt: Any) -> None:
        cu = bool(self.values["CU"])
        if self.values["R"]:
            self.values["CV"] = 0
        elif cu and not self._prev_cu and self.values["CV"] < 32767:
            self.values["CV"] += 1
        self.values["Q"] = self.values["CV"] >= self.values["PV"]
        self._prev_cu = cu


class CTD(NativeFB):
    NAME = "CTD"
    INPUTS = {"CD": "BOOL", "LD": "BOOL", "PV": "INT"}
    OUTPUTS = {"Q": "BOOL", "CV": "INT"}

    def __init__(self) -> None:
        super().__init__()
        self._prev_cd = False

    def execute(self, rt: Any) -> None:
        cd = bool(self.values["CD"])
        if self.values["LD"]:
            self.values["CV"] = self.values["PV"]
        elif cd and not self._prev_cd and self.values["CV"] > -32768:
            self.values["CV"] -= 1
        self.values["Q"] = self.values["CV"] <= 0
        self._prev_cd = cd


class SR(NativeFB):
    NAME = "SR"
    INPUTS = {"SET1": "BOOL", "RESET": "BOOL"}
    OUTPUTS = {"Q1": "BOOL"}

    def execute(self, rt: Any) -> None:
        # Set dominant.
        self.values["Q1"] = bool(self.values["SET1"]) or (
            self.values["Q1"] and not self.values["RESET"]
        )


class RS(NativeFB):
    NAME = "RS"
    INPUTS = {"SET": "BOOL", "RESET1": "BOOL"}
    OUTPUTS = {"Q1": "BOOL"}

    def execute(self, rt: Any) -> None:
        # Reset dominant.
        self.values["Q1"] = (not self.values["RESET1"]) and (
            bool(self.values["SET"]) or self.values["Q1"]
        )


NATIVE_BLOCKS = {
    cls.NAME: cls for cls in (TON, TOF, TP, R_TRIG, F_TRIG, CTU, CTD, SR, RS)
}


# --------------------------------------------------------------------------
# Standard functions
# --------------------------------------------------------------------------


def _limit(minimum: Any, value: Any, maximum: Any) -> Any:
    return min(max(value, minimum), maximum)


def _sel(gate: Any, in0: Any, in1: Any) -> Any:
    return in1 if gate else in0


def _mux(k: Any, *choices: Any) -> Any:
    index = int(k)
    if index < 0 or index >= len(choices):
        raise STRuntimeError("MUX selector {0} out of range".format(index))
    return choices[index]


def _trunc(value: Any) -> int:
    return int(value)


def _round_half_up(value: float) -> int:
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def _div(a: Any, b: Any) -> Any:
    if b == 0:
        raise STRuntimeError("division by zero")
    if isinstance(a, int) and isinstance(b, int):
        quotient = abs(a) // abs(b)
        return quotient if (a >= 0) == (b >= 0) else -quotient
    return a / b


def _concat(*parts: Any) -> str:
    return "".join(str(p) for p in parts)


def _mid(text: str, length: int, position: int) -> str:
    start = max(int(position) - 1, 0)
    return text[start : start + int(length)]


def _real_to_string(value: Any, decimals: int = 3) -> str:
    return "{0:.{1}f}".format(float(value), int(decimals))


STANDARD_FUNCTIONS = {
    "ABS": abs,
    "SQRT": lambda x: math.sqrt(x),
    "LN": lambda x: math.log(x),
    "LOG": lambda x: math.log10(x),
    "EXP": lambda x: math.exp(x),
    "SIN": math.sin,
    "COS": math.cos,
    "TAN": math.tan,
    "ASIN": math.asin,
    "ACOS": math.acos,
    "ATAN": math.atan,
    "EXPT": lambda a, b: a**b,
    "MIN": lambda *xs: min(xs),
    "MAX": lambda *xs: max(xs),
    "LIMIT": _limit,
    "SEL": _sel,
    "MUX": _mux,
    "MOVE": lambda x: x,
    "ADD": lambda *xs: sum(xs),
    "SUB": lambda a, b: a - b,
    "MUL": lambda *xs: _product(xs),
    "DIV": _div,
    "TRUNC": _trunc,
    "ROUND": lambda x: _round_half_up(float(x)),
    # Conversions.
    "REAL_TO_INT": lambda x: _round_half_up(float(x)),
    "REAL_TO_DINT": lambda x: _round_half_up(float(x)),
    "LREAL_TO_INT": lambda x: _round_half_up(float(x)),
    "LREAL_TO_DINT": lambda x: _round_half_up(float(x)),
    "INT_TO_REAL": float,
    "INT_TO_LREAL": float,
    "DINT_TO_REAL": float,
    "DINT_TO_LREAL": float,
    "INT_TO_DINT": int,
    "DINT_TO_INT": int,
    "BOOL_TO_INT": lambda x: 1 if x else 0,
    "INT_TO_BOOL": lambda x: x != 0,
    "REAL_TO_TIME": lambda ms: float(ms) / 1000.0,
    "TIME_TO_REAL": lambda t: float(t) * 1000.0,
    "TIME_TO_DINT": lambda t: _round_half_up(float(t) * 1000.0),
    "DINT_TO_TIME": lambda ms: float(ms) / 1000.0,
    # Strings.
    "CONCAT": _concat,
    "LEN": lambda s: len(s),
    "LEFT": lambda s, n: s[: int(n)],
    "RIGHT": lambda s, n: s[len(s) - int(n) :] if int(n) else "",
    "MID": _mid,
    "FIND": lambda s, sub: s.find(sub) + 1,
    "INT_TO_STRING": lambda x: str(int(x)),
    "DINT_TO_STRING": lambda x: str(int(x)),
    "BOOL_TO_STRING": lambda x: "TRUE" if x else "FALSE",
    "REAL_TO_STRING": _real_to_string,
    "LREAL_TO_STRING": _real_to_string,
}


def _product(values: Any) -> Any:
    total: Any = 1
    for value in values:
        total = total * value
    return total
