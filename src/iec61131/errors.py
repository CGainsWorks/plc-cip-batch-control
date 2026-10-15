"""Exceptions raised while compiling or executing Structured Text."""

from __future__ import annotations

from typing import Optional


class STError(Exception):
    """Base class for every error this package raises."""

    def __init__(self, message: str, line: Optional[int] = None, file: Optional[str] = None):
        self.message = message
        self.line = line
        self.file = file
        where = ""
        if file is not None:
            where = " ({}{})".format(file, ":{}".format(line) if line is not None else "")
        elif line is not None:
            where = " (line {})".format(line)
        super().__init__(message + where)


class STSyntaxError(STError):
    """The source could not be tokenised or parsed."""


class STDeclarationError(STError):
    """The source parsed but the declarations do not hang together."""


class STRuntimeError(STError):
    """Raised while a scan is executing."""
