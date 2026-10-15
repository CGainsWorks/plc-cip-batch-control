"""Loader and scan-cycle interpreter for the Structured Text subset.

`Runtime.load_directory` parses every .st file, resolves the type and POU
tables, instantiates the PROGRAM bodies and then `scan(dt)` executes them once
per cycle in the configured task order. Time is simulated: `now` only advances
because a scan says so, which is what makes a run reproducible.

Three details are there for speed rather than clarity, because a 45 minute
cycle at a 200 ms task is fourteen thousand scans and the experiment sweep runs
it a dozen times:

  * every POU instance gets one flattened scope dictionary holding its own
    variables plus the globals and enumeration constants it can see, so a name
    resolves in a single dict lookup;
  * structure assignment copies field by field into the existing StructValue
    rather than replacing it, which keeps object identity stable;
  * element references are small __slots__ objects instead of closures.

Compiling the tree to Python closures once per instance would be faster again
and is the obvious next step but a tree walk is far easier to trust.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import ast_nodes as A
from .address import Address, parse_address
from .errors import STDeclarationError, STRuntimeError
from .parser import parse
from .stdlib import NATIVE_BLOCKS, STANDARD_FUNCTIONS, NativeFB
from .types import (
    ELEMENTARY,
    ArrayType,
    ElementaryType,
    EnumType,
    FunctionBlockType,
    NativeBlockType,
    StructType,
    StructValue,
    Type,
    coerce,
    make_converter,
)

_PATH_PART_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)((?:\s*\[\s*-?\d+\s*\])*)$")


class _Exit(Exception):
    pass


class _Continue(Exception):
    pass


class _Return(Exception):
    pass


class Var:
    """A storage slot with a declared type."""

    __slots__ = ("name", "type", "value", "constant", "address", "convert")

    def __init__(self, name: str, type_obj: Type, value: Any, address: Optional[Address] = None):
        self.name = name
        self.type = type_obj
        self.value = value
        self.constant = False
        self.address = address
        self.convert = make_converter(type_obj, name)

    def get(self) -> Any:
        return self.value

    def set(self, value: Any) -> None:
        if self.constant:
            raise STRuntimeError("cannot assign to constant {0}".format(self.name))
        self.value = self.convert(self.value, value)


class StructRef:
    """A reference to one field of a structure instance."""

    __slots__ = ("container", "key", "type", "name", "convert")

    def __init__(self, container: StructValue, key: str, type_obj: Type):
        self.container = container
        self.key = key
        self.type = type_obj
        self.name = key
        self.convert = make_converter(type_obj, key)

    def get(self) -> Any:
        return self.container[self.key]

    def set(self, value: Any) -> None:
        self.container[self.key] = self.convert(self.container[self.key], value)


class ElementRef:
    """A reference to one element of an array."""

    __slots__ = ("container", "index", "type", "name", "convert")

    def __init__(self, container: List[Any], index: int, type_obj: Type):
        self.container = container
        self.index = index
        self.type = type_obj
        self.name = "array element"
        self.convert = make_converter(type_obj, "array element")

    def get(self) -> Any:
        return self.container[self.index]

    def set(self, value: Any) -> None:
        self.container[self.index] = self.convert(self.container[self.index], value)


class NativeRef:
    """A reference to an input or output of a standard function block."""

    __slots__ = ("block", "member", "type", "name")

    def __init__(self, block: NativeFB, member: str):
        self.block = block
        self.member = member
        self.type = block.member_type(member)
        self.name = "{0}.{1}".format(block.NAME, member)

    def get(self) -> Any:
        return self.block.get(self.member)

    def set(self, value: Any) -> None:
        self.block.set(self.member, value)


class _Unbound:
    __slots__ = ("name", "type")

    def __init__(self, name: str):
        self.name = name
        self.type = None

    def get(self) -> Any:
        raise STRuntimeError("VAR_IN_OUT {0} was never bound".format(self.name))

    def set(self, value: Any) -> None:
        raise STRuntimeError("VAR_IN_OUT {0} was never bound".format(self.name))


class Instance:
    """An instance of a user FUNCTION_BLOCK or a PROGRAM."""

    __slots__ = ("name", "pou", "vars", "type", "scope", "frame", "positional", "in_out")

    def __init__(self, name: str, pou: A.POU, type_obj: Optional[Type] = None):
        self.name = name
        self.pou = pou
        self.type = type_obj
        self.vars: Dict[str, Any] = {}
        self.scope: Dict[str, Any] = {}
        self.frame: Optional["Frame"] = None
        self.positional: Tuple[str, ...] = ()
        self.in_out: frozenset = frozenset()

    def member_ref(self, member: str) -> Any:
        slot = self.vars.get(member)
        if slot is None:
            raise STRuntimeError("{0} has no member {1}".format(self.pou.name, member))
        return slot

    def bind(self, name: str, slot: Any) -> None:
        self.vars[name] = slot
        self.scope[name] = slot


class Runtime:
    def __init__(self) -> None:
        self.types: Dict[str, Type] = {}
        self.functions: Dict[str, A.POU] = {}
        self.fb_pous: Dict[str, A.POU] = {}
        self.globals: Dict[str, Any] = {}
        self.constants: Dict[str, Var] = {}
        self.program_pous: Dict[str, A.POU] = {}
        self.programs: List[Tuple[str, Instance]] = []
        self.located: Dict[str, Var] = {}
        self.now = 0.0
        self.scan_count = 0
        self.scan_period = 0.0
        self._units: List[A.Unit] = []
        self._eval_dispatch = {
            A.Literal: self._eval_literal,
            A.VarRef: self._eval_varref,
            A.Member: self._eval_slot_value,
            A.Index: self._eval_slot_value,
            A.BinOp: self._eval_binop,
            A.UnaryOp: self._eval_unary,
            A.Call: self._eval_call,
        }
        self._exec_dispatch = {
            A.Assign: self._exec_assign,
            A.CallStmt: self._exec_call,
            A.If: self._exec_if,
            A.Case: self._exec_case,
            A.For: self._exec_for,
            A.While: self._exec_while,
            A.Repeat: self._exec_repeat,
            A.Exit: self._exec_exit,
            A.Continue: self._exec_continue,
            A.Return: self._exec_return,
        }

    # -- loading -----------------------------------------------------------

    def load_source(self, source: str, filename: str = "<memory>") -> None:
        self._units.append(parse(source, filename))

    def load_files(self, paths: Iterable[str]) -> None:
        for path in paths:
            with open(path, "r", encoding="utf-8") as handle:
                self.load_source(handle.read(), os.path.basename(path))

    def load_directory(self, root: str) -> None:
        paths = []
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in sorted(filenames):
                if filename.lower().endswith(".st"):
                    paths.append(os.path.join(dirpath, filename))
        if not paths:
            raise STDeclarationError("no .st files found under {0}".format(root))
        # Sorted, so a load is reproducible regardless of walk order.
        self.load_files(sorted(paths))

    def build(self, program_order: Optional[Sequence[str]] = None) -> None:
        """Resolve declarations and instantiate the programs."""
        self._register_types()
        self._resolve_types()
        self._register_pous()
        self._build_globals()
        self._instantiate_programs(program_order)

    def _register_types(self) -> None:
        for unit in self._units:
            for decl in unit.types:
                name = decl.name
                if name in self.types:
                    raise STDeclarationError("duplicate type {0}".format(name), decl.line)
                if isinstance(decl, A.EnumTypeDecl):
                    enum = EnumType(name, decl.members)
                    self.types[name] = enum
                    for member, value in decl.members:
                        if member in self.constants:
                            raise STDeclarationError(
                                "enumeration member {0} is declared twice".format(member), decl.line
                            )
                        slot = Var(member, enum, value)
                        slot.constant = True
                        self.constants[member] = slot
                elif isinstance(decl, A.StructTypeDecl):
                    self.types[name] = StructType(name)
                elif isinstance(decl, A.AliasTypeDecl):
                    self.types[name] = decl  # replaced during resolution
                else:
                    raise STDeclarationError("unsupported type declaration", decl.line)

    def _resolve_types(self) -> None:
        # Aliases first, so a struct field may name one.
        for name, entry in list(self.types.items()):
            if isinstance(entry, A.AliasTypeDecl):
                self.types[name] = self.resolve_type(entry.target)
        for unit in self._units:
            for decl in unit.types:
                if isinstance(decl, A.StructTypeDecl):
                    struct = self.types[decl.name]
                    if not isinstance(struct, StructType):
                        raise STDeclarationError("{0} is not a structure".format(decl.name))
                    for field in decl.fields:
                        struct.add_field(field.name, self.resolve_type(field.type_ref), field.init)

    def resolve_type(self, ref: Any) -> Type:
        if isinstance(ref, A.ArrayTypeRef):
            return ArrayType(ref.dims, self.resolve_type(ref.element))
        if not isinstance(ref, A.TypeRef):
            raise STDeclarationError("unsupported type reference {0!r}".format(ref))
        name = ref.name
        if name in ELEMENTARY:
            return ElementaryType(name, ref.string_len)
        if name in self.types:
            entry = self.types[name]
            if isinstance(entry, A.AliasTypeDecl):
                entry = self.resolve_type(entry.target)
                self.types[name] = entry
            return entry
        if name in NATIVE_BLOCKS:
            return NativeBlockType(name, NATIVE_BLOCKS[name])
        if name in self.fb_pous:
            return FunctionBlockType(name, self.fb_pous[name])
        raise STDeclarationError("unknown type {0}".format(name), ref.line)

    def _register_pous(self) -> None:
        for unit in self._units:
            for pou in unit.pous:
                if pou.kind == "FUNCTION_BLOCK":
                    if pou.name in self.fb_pous:
                        raise STDeclarationError(
                            "duplicate FUNCTION_BLOCK {0}".format(pou.name), pou.line
                        )
                    self.fb_pous[pou.name] = pou
                    self.types[pou.name] = FunctionBlockType(pou.name, pou)
                elif pou.kind == "FUNCTION":
                    if pou.name in self.functions:
                        raise STDeclarationError("duplicate FUNCTION {0}".format(pou.name), pou.line)
                    self.functions[pou.name] = pou
                elif pou.kind == "PROGRAM":
                    if pou.name in self.program_pous:
                        raise STDeclarationError("duplicate PROGRAM {0}".format(pou.name), pou.line)
                    self.program_pous[pou.name] = pou

    def _build_globals(self) -> None:
        for unit in self._units:
            for block in unit.globals:
                for decl in block.decls:
                    if decl.name in self.globals:
                        raise STDeclarationError("duplicate global {0}".format(decl.name), decl.line)
                    slot = self._make_slot(decl, "GVL")
                    if block.constant and isinstance(slot, Var):
                        slot.constant = True
                    self.globals[decl.name] = slot
                    if decl.address and isinstance(slot, Var):
                        slot.address = parse_address(decl.address, decl.line)
                        self.located[decl.name] = slot

    def _instantiate_programs(self, program_order: Optional[Sequence[str]]) -> None:
        names = list(program_order) if program_order else sorted(self.program_pous)
        for name in names:
            key = name.upper()
            if key not in self.program_pous:
                raise STDeclarationError("no PROGRAM named {0}".format(name))
            self.programs.append((key, self._instantiate_pou(key, self.program_pous[key])))

    # -- instantiation -----------------------------------------------------

    def _instantiate_pou(self, name: str, pou: A.POU, type_obj: Optional[Type] = None) -> Instance:
        instance = Instance(name, pou, type_obj)
        positional: List[str] = []
        in_out: List[str] = []
        for block in pou.var_blocks:
            for decl in block.decls:
                if decl.name in instance.vars:
                    raise STDeclarationError(
                        "{0} declares {1} twice".format(pou.name, decl.name), decl.line
                    )
                if block.kind in ("VAR_INPUT", "VAR_IN_OUT"):
                    positional.append(decl.name)
                if block.kind == "VAR_IN_OUT":
                    in_out.append(decl.name)
                    instance.vars[decl.name] = _Unbound(decl.name)
                    continue
                slot = self._make_slot(decl, pou.name)
                if block.constant and isinstance(slot, Var):
                    slot.constant = True
                instance.vars[decl.name] = slot
        instance.positional = tuple(positional)
        instance.in_out = frozenset(in_out)
        self._flatten_scope(instance)
        return instance

    def _flatten_scope(self, instance: Instance) -> None:
        """One dictionary holding everything a name in this POU can resolve to."""
        scope: Dict[str, Any] = {}
        scope.update(self.constants)
        scope.update(self.globals)
        scope.update(instance.vars)
        instance.scope = scope
        instance.frame = Frame(self, scope)

    def _make_slot(self, decl: A.VarDecl, owner: str) -> Any:
        type_obj = self.resolve_type(decl.type_ref)
        if isinstance(type_obj, NativeBlockType):
            if decl.init is not None:
                raise STDeclarationError("a standard block takes no initialiser", decl.line)
            return type_obj.factory()
        if isinstance(type_obj, FunctionBlockType):
            if decl.init is not None:
                raise STDeclarationError("a function block takes no initialiser", decl.line)
            return self._instantiate_pou("{0}.{1}".format(owner, decl.name), type_obj.pou, type_obj)
        value = self.default_value(type_obj)
        if decl.init is not None:
            value = coerce(type_obj, self._eval_initialiser(decl.init, type_obj), decl.name)
        return Var(decl.name, type_obj, value)

    def default_value(self, type_obj: Type) -> Any:
        if isinstance(type_obj, StructType):
            value = StructValue(type_obj)
            for field in type_obj.field_order:
                field_type = type_obj.fields[field]
                init = type_obj.field_inits.get(field)
                if init is not None:
                    value[field] = coerce(
                        field_type, self._eval_initialiser(init, field_type), field
                    )
                else:
                    value[field] = self.default_value(field_type)
            return value
        if isinstance(type_obj, ArrayType):
            return self._default_array(type_obj, 0)
        return type_obj.default()

    def _default_array(self, type_obj: ArrayType, depth: int) -> Any:
        low, high = type_obj.dims[depth]
        count = high - low + 1
        if depth == len(type_obj.dims) - 1:
            return [self.default_value(type_obj.element) for _ in range(count)]
        return [self._default_array(type_obj, depth + 1) for _ in range(count)]

    def _eval_initialiser(self, init: Any, type_obj: Type) -> Any:
        if isinstance(init, list):
            if not isinstance(type_obj, ArrayType):
                raise STDeclarationError("a list initialiser needs an array type")
            element = (
                type_obj.element
                if len(type_obj.dims) == 1
                else ArrayType(type_obj.dims[1:], type_obj.element)
            )
            return [coerce(element, self._eval_initialiser(item, element), "") for item in init]
        return self.eval(init, Frame(self, self.constants))

    # -- scanning ----------------------------------------------------------

    def scan(self, dt: float) -> None:
        """Advance the simulated clock by dt and run one full scan."""
        self.now += dt
        self.scan_period = dt
        self.scan_count += 1
        exec_block = self.exec_block
        for _name, program in self.programs:
            try:
                exec_block(program.pou.body, program.frame)
            except _Return:
                pass

    # -- name lookup -------------------------------------------------------

    def lookup(self, name: str, frame: "Frame") -> Any:
        slot = frame.vars.get(name)
        if slot is None:
            raise STRuntimeError("unknown identifier {0}".format(name))
        return slot

    def _resolve_slot(self, path: str) -> Any:
        """Resolve a dotted path such as `gKpi.rEnergyKWh` or `gResults[2].xPass`."""
        parts = path.upper().split(".")
        slot: Any = None
        for index, part in enumerate(parts):
            name, subscripts = _split_subscripts(part, path)
            if index == 0:
                if name in self.globals:
                    slot = self.globals[name]
                elif name in self.constants:
                    slot = self.constants[name]
                else:
                    matches = [inst for key, inst in self.programs if key == name]
                    if not matches:
                        raise STRuntimeError("unknown path {0}".format(path))
                    slot = matches[0]
            elif isinstance(slot, Instance):
                slot = slot.member_ref(name)
            elif isinstance(slot, NativeFB):
                slot = NativeRef(slot, name)
            else:
                container = slot.get()
                if not isinstance(container, StructValue):
                    raise STRuntimeError("{0} is not a structure".format(path))
                slot = _struct_ref(container, name)
            for subscript in subscripts:
                slot = self._index_slot(slot, subscript, path)
        return slot

    def _index_slot(self, slot: Any, subscript: int, path: str) -> Any:
        if isinstance(slot, (Instance, NativeFB)):
            raise STRuntimeError("a block cannot be indexed in {0}".format(path))
        array_type = slot.type
        if not isinstance(array_type, ArrayType):
            raise STRuntimeError("{0} is not an array".format(path))
        low, high = array_type.dims[0]
        if subscript < low or subscript > high:
            raise STRuntimeError(
                "index {0} outside {1}..{2} in {3}".format(subscript, low, high, path)
            )
        remaining = array_type.dims[1:]
        element = array_type.element if not remaining else ArrayType(remaining, array_type.element)
        return ElementRef(slot.get(), subscript - low, element)

    def get_path(self, path: str) -> Any:
        slot = self._resolve_slot(path)
        if isinstance(slot, (Instance, NativeFB)):
            raise STRuntimeError("{0} names a block, not a value".format(path))
        return slot.get()

    def set_path(self, path: str, value: Any) -> None:
        self._resolve_slot(path).set(value)

    # -- statement execution ----------------------------------------------

    def exec_block(self, statements: Sequence[Any], frame: "Frame") -> None:
        dispatch = self._exec_dispatch
        for statement in statements:
            handler = dispatch.get(statement.__class__)
            if handler is None:
                raise STRuntimeError(
                    "cannot execute {0}".format(type(statement).__name__), statement.line
                )
            handler(statement, frame)

    def _exec_assign(self, node: A.Assign, frame: "Frame") -> None:
        value = self.eval(node.value, frame)
        target = node.target
        if target.__class__ is A.VarRef:
            slot = frame.vars.get(target.name)
            if slot is None:
                raise STRuntimeError("unknown identifier {0}".format(target.name), node.line)
        else:
            slot = self.slot(target, frame)
        try:
            slot.set(value)
        except STRuntimeError as exc:
            raise STRuntimeError(exc.message, node.line, exc.file) from None
        except AttributeError as exc:
            raise STRuntimeError("cannot assign to a block", node.line) from exc

    def _exec_call(self, node: A.CallStmt, frame: "Frame") -> None:
        self._eval_call(node.call, frame)

    def _exec_if(self, node: A.If, frame: "Frame") -> None:
        for cond, body in node.branches:
            if self.eval(cond, frame):
                self.exec_block(body, frame)
                return
        if node.orelse:
            self.exec_block(node.orelse, frame)

    def _exec_case(self, node: A.Case, frame: "Frame") -> None:
        selector = self.eval(node.selector, frame)
        for labels, body in node.branches:
            for label in labels:
                if label.__class__ is A.BinOp and label.op == "..":
                    low = self.eval(label.left, frame)
                    high = self.eval(label.right, frame)
                    if low <= selector <= high:
                        self.exec_block(body, frame)
                        return
                elif self.eval(label, frame) == selector:
                    self.exec_block(body, frame)
                    return
        if node.orelse:
            self.exec_block(node.orelse, frame)

    def _exec_for(self, node: A.For, frame: "Frame") -> None:
        slot = self.slot(node.var, frame)
        index = self.eval(node.start, frame)
        stop = self.eval(node.stop, frame)
        step = self.eval(node.step, frame) if node.step is not None else 1
        if step == 0:
            raise STRuntimeError("FOR step is zero", node.line)
        while (step > 0 and index <= stop) or (step < 0 and index >= stop):
            slot.set(index)
            try:
                self.exec_block(node.body, frame)
            except _Continue:
                pass
            except _Exit:
                return
            index += step

    def _exec_while(self, node: A.While, frame: "Frame") -> None:
        guard = 0
        while self.eval(node.cond, frame):
            guard += 1
            if guard > 1000000:
                raise STRuntimeError("WHILE loop did not terminate", node.line)
            try:
                self.exec_block(node.body, frame)
            except _Continue:
                continue
            except _Exit:
                return

    def _exec_repeat(self, node: A.Repeat, frame: "Frame") -> None:
        guard = 0
        while True:
            guard += 1
            if guard > 1000000:
                raise STRuntimeError("REPEAT loop did not terminate", node.line)
            try:
                self.exec_block(node.body, frame)
            except _Continue:
                pass
            except _Exit:
                return
            if self.eval(node.until, frame):
                return

    def _exec_exit(self, node: A.Exit, frame: "Frame") -> None:
        raise _Exit()

    def _exec_continue(self, node: A.Continue, frame: "Frame") -> None:
        raise _Continue()

    def _exec_return(self, node: A.Return, frame: "Frame") -> None:
        raise _Return()

    # -- expression evaluation --------------------------------------------

    def eval(self, node: Any, frame: "Frame") -> Any:
        handler = self._eval_dispatch.get(node.__class__)
        if handler is None:
            raise STRuntimeError(
                "cannot evaluate {0}".format(type(node).__name__), getattr(node, "line", 0)
            )
        return handler(node, frame)

    def _eval_literal(self, node: A.Literal, frame: "Frame") -> Any:
        return node.value

    def _eval_varref(self, node: A.VarRef, frame: "Frame") -> Any:
        slot = frame.vars.get(node.name)
        if slot is None:
            raise STRuntimeError("unknown identifier {0}".format(node.source_name), node.line)
        try:
            return slot.get()
        except (TypeError, AttributeError) as exc:
            raise STRuntimeError(
                "{0} is a block, not a value".format(node.source_name), node.line
            ) from exc

    def _eval_slot_value(self, node: Any, frame: "Frame") -> Any:
        slot = self.slot(node, frame)
        if isinstance(slot, (Instance, NativeFB)):
            raise STRuntimeError("expected a value, found a block", node.line)
        return slot.get()

    def _eval_unary(self, node: A.UnaryOp, frame: "Frame") -> Any:
        value = self.eval(node.operand, frame)
        if node.op == "-":
            return -value
        if isinstance(value, bool):
            return not value
        return ~int(value)

    def _eval_binop(self, node: A.BinOp, frame: "Frame") -> Any:
        left = self.eval(node.left, frame)
        right = self.eval(node.right, frame)
        operator = _BINARY_OPS.get(node.op)
        if operator is None:
            raise STRuntimeError("unknown operator {0}".format(node.op), node.line)
        try:
            return operator(left, right)
        except STRuntimeError as exc:
            raise STRuntimeError(exc.message, node.line, exc.file) from None
        except TypeError as exc:
            raise STRuntimeError(str(exc), node.line) from exc

    # -- calls -------------------------------------------------------------

    def _eval_call(self, node: A.Call, frame: "Frame") -> Any:
        target = node.target
        if target.__class__ is A.VarRef:
            slot = frame.vars.get(target.name)
            if slot is None:
                name = target.name
                if name in self.functions:
                    return self._call_function(self.functions[name], node, frame)
                if name in STANDARD_FUNCTIONS:
                    return self._call_standard(name, node, frame)
                raise STRuntimeError("unknown identifier {0}".format(target.source_name), node.line)
        else:
            slot = self.slot(target, frame)
        if isinstance(slot, Instance):
            return self._call_fb(slot, node, frame)
        if isinstance(slot, NativeFB):
            return self._call_native(slot, node, frame)
        raise STRuntimeError("{0} is not callable".format(getattr(target, "name", target)), node.line)

    def _call_standard(self, name: str, node: A.Call, frame: "Frame") -> Any:
        args = []
        for arg in node.args:
            if arg.name is not None:
                raise STRuntimeError("{0} takes positional arguments".format(name), node.line)
            args.append(self.eval(arg.value, frame))
        try:
            return STANDARD_FUNCTIONS[name](*args)
        except STRuntimeError as exc:
            raise STRuntimeError(exc.message, node.line, exc.file) from None
        except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
            raise STRuntimeError("{0}: {1}".format(name, exc), node.line) from exc

    def _call_function(self, pou: A.POU, node: A.Call, frame: "Frame") -> Any:
        instance = self._instantiate_pou(pou.name, pou)
        return_type = self.resolve_type(pou.return_type)
        instance.bind(pou.name, Var(pou.name, return_type, self.default_value(return_type)))
        callee = instance.frame
        self._bind_arguments(instance, node, frame)
        try:
            self.exec_block(pou.body, callee)
        except _Return:
            pass
        self._write_back_outputs(instance, node, frame)
        return instance.vars[pou.name].get()

    def _call_fb(self, instance: Instance, node: A.Call, frame: "Frame") -> Any:
        self._bind_arguments(instance, node, frame)
        try:
            self.exec_block(instance.pou.body, instance.frame)
        except _Return:
            pass
        self._write_back_outputs(instance, node, frame)
        return None

    def _call_native(self, block: NativeFB, node: A.Call, frame: "Frame") -> Any:
        positional = block.positional_inputs()
        index = 0
        deferred: List[Tuple[str, Any]] = []
        for arg in node.args:
            if arg.is_output:
                deferred.append((arg.name or "", arg.value))
                continue
            if arg.name is None:
                if index >= len(positional):
                    raise STRuntimeError("too many arguments for {0}".format(block.NAME), node.line)
                name = positional[index]
                index += 1
            else:
                name = arg.name
            block.set(name, self.eval(arg.value, frame))
        block.execute(self)
        for name, target in deferred:
            self.slot(target, frame).set(block.get(name))
        return None

    def _bind_arguments(self, instance: Instance, node: A.Call, caller: "Frame") -> None:
        positional = instance.positional
        index = 0
        in_out = instance.in_out
        for arg in node.args:
            if arg.is_output:
                continue
            name = arg.name
            if name is None:
                if index >= len(positional):
                    raise STRuntimeError(
                        "too many arguments for {0}".format(instance.pou.name), node.line
                    )
                name = positional[index]
                index += 1
            if name in in_out:
                instance.bind(name, self.slot(arg.value, caller))
                continue
            slot = instance.vars.get(name)
            if slot is None:
                raise STRuntimeError(
                    "{0} has no input {1}".format(instance.pou.name, name), node.line
                )
            slot.set(self.eval(arg.value, caller))

    def _write_back_outputs(self, instance: Instance, node: A.Call, caller: "Frame") -> None:
        for arg in node.args:
            if not arg.is_output:
                continue
            source = instance.vars.get(arg.name or "")
            if source is None:
                raise STRuntimeError(
                    "{0} has no output {1}".format(instance.pou.name, arg.name), node.line
                )
            self.slot(arg.value, caller).set(source.get())

    # -- lvalue resolution -------------------------------------------------

    def slot(self, node: Any, frame: "Frame") -> Any:
        kind = node.__class__
        if kind is A.VarRef:
            slot = frame.vars.get(node.name)
            if slot is None:
                raise STRuntimeError("unknown identifier {0}".format(node.source_name), node.line)
            return slot
        if kind is A.Member:
            base = self.slot(node.base, frame)
            if isinstance(base, Instance):
                return base.member_ref(node.name)
            if isinstance(base, NativeFB):
                return NativeRef(base, node.name)
            container = base.get()
            if isinstance(container, StructValue):
                return _struct_ref(container, node.name)
            raise STRuntimeError("{0} has no member {1}".format(base.name, node.name), node.line)
        if kind is A.Index:
            base = self.slot(node.base, frame)
            if isinstance(base, (Instance, NativeFB)):
                raise STRuntimeError("a block cannot be indexed", node.line)
            array_type = base.type
            if not isinstance(array_type, ArrayType):
                raise STRuntimeError("{0} is not an array".format(base.name), node.line)
            container = base.get()
            subscripts = [int(self.eval(sub, frame)) for sub in node.subscripts]
            if len(subscripts) > len(array_type.dims):
                raise STRuntimeError("too many subscripts", node.line)
            for depth, subscript in enumerate(subscripts):
                low, high = array_type.dims[depth]
                if subscript < low or subscript > high:
                    raise STRuntimeError(
                        "index {0} outside {1}..{2}".format(subscript, low, high), node.line
                    )
                offset = subscript - low
                if depth == len(subscripts) - 1:
                    remaining = array_type.dims[depth + 1 :]
                    element = (
                        array_type.element
                        if not remaining
                        else ArrayType(remaining, array_type.element)
                    )
                    return ElementRef(container, offset, element)
                container = container[offset]
        raise STRuntimeError(
            "{0} is not an assignable location".format(type(node).__name__),
            getattr(node, "line", 0),
        )


class Frame:
    __slots__ = ("runtime", "vars")

    def __init__(self, runtime: Runtime, variables: Dict[str, Any]):
        self.runtime = runtime
        self.vars = variables


def _struct_ref(container: StructValue, field: str) -> StructRef:
    field_type = container.type.fields.get(field)
    if field_type is None:
        raise STRuntimeError("{0} has no field {1}".format(container.type.name, field))
    return StructRef(container, field, field_type)


def _split_subscripts(part: str, path: str) -> Tuple[str, List[int]]:
    match = _PATH_PART_RE.match(part.strip())
    if not match:
        raise STRuntimeError("cannot parse {0!r} in path {1}".format(part, path))
    name = match.group(1)
    subscripts = [int(token) for token in re.findall(r"-?\d+", match.group(2) or "")]
    return name, subscripts


def _divide(left: Any, right: Any) -> Any:
    if right == 0:
        raise STRuntimeError("division by zero")
    if isinstance(left, int) and isinstance(right, int) and not isinstance(left, bool):
        quotient = abs(left) // abs(right)
        return quotient if (left >= 0) == (right >= 0) else -quotient
    return left / right


def _modulo(left: Any, right: Any) -> Any:
    if right == 0:
        raise STRuntimeError("division by zero")
    # IEC 61131-3 MOD takes the sign of the dividend.
    remainder = abs(left) % abs(right)
    return remainder if left >= 0 else -remainder


def _logical_and(left: Any, right: Any) -> Any:
    if left is True or left is False:
        if right is True or right is False:
            return left and right
    return int(left) & int(right)


def _logical_or(left: Any, right: Any) -> Any:
    if left is True or left is False:
        if right is True or right is False:
            return left or right
    return int(left) | int(right)


def _logical_xor(left: Any, right: Any) -> Any:
    if left is True or left is False:
        if right is True or right is False:
            return left != right
    return int(left) ^ int(right)


_BINARY_OPS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": _divide,
    "MOD": _modulo,
    "**": lambda a, b: a**b,
    "=": lambda a, b: a == b,
    "<>": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "AND": _logical_and,
    "OR": _logical_or,
    "XOR": _logical_xor,
}


def load(root: str, program_order: Optional[Sequence[str]] = None) -> Runtime:
    runtime = Runtime()
    runtime.load_directory(root)
    runtime.build(program_order)
    return runtime
