"""Runtime type system.

Values are represented with plain Python objects: BOOL is bool, the integer
types are int, REAL/LREAL are float, TIME is a float count of seconds, STRING is
str, a struct is a StructValue and an array is a list. Every assignment goes
through `coerce`, which is where integer wrap-around and REAL-to-INT rounding
happen, so a program cannot smuggle a float into a DINT the way it could if the
interpreter just used Python semantics.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .errors import STRuntimeError

# name -> (bit width, signed)
INTEGER_TYPES: Dict[str, Tuple[int, bool]] = {
    "SINT": (8, True),
    "INT": (16, True),
    "DINT": (32, True),
    "LINT": (64, True),
    "USINT": (8, False),
    "UINT": (16, False),
    "UDINT": (32, False),
    "ULINT": (64, False),
    "BYTE": (8, False),
    "WORD": (16, False),
    "DWORD": (32, False),
    "LWORD": (64, False),
}

REAL_TYPES = frozenset(["REAL", "LREAL"])
ELEMENTARY = frozenset(list(INTEGER_TYPES) + list(REAL_TYPES) + ["BOOL", "TIME", "STRING"])


class Type:
    name = "?"

    def default(self) -> Any:
        raise NotImplementedError

    def __repr__(self) -> str:
        return "<{0} {1}>".format(type(self).__name__, self.name)


class ElementaryType(Type):
    def __init__(self, name: str, string_len: Optional[int] = None):
        self.name = name
        self.string_len = string_len

    def default(self) -> Any:
        if self.name == "BOOL":
            return False
        if self.name == "STRING":
            return ""
        if self.name in REAL_TYPES or self.name == "TIME":
            return 0.0
        return 0


class EnumType(Type):
    def __init__(self, name: str, members: Sequence[Tuple[str, int]]):
        self.name = name
        self.members = dict(members)
        self.by_value = {}
        for member_name, value in members:
            self.by_value.setdefault(value, member_name)
        self.first = members[0][1] if members else 0

    def default(self) -> Any:
        return self.first

    def label(self, value: int) -> str:
        return self.by_value.get(int(value), str(value))


class StructType(Type):
    def __init__(self, name: str):
        self.name = name
        self.fields: "Dict[str, Type]" = {}
        self.field_inits: Dict[str, Any] = {}
        self.field_order: List[str] = []

    def add_field(self, name: str, field_type: Type, init: Any = None) -> None:
        self.fields[name] = field_type
        self.field_inits[name] = init
        self.field_order.append(name)

    def default(self) -> "StructValue":
        value = StructValue(self)
        for field in self.field_order:
            value[field] = self.fields[field].default()
        return value


class ArrayType(Type):
    def __init__(self, dims: Sequence[Tuple[int, int]], element: Type):
        self.dims = tuple(dims)
        self.element = element
        self.name = "ARRAY[{0}] OF {1}".format(
            ", ".join("{0}..{1}".format(lo, hi) for lo, hi in self.dims), element.name
        )

    def default(self) -> Any:
        return self._build(0)

    def _build(self, depth: int) -> Any:
        lo, hi = self.dims[depth]
        count = hi - lo + 1
        if depth == len(self.dims) - 1:
            return [self.element.default() for _ in range(count)]
        return [self._build(depth + 1) for _ in range(count)]


class FunctionBlockType(Type):
    """A user-declared FUNCTION_BLOCK. Instances are created by the runtime."""

    def __init__(self, name: str, pou: Any):
        self.name = name
        self.pou = pou

    def default(self) -> Any:  # instantiated by Runtime, never by the type
        raise STRuntimeError("function block {0} must be instantiated".format(self.name))


class NativeBlockType(Type):
    """A standard block (TON, R_TRIG, ...) implemented in Python."""

    def __init__(self, name: str, factory: Any):
        self.name = name
        self.factory = factory

    def default(self) -> Any:
        raise STRuntimeError("native block {0} must be instantiated".format(self.name))


class StructValue(dict):
    """A STRUCT instance. Members are reachable by name, case folded."""

    def __init__(self, struct_type: StructType):
        super().__init__()
        self.type = struct_type

    def copy(self) -> "StructValue":
        clone = StructValue(self.type)
        for key, value in self.items():
            clone[key] = value.copy() if isinstance(value, StructValue) else (
                list(value) if isinstance(value, list) else value
            )
        return clone


def wrap_int(value: int, bits: int, signed: bool) -> int:
    span = 1 << bits
    value &= span - 1
    if signed and value >= span >> 1:
        value -= span
    return value


def coerce(target: Type, value: Any, where: str = "") -> Any:
    """Convert `value` to the representation `target` demands."""
    if isinstance(target, ElementaryType):
        name = target.name
        if name == "BOOL":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value != 0
            raise STRuntimeError("cannot assign {0!r} to BOOL {1}".format(value, where))
        if name in INTEGER_TYPES:
            if isinstance(value, bool):
                number = 1 if value else 0
            elif isinstance(value, float):
                # IEC rounds to nearest on REAL -> integer conversion.
                number = int(_round_half_up(value))
            elif isinstance(value, int):
                number = value
            else:
                raise STRuntimeError("cannot assign {0!r} to {1} {2}".format(value, name, where))
            bits, signed = INTEGER_TYPES[name]
            return wrap_int(number, bits, signed)
        if name in REAL_TYPES or name == "TIME":
            if isinstance(value, bool):
                return 1.0 if value else 0.0
            if isinstance(value, (int, float)):
                return float(value)
            raise STRuntimeError("cannot assign {0!r} to {1} {2}".format(value, name, where))
        if name == "STRING":
            if not isinstance(value, str):
                raise STRuntimeError("cannot assign {0!r} to STRING {1}".format(value, where))
            if target.string_len is not None:
                return value[: target.string_len]
            return value
        raise STRuntimeError("unknown elementary type {0}".format(name))

    if isinstance(target, EnumType):
        if isinstance(value, bool):
            raise STRuntimeError("cannot assign BOOL to enum {0} {1}".format(target.name, where))
        if isinstance(value, (int, float)):
            number = int(value)
            if number not in target.by_value:
                raise STRuntimeError(
                    "{0} is not a member of {1} {2}".format(number, target.name, where)
                )
            return number
        raise STRuntimeError("cannot assign {0!r} to enum {1} {2}".format(value, target.name, where))

    if isinstance(target, StructType):
        if isinstance(value, StructValue) and value.type is target:
            return value.copy()
        raise STRuntimeError("cannot assign {0!r} to struct {1} {2}".format(value, target.name, where))

    if isinstance(target, ArrayType):
        if isinstance(value, list):
            return list(value)
        raise STRuntimeError("cannot assign {0!r} to {1} {2}".format(value, target.name, where))

    raise STRuntimeError("cannot assign to {0} {1}".format(target.name, where))


def _round_half_up(value: float) -> float:
    if value >= 0:
        return float(int(value + 0.5))
    return float(-int(-value + 0.5))


def type_of_literal(kind: str) -> Type:
    return {
        "BOOL": ElementaryType("BOOL"),
        "NUMBER": ElementaryType("DINT"),
        "REAL": ElementaryType("LREAL"),
        "TIME": ElementaryType("TIME"),
        "STRING": ElementaryType("STRING"),
    }[kind]


def copy_into(dest: "StructValue", src: "StructValue") -> "StructValue":
    """Copy a structure field by field into an existing instance.

    Structure assignment in IEC 61131-3 is by value. Copying into the existing
    StructValue rather than replacing it keeps the object identity stable,
    which is what lets a reference to one of its fields stay valid.
    """
    for key, value in src.items():
        if isinstance(value, StructValue):
            target = dest.get(key)
            if isinstance(target, StructValue):
                copy_into(target, value)
            else:
                dest[key] = value.copy()
        elif isinstance(value, list):
            dest[key] = list(value)
        else:
            dest[key] = value
    return dest


def make_converter(target: Type, where: str = "") -> Callable[[Any, Any], Any]:
    """Build the assignment conversion for one declared type.

    Specialising once per variable rather than dispatching on the type at every
    assignment is worth roughly a fifth of the scan time.
    """
    if isinstance(target, ElementaryType):
        name = target.name
        if name == "BOOL":

            def to_bool(current: Any, value: Any) -> Any:
                if value is True or value is False:
                    return value
                if isinstance(value, (int, float)):
                    return value != 0
                raise STRuntimeError("cannot assign {0!r} to BOOL {1}".format(value, where))

            return to_bool

        if name in REAL_TYPES or name == "TIME":

            def to_real(current: Any, value: Any) -> Any:
                if value.__class__ is float:
                    return value
                if value is True:
                    return 1.0
                if value is False:
                    return 0.0
                if isinstance(value, (int, float)):
                    return float(value)
                raise STRuntimeError("cannot assign {0!r} to {1} {2}".format(value, name, where))

            return to_real

        if name in INTEGER_TYPES:
            bits, signed = INTEGER_TYPES[name]

            def to_int(current: Any, value: Any) -> Any:
                if value is True:
                    number = 1
                elif value is False:
                    number = 0
                elif value.__class__ is int:
                    number = value
                elif isinstance(value, float):
                    number = int(_round_half_up(value))
                elif isinstance(value, int):
                    number = int(value)
                else:
                    raise STRuntimeError(
                        "cannot assign {0!r} to {1} {2}".format(value, name, where)
                    )
                return wrap_int(number, bits, signed)

            return to_int

    if isinstance(target, StructType):

        def to_struct(current: Any, value: Any) -> Any:
            if isinstance(value, StructValue) and value.type is target:
                if isinstance(current, StructValue) and current.type is target:
                    return copy_into(current, value)
                return value.copy()
            raise STRuntimeError(
                "cannot assign {0!r} to structure {1} {2}".format(value, target.name, where)
            )

        return to_struct

    def general(current: Any, value: Any) -> Any:
        return coerce(target, value, where)

    return general
