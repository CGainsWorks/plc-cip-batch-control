"""The same ST, compiled by RuSTy, behind the interpreter's interface.

`CompiledRuntime` loads the shared object that `scripts/rusty/compile_plc.sh`
builds from `plc/` with the RuSTy IEC 61131-3 compiler and exposes the four
things the harness uses from the interpreter: `get_path`, `set_path`, `scan`
and the located-variable table. `CipCell(..., backend="rusty")` then drives the
compiled program against the same plant model, the same Modbus server and the
same batch record code, so the two executions can be compared scan for scan.

What comes from where:

* The machine code, the memory layout and the I/O addresses come from RuSTy.
  The layout of every global is read out of the LLVM IR the compiler emitted
  and the addresses out of the hardware map it wrote; neither is assumed.
* Variable and field *names* come from the ST declarations, because LLVM IR
  keeps the order of structure members but not their names. Loading checks
  that the declarations and the IR agree member for member and refuses to run
  if they do not.
* Time comes from the RuSTy standard library built with its own `mock_time`
  feature, which replaces the monotonic clock behind TON, TOF and TP with one
  the caller advances. Each scan advances it by exactly one scan period, which
  is what makes a compiled run deterministic and lets an hour of plant run in
  seconds.

Linux only: RuSTy targets ELF here and the build runs in CI or in a container.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..iec61131.runtime import Runtime, load
from ..iec61131.types import ArrayType, ElementaryType, EnumType, StructType, Type

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_BUILD_DIR = os.path.join(REPO_ROOT, "build", "rusty")
LIBRARY = "libcip.so"
STDLIB = "libiec61131std.so"
IR_FILE = "cip.ll"
HWMAP_FILE = "libcip.hwmap.json"

NS_PER_S = 1_000_000_000

# Elementary IEC type -> (LLVM type RuSTy emits, ctypes type).
ELEMENTARY: Dict[str, Tuple[str, Any]] = {
    "BOOL": ("i8", ctypes.c_uint8),
    "SINT": ("i8", ctypes.c_int8),
    "USINT": ("i8", ctypes.c_uint8),
    "BYTE": ("i8", ctypes.c_uint8),
    "INT": ("i16", ctypes.c_int16),
    "UINT": ("i16", ctypes.c_uint16),
    "WORD": ("i16", ctypes.c_uint16),
    "DINT": ("i32", ctypes.c_int32),
    "UDINT": ("i32", ctypes.c_uint32),
    "DWORD": ("i32", ctypes.c_uint32),
    "LINT": ("i64", ctypes.c_int64),
    "ULINT": ("i64", ctypes.c_uint64),
    "LWORD": ("i64", ctypes.c_uint64),
    "REAL": ("float", ctypes.c_float),
    "LREAL": ("double", ctypes.c_double),
    "TIME": ("i64", ctypes.c_int64),  # nanoseconds in RuSTy
}
# An enumeration without an explicit base type is a DINT in RuSTy.
ENUM_LLVM, ENUM_CTYPE = "i32", ctypes.c_int32

_STRUCT_RE = re.compile(r"^%(?P<name>[A-Za-z_][\w.]*) = type \{ (?P<body>.*) \}$")
_GLOBAL_RE = re.compile(
    r"^@(?P<name>[A-Za-z_]\w*) = (?:(?:dso_local|unnamed_addr|local_unnamed_addr) )*"
    r"(?:global|constant) (?P<type>\[[^\]]*\]|%[\w.]+|i\d+|float|double|ptr)"
)
_PATH_RE = re.compile(r"^(?P<name>[A-Za-z_]\w*)(?P<subs>(?:\[\s*-?\d+\s*\])*)$")


class CompiledBuildMissing(RuntimeError):
    """The RuSTy build output is not there, or is not usable on this machine."""


class LayoutMismatch(RuntimeError):
    """The ST declarations and the compiled memory layout disagree."""


def build_dir() -> str:
    return os.environ.get("CIP_RUSTY_BUILD", DEFAULT_BUILD_DIR)


def available(directory: Optional[str] = None) -> bool:
    directory = directory or build_dir()
    return os.name == "posix" and all(
        os.path.exists(os.path.join(directory, name))
        for name in (LIBRARY, STDLIB, IR_FILE, HWMAP_FILE)
    )


# -- reading what the compiler emitted ---------------------------------------


def _split_top_level(body: str) -> List[str]:
    parts, depth, current = [], 0, []
    for char in body:
        if char in "[{<":
            depth += 1
        elif char in "]}>":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current).strip())
    return parts


def read_ir(path: str) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """Structure layouts and global types out of an LLVM IR listing."""
    structs: Dict[str, List[str]] = {}
    globals_: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            match = _STRUCT_RE.match(line)
            if match:
                structs[match.group("name")] = _split_top_level(match.group("body"))
                continue
            match = _GLOBAL_RE.match(line)
            if match:
                globals_[match.group("name")] = match.group("type")
    return structs, globals_


def read_hwmap(path: str) -> Dict[str, Tuple[str, str]]:
    """Located variable -> (address, symbol holding its value)."""
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return {
        entry["name"].upper(): (entry["address"].upper(), entry["mangled_name"])
        for entry in payload["VariableMap"]
    }


# -- layout -----------------------------------------------------------------


class _Layout:
    """ctypes types for the declared ST types, checked against the IR."""

    def __init__(self, ir_structs: Dict[str, List[str]]):
        self.ir_structs = ir_structs
        self.ir_by_upper = {name.upper(): name for name in ir_structs}
        self._ctypes: Dict[str, Any] = {}

    def llvm(self, type_obj: Type) -> str:
        if isinstance(type_obj, EnumType):
            return ENUM_LLVM
        if isinstance(type_obj, ElementaryType):
            if type_obj.name == "STRING":
                return "[{0} x i8]".format(_string_len(type_obj) + 1)
            return ELEMENTARY[type_obj.name][0]
        if isinstance(type_obj, StructType):
            return "%" + self._ir_struct_name(type_obj)
        if isinstance(type_obj, ArrayType):
            return _nest_array(type_obj.dims, self.llvm(type_obj.element))
        raise LayoutMismatch("no compiled layout for {0}".format(type_obj))

    def ctype(self, type_obj: Type) -> Any:
        if isinstance(type_obj, EnumType):
            return ENUM_CTYPE
        if isinstance(type_obj, ElementaryType):
            if type_obj.name == "STRING":
                return ctypes.c_char * (_string_len(type_obj) + 1)
            return ELEMENTARY[type_obj.name][1]
        if isinstance(type_obj, StructType):
            return self._struct(type_obj)
        if isinstance(type_obj, ArrayType):
            result = self.ctype(type_obj.element)
            for low, high in reversed(type_obj.dims):
                result = result * (high - low + 1)
            return result
        raise LayoutMismatch("no compiled layout for {0}".format(type_obj))

    def _ir_struct_name(self, type_obj: StructType) -> str:
        name = self.ir_by_upper.get(type_obj.name.upper())
        if name is None:
            raise LayoutMismatch("the compiler emitted no structure {0}".format(type_obj.name))
        return name

    def _struct(self, type_obj: StructType) -> Any:
        key = type_obj.name.upper()
        if key in self._ctypes:
            return self._ctypes[key]
        ir_name = self._ir_struct_name(type_obj)
        compiled = self.ir_structs[ir_name]
        declared = [self.llvm(type_obj.fields[f]) for f in type_obj.field_order]
        if compiled != declared:
            raise LayoutMismatch(
                "{0}: declared members {1} but the compiler laid out {2}".format(
                    type_obj.name, declared, compiled
                )
            )
        fields = [(f.upper(), self.ctype(type_obj.fields[f])) for f in type_obj.field_order]
        cls = type(str(ir_name), (ctypes.Structure,), {"_fields_": fields})
        self._ctypes[key] = cls
        return cls


def _string_len(type_obj: ElementaryType) -> int:
    return int(type_obj.string_len or 80)


def _nest_array(dims: Sequence[Tuple[int, int]], element: str) -> str:
    result = element
    for low, high in reversed(dims):
        result = "[{0} x {1}]".format(high - low + 1, result)
    return result


# -- references into the compiled image ---------------------------------------


class _Ref:
    """One value inside the compiled program's memory."""

    __slots__ = ("owner", "key", "type")

    def __init__(self, owner: Any, key: Any, type_obj: Type):
        self.owner = owner  # a ctypes Structure, Array, or scalar cell (key None)
        self.key = key
        self.type = type_obj

    def raw(self) -> Any:
        if self.key is None:
            return self.owner.value if hasattr(self.owner, "value") else self.owner
        if isinstance(self.key, int):
            return self.owner[self.key]
        return getattr(self.owner, self.key)

    def get(self) -> Any:
        value = self.raw()
        type_obj = self.type
        if isinstance(type_obj, EnumType):
            return int(value)
        if isinstance(type_obj, ElementaryType):
            name = type_obj.name
            if name == "BOOL":
                return bool(value)
            if name == "TIME":
                return int(value) / NS_PER_S
            if name in ("REAL", "LREAL"):
                return float(value)
            if name == "STRING":
                raw = value if isinstance(value, bytes) else bytes(value)
                return raw.split(b"\0", 1)[0].decode("latin-1")
            return int(value)
        raise TypeError("{0} is not a scalar".format(type_obj))

    def set(self, value: Any) -> None:
        type_obj = self.type
        name = getattr(type_obj, "name", "")
        if isinstance(type_obj, EnumType):
            raw: Any = int(value)
        elif isinstance(type_obj, ElementaryType):
            if name == "BOOL":
                raw = 1 if value else 0
            elif name == "TIME":
                raw = int(round(float(value) * NS_PER_S))
            elif name in ("REAL", "LREAL"):
                raw = float(value)
            elif name == "STRING":
                limit = _string_len(type_obj)
                raw = str(value).encode("latin-1")[:limit]
            else:
                raw = int(value)
        else:
            raise TypeError("{0} is not a scalar".format(type_obj))
        if self.key is None:
            if isinstance(type_obj, ElementaryType) and name == "STRING":
                ctypes.memset(ctypes.addressof(self.owner), 0, ctypes.sizeof(self.owner))
                ctypes.memmove(ctypes.addressof(self.owner), raw, len(raw))
            else:
                self.owner.value = raw
        elif isinstance(self.key, int):
            self.owner[self.key] = raw
        else:
            setattr(self.owner, self.key, raw)

    def child(self, part: str, subscripts: List[int], path: str) -> "_Ref":
        container = self.raw()
        type_obj = self.type
        if not isinstance(type_obj, StructType):
            raise KeyError("{0} is not a structure".format(path))
        if part not in type_obj.fields:
            raise KeyError("{0} has no member {1}".format(type_obj.name, part))
        ref = _Ref(container, part, type_obj.fields[part])
        return ref.index(subscripts, path)

    def index(self, subscripts: List[int], path: str) -> "_Ref":
        ref = self
        for subscript in subscripts:
            array_type = ref.type
            if not isinstance(array_type, ArrayType):
                raise KeyError("{0} is not an array".format(path))
            low, high = array_type.dims[0]
            if subscript < low or subscript > high:
                raise KeyError("index {0} outside {1}..{2} in {3}".format(subscript, low, high, path))
            remaining = array_type.dims[1:]
            element = (
                array_type.element if not remaining else ArrayType(remaining, array_type.element)
            )
            ref = _Ref(ref.raw(), subscript - low, element)
        return ref


def _split(part: str, path: str) -> Tuple[str, List[int]]:
    match = _PATH_RE.match(part.strip())
    if not match:
        raise KeyError("cannot parse {0}".format(path))
    subs = [int(s) for s in re.findall(r"-?\d+", match.group("subs"))]
    return match.group("name").upper(), subs


# -- the runtime --------------------------------------------------------------


class CompiledRuntime:
    """A RuSTy-compiled PLC program with the interpreter's harness interface."""

    def __init__(
        self,
        plc_dir: str,
        program_order: Sequence[str],
        directory: Optional[str] = None,
    ):
        directory = directory or build_dir()
        if not available(directory):
            raise CompiledBuildMissing(
                "no usable RuSTy build in {0}; run scripts/rusty/compile_plc.sh on Linux".format(
                    directory
                )
            )
        # Names and types of the declarations. Only the declarations: nothing
        # of the interpreter's execution is used.
        self.declarations: Runtime = load(plc_dir, program_order)
        ir_structs, ir_globals = read_ir(os.path.join(directory, IR_FILE))
        hwmap = read_hwmap(os.path.join(directory, HWMAP_FILE))
        self.layout = _Layout(ir_structs)

        # Each runtime gets private copies of the program and of the standard
        # library, so every instance has its own globals and its own clock.
        # The dynamic loader would otherwise hand back the already-loaded image.
        self._workdir = tempfile.mkdtemp(prefix="cip-rusty-")
        for name in (LIBRARY, STDLIB):
            shutil.copyfile(os.path.join(directory, name), os.path.join(self._workdir, name))
        self.lib = ctypes.CDLL(os.path.join(self._workdir, LIBRARY), mode=os.RTLD_LOCAL)
        try:
            self._advance = getattr(self.lib, "__mock_time_advance_ns")
        except AttributeError as exc:
            raise CompiledBuildMissing(
                "{0} was not built with the mock_time feature; timers would read the "
                "wall clock".format(STDLIB)
            ) from exc
        self._advance.argtypes = [ctypes.c_uint64]
        self._advance.restype = None

        symbols = {name.upper(): name for name in ir_globals}
        self.located = dict(self.declarations.located)
        self._globals: Dict[str, _Ref] = {}
        for key, slot in self._declared_globals():
            type_obj = slot.type
            if key in self.located:
                address, symbol = hwmap.get(key, (None, None))
                declared = str(self.located[key].address)
                if address != declared:
                    raise LayoutMismatch(
                        "{0} is at {1} in the source but {2} in the compiled map".format(
                            key, declared, address
                        )
                    )
            else:
                symbol = symbols.get(key)
                if symbol is None:
                    raise LayoutMismatch("the compiler emitted no global {0}".format(key))
            expected = self.layout.llvm(type_obj)
            if ir_globals.get(symbol) != expected:
                raise LayoutMismatch(
                    "{0}: declared {1} but compiled as {2}".format(
                        key, expected, ir_globals.get(symbol)
                    )
                )
            cell = self.layout.ctype(type_obj).in_dll(self.lib, symbol)
            self._globals[key] = _Ref(cell, None, type_obj)
        missing = set(hwmap) - set(self.located)
        if missing:
            raise LayoutMismatch("located in the build but not the source: " + ", ".join(missing))
        # RuSTy backs %I and %Q with one process image keyed by the address
        # numbers alone, so %IX0.0 and %QX0.0 would be one byte. GVL_Io.st keeps
        # the numbers apart; this is the check that it still does.
        by_symbol: Dict[str, List[str]] = {}
        for name, (_address, symbol) in hwmap.items():
            by_symbol.setdefault(symbol, []).append(name)
        shared = {s: sorted(n) for s, n in by_symbol.items() if len(n) > 1}
        if shared:
            raise LayoutMismatch(
                "located variables share storage in the compiled program: {0}".format(shared)
            )

        self.programs: List[Tuple[str, Any, int]] = []
        for name in program_order:
            symbol = symbols.get((name + "_instance").upper())
            if symbol is None:
                raise LayoutMismatch("no instance of {0} in the build".format(name))
            function = getattr(self.lib, name)
            function.argtypes = [ctypes.c_void_p]
            function.restype = None
            address = ctypes.addressof(ctypes.c_uint8.in_dll(self.lib, symbol))
            self.programs.append((name.upper(), function, address))

        self.globals = self._globals
        self.now = 0.0
        self.scan_count = 0
        self.scan_period = 0.0

    def _declared_globals(self) -> List[Tuple[str, Any]]:
        # VAR_GLOBAL and VAR_GLOBAL CONSTANT both land in `globals`; function
        # block instances have no `type` attribute and are never global here.
        return [
            (key, slot)
            for key, slot in self.declarations.globals.items()
            if hasattr(slot, "type") and hasattr(slot, "get")
        ]

    # -- the harness interface ---------------------------------------------

    def scan(self, dt: float) -> None:
        """Advance the library clock by dt and run the task once."""
        self._advance(int(round(dt * NS_PER_S)))
        self.now += dt
        self.scan_period = dt
        self.scan_count += 1
        for _name, function, address in self.programs:
            function(address)

    def _resolve(self, path: str) -> _Ref:
        parts = path.split(".")
        name, subs = _split(parts[0], path)
        ref = self._globals.get(name)
        if ref is None:
            raise KeyError("unknown path {0}".format(path))
        ref = ref.index(subs, path)
        for part in parts[1:]:
            member, subs = _split(part, path)
            ref = ref.child(member, subs, path)
        return ref

    def get_path(self, path: str) -> Any:
        return self._resolve(path).get()

    def set_path(self, path: str, value: Any) -> None:
        self._resolve(path).set(value)

    def close(self) -> None:
        shutil.rmtree(self._workdir, ignore_errors=True)
