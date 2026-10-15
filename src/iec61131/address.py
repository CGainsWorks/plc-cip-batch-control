"""Located-variable addresses (`AT %IX0.0`, `AT %QD12`).

The ST declarations are the single source of truth for the I/O map: the
simulated field bus and the Modbus TCP server both derive their tables from the
addresses parsed here, so an address cannot drift out of step with the program
that uses it.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

from .errors import STDeclarationError

_RE = re.compile(r"^%(?P<prefix>[IQM])(?P<size>[XBWDL]?)(?P<parts>\d+(?:\.\d+)*)$")

# Width of each size letter, in bits.
SIZE_BITS = {"X": 1, "B": 8, "W": 16, "D": 32, "L": 64}


class Address(NamedTuple):
    text: str
    prefix: str  # I input, Q output, M memory
    size: str  # X B W D L
    word: int  # the numeric part before the dot
    bit: int  # the bit within the word, 0 when not a bit address

    @property
    def bits(self) -> int:
        return SIZE_BITS[self.size]

    @property
    def is_bit(self) -> bool:
        return self.size == "X"

    def __str__(self) -> str:
        return self.text


def parse_address(text: str, line: Optional[int] = None) -> Address:
    match = _RE.match(text.upper())
    if not match:
        raise STDeclarationError("malformed address {0!r}".format(text), line=line)
    size = match.group("size") or "X"
    parts = [int(p) for p in match.group("parts").split(".")]
    if size == "X":
        if len(parts) == 1:
            word, bit = parts[0] // 8, parts[0] % 8
        elif len(parts) == 2:
            word, bit = parts
        else:
            raise STDeclarationError("bit address {0!r} has too many parts".format(text), line=line)
        if bit > 7:
            raise STDeclarationError("bit index {0} exceeds 7 in {1!r}".format(bit, text), line=line)
    else:
        if len(parts) != 1:
            raise STDeclarationError(
                "{0!r} is a word address and takes no bit index".format(text), line=line
            )
        word, bit = parts[0], 0
    return Address(text=text.upper(), prefix=match.group("prefix"), size=size, word=word, bit=bit)
