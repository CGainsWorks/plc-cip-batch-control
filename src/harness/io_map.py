"""The I/O map, derived from the ST declarations rather than written twice.

`build_io_map` reads the located variables back out of a compiled Runtime and
produces the tables the simulated field bus and the Modbus TCP server both use.
Adding `AT %QX2.0` to GVL_Io.st is therefore the only edit needed to expose a
new coil.

Modbus placement:

    %IXb.i  -> discrete input  b*8 + i
    %QXb.i  -> coil            b*8 + i
    %MXb.i  -> coil            1000 + b*8 + i
    %IDn    -> input registers   2n, 2n+1   (float32, high word first)
    %QDn    -> holding registers 2n, 2n+1
    %MWn    -> holding register  1000 + n   (int16)
    %MDn    -> holding registers 2000 + 2n, +1
"""

from __future__ import annotations

import struct
from typing import Dict, Iterable, List, NamedTuple, Tuple

from ..iec61131.address import Address
from ..iec61131.runtime import Runtime

COIL_MEMORY_BASE = 1000
HOLDING_WORD_BASE = 1000
HOLDING_MEMORY_FLOAT_BASE = 2000


class Point(NamedTuple):
    name: str
    address: Address
    type_name: str
    table: str  # discrete_input, coil, input_register, holding_register
    offset: int
    words: int


class IoMap(NamedTuple):
    points: Tuple[Point, ...]

    def by_table(self, table: str) -> List[Point]:
        return [p for p in self.points if p.table == table]

    def inputs(self) -> List[Point]:
        return [p for p in self.points if p.address.prefix == "I"]

    def outputs(self) -> List[Point]:
        return [p for p in self.points if p.address.prefix == "Q"]

    def memory(self) -> List[Point]:
        return [p for p in self.points if p.address.prefix == "M"]

    def find(self, name: str) -> Point:
        key = name.upper()
        for point in self.points:
            if point.name == key:
                return point
        raise KeyError(name)


def build_io_map(runtime: Runtime) -> IoMap:
    points: List[Point] = []
    for name, var in sorted(runtime.located.items()):
        address = var.address
        assert address is not None
        type_name = var.type.name
        if address.is_bit:
            bit_offset = address.word * 8 + address.bit
            if address.prefix == "I":
                table, offset = "discrete_input", bit_offset
            elif address.prefix == "Q":
                table, offset = "coil", bit_offset
            else:
                table, offset = "coil", COIL_MEMORY_BASE + bit_offset
            points.append(Point(name, address, type_name, table, offset, 1))
            continue
        if address.size == "D":
            if address.prefix == "I":
                table, offset = "input_register", 2 * address.word
            elif address.prefix == "Q":
                table, offset = "holding_register", 2 * address.word
            else:
                table, offset = "holding_register", HOLDING_MEMORY_FLOAT_BASE + 2 * address.word
            points.append(Point(name, address, type_name, table, offset, 2))
            continue
        if address.size == "W":
            if address.prefix == "I":
                table, offset = "input_register", address.word
            elif address.prefix == "Q":
                table, offset = "holding_register", address.word
            else:
                table, offset = "holding_register", HOLDING_WORD_BASE + address.word
            points.append(Point(name, address, type_name, table, offset, 1))
            continue
        raise ValueError("unsupported address size in {0}: {1}".format(name, address))
    _assert_no_overlap(points)
    return IoMap(tuple(points))


def _assert_no_overlap(points: Iterable[Point]) -> None:
    seen: Dict[Tuple[str, int], str] = {}
    for point in points:
        for word in range(point.words):
            key = (point.table, point.offset + word)
            if key in seen:
                raise ValueError(
                    "{0} and {1} both map to {2} {3}".format(
                        seen[key], point.name, key[0], key[1]
                    )
                )
            seen[key] = point.name


def float_to_words(value: float) -> Tuple[int, int]:
    """IEEE 754 single precision, high word first."""
    raw = struct.pack(">f", float(value))
    high, low = struct.unpack(">HH", raw)
    return high, low


def words_to_float(high: int, low: int) -> float:
    return struct.unpack(">f", struct.pack(">HH", high & 0xFFFF, low & 0xFFFF))[0]


def int_to_word(value: int) -> int:
    return int(value) & 0xFFFF


def word_to_int(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value >= 0x8000 else value
