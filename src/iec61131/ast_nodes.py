"""Abstract syntax tree for the Structured Text subset.

Plain dataclasses, no behaviour. The parser builds these, the runtime walks
them. Every node carries the source line so a fault raised in the middle of a
scan can be traced back to the ST that caused it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple


@dataclass
class Node:
    line: int = 0


# --------------------------------------------------------------------------
# Type references
# --------------------------------------------------------------------------


@dataclass
class TypeRef(Node):
    """A named type: an elementary type, or a TYPE...END_TYPE declaration."""

    name: str = ""
    string_len: Optional[int] = None


@dataclass
class ArrayTypeRef(Node):
    dims: Sequence[Tuple[int, int]] = ()
    element: Any = None


@dataclass
class EnumTypeDecl(Node):
    name: str = ""
    members: Sequence[Tuple[str, int]] = ()


@dataclass
class StructTypeDecl(Node):
    name: str = ""
    fields: Sequence["VarDecl"] = ()


@dataclass
class AliasTypeDecl(Node):
    name: str = ""
    target: Any = None
    init: Any = None


# --------------------------------------------------------------------------
# Declarations
# --------------------------------------------------------------------------


@dataclass
class VarDecl(Node):
    name: str = ""
    type_ref: Any = None
    init: Any = None
    address: Optional[str] = None
    source_name: str = ""


@dataclass
class VarBlock(Node):
    kind: str = "VAR"  # VAR VAR_INPUT VAR_OUTPUT VAR_IN_OUT VAR_GLOBAL VAR_TEMP
    constant: bool = False
    decls: List[VarDecl] = field(default_factory=list)


@dataclass
class POU(Node):
    """PROGRAM, FUNCTION_BLOCK or FUNCTION."""

    kind: str = "PROGRAM"
    name: str = ""
    source_name: str = ""
    return_type: Any = None
    var_blocks: List[VarBlock] = field(default_factory=list)
    body: List[Any] = field(default_factory=list)


@dataclass
class Unit(Node):
    """Everything found in one .st file."""

    filename: str = ""
    types: List[Any] = field(default_factory=list)
    globals: List[VarBlock] = field(default_factory=list)
    pous: List[POU] = field(default_factory=list)


# --------------------------------------------------------------------------
# Expressions
# --------------------------------------------------------------------------


@dataclass
class Literal(Node):
    value: Any = None
    kind: str = ""  # BOOL NUMBER REAL TIME STRING


@dataclass
class VarRef(Node):
    name: str = ""
    source_name: str = ""


@dataclass
class Member(Node):
    base: Any = None
    name: str = ""


@dataclass
class Index(Node):
    base: Any = None
    subscripts: List[Any] = field(default_factory=list)


@dataclass
class BinOp(Node):
    op: str = ""
    left: Any = None
    right: Any = None


@dataclass
class UnaryOp(Node):
    op: str = ""
    operand: Any = None


@dataclass
class Arg(Node):
    name: Optional[str] = None
    value: Any = None
    is_output: bool = False  # spelled `=>` rather than `:=`


@dataclass
class Call(Node):
    target: Any = None  # VarRef for a function, or Member/VarRef for an FB instance
    args: List[Arg] = field(default_factory=list)


# --------------------------------------------------------------------------
# Statements
# --------------------------------------------------------------------------


@dataclass
class Assign(Node):
    target: Any = None
    value: Any = None


@dataclass
class CallStmt(Node):
    call: Any = None


@dataclass
class If(Node):
    branches: List[Tuple[Any, List[Any]]] = field(default_factory=list)
    orelse: List[Any] = field(default_factory=list)


@dataclass
class Case(Node):
    selector: Any = None
    branches: List[Tuple[List[Any], List[Any]]] = field(default_factory=list)
    orelse: List[Any] = field(default_factory=list)


@dataclass
class For(Node):
    var: Any = None
    start: Any = None
    stop: Any = None
    step: Any = None
    body: List[Any] = field(default_factory=list)


@dataclass
class While(Node):
    cond: Any = None
    body: List[Any] = field(default_factory=list)


@dataclass
class Repeat(Node):
    body: List[Any] = field(default_factory=list)
    until: Any = None


@dataclass
class Exit(Node):
    pass


@dataclass
class Continue(Node):
    pass


@dataclass
class Return(Node):
    pass
