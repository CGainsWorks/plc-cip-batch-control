"""Recursive-descent parser for the Structured Text subset.

Handles the declaration part (TYPE, VAR_GLOBAL and the VAR blocks of a POU) and
the statement part (assignment, FB invocation, IF, CASE, FOR, WHILE, REPEAT,
EXIT, CONTINUE, RETURN). Operator precedence follows IEC 61131-3 table 55.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from . import ast_nodes as A
from .errors import STSyntaxError
from .lexer import Token, tokenize

VAR_BLOCK_KINDS = (
    "VAR",
    "VAR_INPUT",
    "VAR_OUTPUT",
    "VAR_IN_OUT",
    "VAR_GLOBAL",
    "VAR_TEMP",
)

# Lowest precedence first. Each entry is (matcher-kind, operators).
_BINARY_LEVELS: List[Tuple[str, Tuple[str, ...]]] = [
    ("KEYWORD", ("OR",)),
    ("KEYWORD", ("XOR",)),
    ("KEYWORD", ("AND",)),
    ("SYMBOL", ("=", "<>")),
    ("SYMBOL", ("<", ">", "<=", ">=")),
    ("SYMBOL", ("+", "-")),
    ("MUL", ("*", "/", "MOD")),
]


class Parser:
    def __init__(self, tokens: List[Token], filename: Optional[str] = None):
        self.toks = tokens
        self.i = 0
        self.filename = filename

    # -- token helpers -----------------------------------------------------

    @property
    def cur(self) -> Token:
        return self.toks[self.i]

    def peek(self, ahead: int = 1) -> Token:
        j = min(self.i + ahead, len(self.toks) - 1)
        return self.toks[j]

    def _fail(self, message: str, tok: Optional[Token] = None) -> STSyntaxError:
        tok = tok or self.cur
        got = "end of file" if tok.kind == "EOF" else repr(tok.text)
        return STSyntaxError("{0} (got {1})".format(message, got), tok.line, self.filename)

    def at(self, kind: str, value: Any = None) -> bool:
        tok = self.cur
        return tok.kind == kind and (value is None or tok.value == value)

    def at_kw(self, *words: str) -> bool:
        return self.cur.kind == "KEYWORD" and self.cur.value in words

    def at_sym(self, *syms: str) -> bool:
        return self.cur.kind == "SYMBOL" and self.cur.value in syms

    def take(self) -> Token:
        tok = self.cur
        self.i += 1
        return tok

    def accept_sym(self, sym: str) -> bool:
        if self.at_sym(sym):
            self.i += 1
            return True
        return False

    def accept_kw(self, word: str) -> bool:
        if self.at_kw(word):
            self.i += 1
            return True
        return False

    def expect_sym(self, sym: str) -> Token:
        if not self.at_sym(sym):
            raise self._fail("expected {0!r}".format(sym))
        return self.take()

    def expect_kw(self, word: str) -> Token:
        if not self.at_kw(word):
            raise self._fail("expected {0}".format(word))
        return self.take()

    def expect_ident(self) -> Token:
        if self.cur.kind != "IDENT":
            raise self._fail("expected an identifier")
        return self.take()

    # -- top level ---------------------------------------------------------

    def parse_unit(self) -> A.Unit:
        unit = A.Unit(line=1, filename=self.filename or "<memory>")
        while not self.at("EOF"):
            if self.at_kw("TYPE"):
                unit.types.extend(self.parse_type_block())
            elif self.at_kw("VAR_GLOBAL"):
                unit.globals.append(self.parse_var_block())
            elif self.at_kw("PROGRAM", "FUNCTION_BLOCK", "FUNCTION"):
                unit.pous.append(self.parse_pou())
            else:
                raise self._fail("expected TYPE, VAR_GLOBAL, PROGRAM, FUNCTION_BLOCK or FUNCTION")
        return unit

    def parse_type_block(self) -> List[Any]:
        self.expect_kw("TYPE")
        out: List[Any] = []
        while not self.at_kw("END_TYPE"):
            if self.at("EOF"):
                raise self._fail("unterminated TYPE block")
            out.append(self.parse_type_decl())
        self.expect_kw("END_TYPE")
        self.accept_sym(";")
        return out

    def parse_type_decl(self) -> Any:
        tok = self.expect_ident()
        name, line = str(tok.value), tok.line
        self.expect_sym(":")
        if self.at_sym("("):
            decl = self.parse_enum_body(name, line)
        elif self.at_kw("STRUCT"):
            self.take()
            fields = []
            while not self.at_kw("END_STRUCT"):
                if self.at("EOF"):
                    raise self._fail("unterminated STRUCT")
                fields.extend(self.parse_var_decl_line())
            self.expect_kw("END_STRUCT")
            decl = A.StructTypeDecl(line=line, name=name, fields=fields)
        else:
            target = self.parse_type_spec()
            init = self.parse_expr() if self.accept_sym(":=") else None
            decl = A.AliasTypeDecl(line=line, name=name, target=target, init=init)
        # Vendors differ on whether END_STRUCT and the closing ')' of an
        # enumeration take a trailing semicolon, so it is optional here.
        self.accept_sym(";")
        return decl

    def parse_enum_body(self, name: str, line: int) -> A.EnumTypeDecl:
        self.expect_sym("(")
        members: List[Tuple[str, int]] = []
        next_value = 0
        while True:
            member = self.expect_ident()
            if self.accept_sym(":="):
                value_tok = self.take()
                if value_tok.kind != "NUMBER":
                    raise self._fail("enumeration values must be integer literals", value_tok)
                next_value = int(value_tok.value)
            members.append((str(member.value), next_value))
            next_value += 1
            if not self.accept_sym(","):
                break
        self.expect_sym(")")
        if self.accept_sym(":="):
            self.parse_expr()  # default value; the runtime uses member 0
        return A.EnumTypeDecl(line=line, name=name, members=tuple(members))

    # -- declarations ------------------------------------------------------

    def parse_var_block(self) -> A.VarBlock:
        if self.cur.kind != "KEYWORD" or self.cur.value not in VAR_BLOCK_KINDS:
            raise self._fail("expected a VAR block")
        tok = self.take()
        block = A.VarBlock(line=tok.line, kind=str(tok.value))
        while self.at_kw("CONSTANT", "RETAIN", "NON_RETAIN"):
            if self.cur.value == "CONSTANT":
                block.constant = True
            self.take()
        while not self.at_kw("END_VAR"):
            if self.at("EOF"):
                raise self._fail("unterminated {0} block".format(block.kind))
            block.decls.extend(self.parse_var_decl_line())
        self.expect_kw("END_VAR")
        self.accept_sym(";")
        return block

    def parse_var_decl_line(self) -> List[A.VarDecl]:
        names: List[Token] = [self.expect_ident()]
        while self.accept_sym(","):
            names.append(self.expect_ident())
        address = None
        if self.accept_kw("AT"):
            if self.cur.kind != "ADDRESS":
                raise self._fail("expected a located-variable address after AT")
            if len(names) != 1:
                raise self._fail("AT applies to a single variable only")
            address = str(self.take().value)
        self.expect_sym(":")
        type_ref = self.parse_type_spec()
        init = self.parse_initialiser() if self.accept_sym(":=") else None
        self.expect_sym(";")
        return [
            A.VarDecl(
                line=t.line,
                name=str(t.value),
                source_name=t.text,
                type_ref=type_ref,
                init=init,
                address=address,
            )
            for t in names
        ]

    def parse_initialiser(self) -> Any:
        """An initialiser is an expression, or `[a, b, c]` for an array."""
        if self.at_sym("["):
            self.take()
            items = [self.parse_initialiser()]
            while self.accept_sym(","):
                items.append(self.parse_initialiser())
            self.expect_sym("]")
            return items
        return self.parse_expr()

    def parse_type_spec(self) -> Any:
        line = self.cur.line
        if self.at_kw("ARRAY"):
            self.take()
            self.expect_sym("[")
            dims: List[Tuple[int, int]] = []
            while True:
                lo = self.parse_bound()
                self.expect_sym("..")
                hi = self.parse_bound()
                dims.append((lo, hi))
                if not self.accept_sym(","):
                    break
            self.expect_sym("]")
            self.expect_kw("OF")
            return A.ArrayTypeRef(line=line, dims=tuple(dims), element=self.parse_type_spec())
        tok = self.take()
        if tok.kind not in ("IDENT", "KEYWORD"):
            raise self._fail("expected a type name", tok)
        name = str(tok.value)
        string_len = None
        # STRING(24) and STRING[24] are both in circulation.
        if self.at_sym("[") or (name in ("STRING", "WSTRING") and self.at_sym("(")):
            closing = "]" if self.cur.value == "[" else ")"
            self.take()
            length = self.take()
            if length.kind != "NUMBER":
                raise self._fail("string length must be an integer literal", length)
            string_len = int(length.value)
            self.expect_sym(closing)
        return A.TypeRef(line=line, name=name, string_len=string_len)

    def parse_bound(self) -> int:
        negative = self.accept_sym("-")
        tok = self.take()
        if tok.kind != "NUMBER":
            raise self._fail("array bounds must be integer literals", tok)
        return -int(tok.value) if negative else int(tok.value)

    def parse_pou(self) -> A.POU:
        tok = self.take()
        kind = str(tok.value)
        name_tok = self.expect_ident()
        pou = A.POU(line=tok.line, kind=kind, name=str(name_tok.value), source_name=name_tok.text)
        if kind == "FUNCTION":
            self.expect_sym(":")
            pou.return_type = self.parse_type_spec()
        while self.cur.kind == "KEYWORD" and self.cur.value in VAR_BLOCK_KINDS:
            pou.var_blocks.append(self.parse_var_block())
        end_kw = "END_" + kind
        pou.body = self.parse_statements(until=(end_kw,))
        self.expect_kw(end_kw)
        self.accept_sym(";")
        return pou

    # -- statements --------------------------------------------------------

    def parse_statements(self, until: Tuple[str, ...], stop_at_case_label: bool = False) -> List[Any]:
        out: List[Any] = []
        while True:
            if self.at("EOF"):
                raise self._fail("expected one of {0}".format(", ".join(until)))
            if self.cur.kind == "KEYWORD" and self.cur.value in until:
                return out
            if stop_at_case_label and self._at_case_label():
                return out
            if self.accept_sym(";"):
                continue
            out.append(self.parse_statement())

    def _at_case_label(self) -> bool:
        """Does a new CASE branch start here, rather than another statement?

        `RUN:` opens a branch, `RUN := 1;` is an assignment and both begin with
        an identifier, so the only way to tell is to try the label grammar and
        see whether a ':' follows.
        """
        if self.cur.kind not in ("NUMBER", "IDENT") and not self.at_sym("-"):
            return False
        mark = self.i
        try:
            self.parse_case_label()
            while self.accept_sym(","):
                self.parse_case_label()
            return self.at_sym(":")
        except STSyntaxError:
            return False
        finally:
            self.i = mark

    def parse_statement(self) -> Any:
        tok = self.cur
        if tok.kind == "KEYWORD":
            word = tok.value
            if word == "IF":
                return self.parse_if()
            if word == "CASE":
                return self.parse_case()
            if word == "FOR":
                return self.parse_for()
            if word == "WHILE":
                return self.parse_while()
            if word == "REPEAT":
                return self.parse_repeat()
            if word == "EXIT":
                self.take()
                self.accept_sym(";")
                return A.Exit(line=tok.line)
            if word == "CONTINUE":
                self.take()
                self.accept_sym(";")
                return A.Continue(line=tok.line)
            if word == "RETURN":
                self.take()
                self.accept_sym(";")
                return A.Return(line=tok.line)
            raise self._fail("unexpected keyword in statement position")

        target = self.parse_variable()
        if self.at_sym(":="):
            self.take()
            value = self.parse_expr()
            self.accept_sym(";")
            return A.Assign(line=tok.line, target=target, value=value)
        if isinstance(target, A.Call):
            self.accept_sym(";")
            return A.CallStmt(line=tok.line, call=target)
        raise self._fail("expected ':=' or a function block invocation")

    def parse_if(self) -> A.If:
        line = self.expect_kw("IF").line
        node = A.If(line=line)
        cond = self.parse_expr()
        self.expect_kw("THEN")
        body = self.parse_statements(until=("ELSIF", "ELSE", "END_IF"))
        node.branches.append((cond, body))
        while self.at_kw("ELSIF"):
            self.take()
            cond = self.parse_expr()
            self.expect_kw("THEN")
            body = self.parse_statements(until=("ELSIF", "ELSE", "END_IF"))
            node.branches.append((cond, body))
        if self.accept_kw("ELSE"):
            node.orelse = self.parse_statements(until=("END_IF",))
        self.expect_kw("END_IF")
        self.accept_sym(";")
        return node

    def parse_case(self) -> A.Case:
        line = self.expect_kw("CASE").line
        selector = self.parse_expr()
        self.expect_kw("OF")
        node = A.Case(line=line, selector=selector)
        while not self.at_kw("ELSE", "END_CASE"):
            if self.at("EOF"):
                raise self._fail("unterminated CASE")
            labels = [self.parse_case_label()]
            while self.accept_sym(","):
                labels.append(self.parse_case_label())
            self.expect_sym(":")
            body = self.parse_statements(until=("ELSE", "END_CASE"), stop_at_case_label=True)
            node.branches.append((labels, body))
        if self.accept_kw("ELSE"):
            node.orelse = self.parse_statements(until=("END_CASE",))
        self.expect_kw("END_CASE")
        self.accept_sym(";")
        return node

    def parse_case_label(self) -> Any:
        low = self.parse_unary()
        if self.at_sym(".."):
            line = self.take().line
            return A.BinOp(line=line, op="..", left=low, right=self.parse_unary())
        return low

    def parse_for(self) -> A.For:
        line = self.expect_kw("FOR").line
        var = self.parse_variable()
        self.expect_sym(":=")
        start = self.parse_expr()
        self.expect_kw("TO")
        stop = self.parse_expr()
        step = self.parse_expr() if self.accept_kw("BY") else None
        self.expect_kw("DO")
        body = self.parse_statements(until=("END_FOR",))
        self.expect_kw("END_FOR")
        self.accept_sym(";")
        return A.For(line=line, var=var, start=start, stop=stop, step=step, body=body)

    def parse_while(self) -> A.While:
        line = self.expect_kw("WHILE").line
        cond = self.parse_expr()
        self.expect_kw("DO")
        body = self.parse_statements(until=("END_WHILE",))
        self.expect_kw("END_WHILE")
        self.accept_sym(";")
        return A.While(line=line, cond=cond, body=body)

    def parse_repeat(self) -> A.Repeat:
        line = self.expect_kw("REPEAT").line
        body = self.parse_statements(until=("UNTIL",))
        self.expect_kw("UNTIL")
        cond = self.parse_expr()
        self.expect_kw("END_REPEAT")
        self.accept_sym(";")
        return A.Repeat(line=line, body=body, until=cond)

    # -- expressions -------------------------------------------------------

    def parse_expr(self) -> Any:
        return self.parse_binary(0)

    def parse_binary(self, level: int) -> Any:
        if level >= len(_BINARY_LEVELS):
            return self.parse_unary()
        kind, ops = _BINARY_LEVELS[level]
        node = self.parse_binary(level + 1)
        while True:
            tok = self.cur
            if kind == "KEYWORD":
                matched = tok.kind == "KEYWORD" and tok.value in ops
            elif kind == "MUL":
                matched = (tok.kind == "SYMBOL" and tok.value in ("*", "/")) or (
                    tok.kind == "KEYWORD" and tok.value == "MOD"
                )
            else:
                matched = tok.kind == "SYMBOL" and tok.value in ops
            if not matched:
                return node
            self.take()
            right = self.parse_binary(level + 1)
            node = A.BinOp(line=tok.line, op=str(tok.value), left=node, right=right)

    def parse_unary(self) -> Any:
        tok = self.cur
        if self.at_sym("-"):
            self.take()
            return A.UnaryOp(line=tok.line, op="-", operand=self.parse_unary())
        if self.at_sym("+"):
            self.take()
            return self.parse_unary()
        if self.at_kw("NOT"):
            self.take()
            return A.UnaryOp(line=tok.line, op="NOT", operand=self.parse_unary())
        return self.parse_power()

    def parse_power(self) -> Any:
        base = self.parse_variable()
        if self.at_sym("**"):
            tok = self.take()
            # Right associative and the exponent may itself be negated.
            return A.BinOp(line=tok.line, op="**", left=base, right=self.parse_unary())
        return base

    def parse_variable(self) -> Any:
        """A primary expression plus any chain of `.field`, `[i]` and `(args)`."""
        tok = self.cur
        if tok.kind == "NUMBER":
            self.take()
            node: Any = A.Literal(line=tok.line, value=int(tok.value), kind="NUMBER")
        elif tok.kind == "REAL":
            self.take()
            node = A.Literal(line=tok.line, value=float(tok.value), kind="REAL")
        elif tok.kind == "TIME":
            self.take()
            node = A.Literal(line=tok.line, value=float(tok.value), kind="TIME")
        elif tok.kind == "STRING":
            self.take()
            node = A.Literal(line=tok.line, value=str(tok.value), kind="STRING")
        elif tok.kind == "BOOL":
            self.take()
            node = A.Literal(line=tok.line, value=bool(tok.value), kind="BOOL")
        elif self.at_kw("TRUE", "FALSE"):
            self.take()
            node = A.Literal(line=tok.line, value=tok.value == "TRUE", kind="BOOL")
        elif self.at_sym("("):
            self.take()
            node = self.parse_expr()
            self.expect_sym(")")
        elif tok.kind == "IDENT":
            self.take()
            node = A.VarRef(line=tok.line, name=str(tok.value), source_name=tok.text)
        else:
            raise self._fail("expected an expression")

        while True:
            if self.at_sym("."):
                self.take()
                field = self.take()
                if field.kind not in ("IDENT", "KEYWORD"):
                    raise self._fail("expected a member name", field)
                node = A.Member(line=field.line, base=node, name=str(field.value))
            elif self.at_sym("["):
                line = self.take().line
                subs = [self.parse_expr()]
                while self.accept_sym(","):
                    subs.append(self.parse_expr())
                self.expect_sym("]")
                node = A.Index(line=line, base=node, subscripts=subs)
            elif self.at_sym("("):
                line = self.take().line
                args = self.parse_args()
                node = A.Call(line=line, target=node, args=args)
            else:
                return node

    def parse_args(self) -> List[A.Arg]:
        args: List[A.Arg] = []
        if self.accept_sym(")"):
            return args
        while True:
            line = self.cur.line
            name = None
            is_output = False
            # `NAME := expr` and `NAME => variable` are the named forms; a bare
            # expression is positional. Look two tokens ahead to tell them apart.
            if self.cur.kind == "IDENT" and self.peek().kind == "SYMBOL":
                if self.peek().value == ":=":
                    name = str(self.take().value)
                    self.take()
                elif self.peek().value == "=>":
                    name = str(self.take().value)
                    self.take()
                    is_output = True
            value = self.parse_expr()
            args.append(A.Arg(line=line, name=name, value=value, is_output=is_output))
            if not self.accept_sym(","):
                break
        self.expect_sym(")")
        return args


def parse(source: str, filename: Optional[str] = None) -> A.Unit:
    return Parser(tokenize(source, filename), filename).parse_unit()
