"""Tokeniser for the Structured Text subset.

IEC 61131-3 is case insensitive for both keywords and identifiers, so every
identifier is folded to upper case here and the runtime uses the folded form as
its lookup key. The original spelling is kept on the token for error messages.
"""

from __future__ import annotations

import re
from typing import List, NamedTuple, Optional

from .errors import STSyntaxError

KEYWORDS = frozenset(
    """
    PROGRAM END_PROGRAM FUNCTION_BLOCK END_FUNCTION_BLOCK FUNCTION END_FUNCTION
    VAR VAR_INPUT VAR_OUTPUT VAR_IN_OUT VAR_GLOBAL VAR_TEMP END_VAR
    CONSTANT RETAIN NON_RETAIN AT TYPE END_TYPE STRUCT END_STRUCT ARRAY OF
    IF THEN ELSIF ELSE END_IF CASE END_CASE FOR TO BY DO END_FOR
    WHILE END_WHILE REPEAT UNTIL END_REPEAT EXIT CONTINUE RETURN
    TRUE FALSE AND OR XOR NOT MOD
    """.split()
)

# Longest first so that ':=' never lexes as ':' followed by '='.
SYMBOLS = [
    ":=",
    "=>",
    "<=",
    ">=",
    "<>",
    "**",
    "..",
    "+",
    "-",
    "*",
    "/",
    "(",
    ")",
    "[",
    "]",
    ",",
    ";",
    ":",
    ".",
    "=",
    "<",
    ">",
]

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# 16#FF, 2#1010, 8#777 and plain decimals, with optional _ separators.
_BASED_INT_RE = re.compile(r"(\d+)#([0-9A-Fa-f_]+)")
_REAL_RE = re.compile(r"\d[\d_]*\.\d[\d_]*([eE][+-]?\d+)?|\d[\d_]*[eE][+-]?\d+")
_INT_RE = re.compile(r"\d[\d_]*")
_ADDRESS_RE = re.compile(r"%[IQM][XBWDL]?\d+(\.\d+)*")
# T#1d2h3m4s500ms, also spelled TIME# and negative durations.
_DURATION_RE = re.compile(
    r"(-?)"
    r"(?:(\d+(?:\.\d+)?)d)?"
    r"(?:(\d+(?:\.\d+)?)h)?"
    r"(?:(\d+(?:\.\d+)?)m(?!s))?"
    r"(?:(\d+(?:\.\d+)?)s)?"
    r"(?:(\d+(?:\.\d+)?)ms)?",
    re.IGNORECASE,
)

TYPE_PREFIXES = frozenset(
    """
    BOOL SINT INT DINT LINT USINT UINT UDINT ULINT BYTE WORD DWORD LWORD
    REAL LREAL TIME STRING WSTRING
    """.split()
)

_ESCAPES = {"L": "\n", "N": "\n", "P": "\f", "R": "\r", "T": "\t", "$": "$"}


class Token(NamedTuple):
    kind: str  # KEYWORD IDENT NUMBER REAL TIME STRING BOOL SYMBOL ADDRESS EOF
    value: object
    text: str
    line: int


def _strip_underscores(text: str) -> str:
    return text.replace("_", "")


class Lexer:
    def __init__(self, source: str, filename: Optional[str] = None):
        self.src = source
        self.filename = filename
        self.pos = 0
        self.line = 1

    def _fail(self, message: str) -> STSyntaxError:
        return STSyntaxError(message, line=self.line, file=self.filename)

    def _skip_trivia(self) -> None:
        src, n = self.src, len(self.src)
        while self.pos < n:
            ch = src[self.pos]
            if ch == "\n":
                self.line += 1
                self.pos += 1
            elif ch in " \t\r":
                self.pos += 1
            elif src.startswith("(*", self.pos):
                end = src.find("*)", self.pos + 2)
                if end < 0:
                    raise self._fail("unterminated (* comment")
                self.line += src.count("\n", self.pos, end)
                self.pos = end + 2
            elif src.startswith("/*", self.pos):
                end = src.find("*/", self.pos + 2)
                if end < 0:
                    raise self._fail("unterminated /* comment")
                self.line += src.count("\n", self.pos, end)
                self.pos = end + 2
            elif src.startswith("//", self.pos):
                end = src.find("\n", self.pos)
                self.pos = n if end < 0 else end
            elif ch == "{":
                # Compiler pragma, e.g. {attribute 'hide'}. Not interpreted.
                end = src.find("}", self.pos)
                if end < 0:
                    raise self._fail("unterminated { pragma")
                self.line += src.count("\n", self.pos, end)
                self.pos = end + 1
            else:
                return

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []
        src = self.src
        n = len(src)
        while True:
            self._skip_trivia()
            if self.pos >= n:
                tokens.append(Token("EOF", None, "", self.line))
                return tokens
            line = self.line
            ch = src[self.pos]

            if ch == "'" or ch == '"':
                tokens.append(self._read_string(ch, line))
                continue

            if ch == "%":
                m = _ADDRESS_RE.match(src, self.pos)
                if not m:
                    raise self._fail("malformed located-variable address")
                tokens.append(Token("ADDRESS", m.group(0).upper(), m.group(0), line))
                self.pos = m.end()
                continue

            if ch.isdigit():
                tokens.append(self._read_number(line))
                continue

            m = _IDENT_RE.match(src, self.pos)
            if m:
                word = m.group(0)
                upper = word.upper()
                # Durations, typed literals and enum-qualified literals all
                # look like an identifier followed by '#'.
                if src[m.end() : m.end() + 1] == "#":
                    self.pos = m.end() + 1
                    if upper in ("T", "TIME", "LT", "LTIME"):
                        tokens.append(self._read_duration(line, word + "#"))
                        continue
                    if upper in TYPE_PREFIXES:
                        tokens.append(self._read_typed_literal(upper, line))
                        continue
                    # E_Phase#RINSE: the member name resolves on its own.
                    continue
                self.pos = m.end()
                kind = "KEYWORD" if upper in KEYWORDS else "IDENT"
                tokens.append(Token(kind, upper, word, line))
                continue

            for sym in SYMBOLS:
                if src.startswith(sym, self.pos):
                    self.pos += len(sym)
                    tokens.append(Token("SYMBOL", sym, sym, line))
                    break
            else:
                raise self._fail("unexpected character {0!r}".format(ch))

    def _read_string(self, quote: str, line: int) -> Token:
        src = self.src
        i = self.pos + 1
        out = []
        while True:
            if i >= len(src):
                raise self._fail("unterminated string literal")
            c = src[i]
            if c == quote:
                i += 1
                break
            if c == "$":
                # IEC uses $ as its escape introducer, not backslash.
                nxt = src[i + 1 : i + 2]
                upper = nxt.upper()
                if upper in _ESCAPES:
                    out.append(_ESCAPES[upper])
                    i += 2
                    continue
                if nxt == "'" or nxt == '"':
                    out.append(nxt)
                    i += 2
                    continue
                if re.match(r"[0-9A-Fa-f]{2}", src[i + 1 : i + 3]):
                    out.append(chr(int(src[i + 1 : i + 3], 16)))
                    i += 3
                    continue
                raise self._fail("bad $ escape in string literal")
            if c == "\n":
                raise self._fail("newline inside string literal")
            out.append(c)
            i += 1
        text = "".join(out)
        self.pos = i
        return Token("STRING", text, text, line)

    def _read_duration(self, line: int, prefix: str = "T#") -> Token:
        m = _DURATION_RE.match(self.src, self.pos)
        if not m or m.end() == self.pos:
            raise self._fail("malformed duration literal")
        sign = -1.0 if m.group(1) else 1.0
        parts = [float(g) if g else 0.0 for g in m.group(2, 3, 4, 5, 6)]
        if not any(g for g in m.group(2, 3, 4, 5, 6)):
            raise self._fail("duration literal has no components")
        days, hours, minutes, seconds, millis = parts
        total = days * 86400.0 + hours * 3600.0 + minutes * 60.0 + seconds + millis / 1000.0
        self.pos = m.end()
        return Token("TIME", sign * total, prefix + m.group(0), line)

    def _read_typed_literal(self, type_name: str, line: int) -> Token:
        src = self.src
        if type_name == "BOOL":
            m = re.compile(r"TRUE|FALSE|1|0", re.IGNORECASE).match(src, self.pos)
            if not m:
                raise self._fail("malformed BOOL# literal")
            self.pos = m.end()
            return Token("BOOL", m.group(0).upper() in ("TRUE", "1"), m.group(0), line)
        if type_name in ("STRING", "WSTRING"):
            return self._read_string(src[self.pos], line)
        if type_name == "TIME":
            return self._read_duration(line, "TIME#")
        tok = self._read_number(line)
        if type_name in ("REAL", "LREAL"):
            return Token("REAL", float(tok.value), tok.text, line)
        return Token("NUMBER", int(tok.value), tok.text, line)

    def _read_number(self, line: int) -> Token:
        src = self.src
        m = _BASED_INT_RE.match(src, self.pos)
        if m:
            base = int(m.group(1))
            if base not in (2, 8, 16):
                raise self._fail("unsupported integer base {0}".format(base))
            self.pos = m.end()
            return Token("NUMBER", int(_strip_underscores(m.group(2)), base), m.group(0), line)
        m = _REAL_RE.match(src, self.pos)
        if m:
            self.pos = m.end()
            return Token("REAL", float(_strip_underscores(m.group(0))), m.group(0), line)
        m = _INT_RE.match(src, self.pos)
        if m:
            self.pos = m.end()
            return Token("NUMBER", int(_strip_underscores(m.group(0))), m.group(0), line)
        raise self._fail("malformed numeric literal")


def tokenize(source: str, filename: Optional[str] = None) -> List[Token]:
    return Lexer(source, filename).tokenize()
