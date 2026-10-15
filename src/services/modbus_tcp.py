"""A small Modbus TCP server that publishes the PLC image.

Written against the specification rather than pulled in as a dependency, so the
repository has no runtime requirements at all and the wire format is visible.
Supported function codes: 1, 2, 3, 4, 5, 6, 15, 16.

The bridge is deliberately not thread-safe with respect to the interpreter.
The socket thread only ever touches a snapshot dict and a queue of pending
writes; the scan loop calls `refresh()` and `apply_writes()` between scans, so
a SCADA client can never observe a half-executed scan.
"""

from __future__ import annotations

import socket
import socketserver
import struct
import threading
from typing import Dict, List, Optional, Tuple

from ..harness.io_map import (
    IoMap,
    Point,
    float_to_words,
    int_to_word,
    word_to_int,
    words_to_float,
)
from ..iec61131.runtime import Runtime

ILLEGAL_FUNCTION = 0x01
ILLEGAL_DATA_ADDRESS = 0x02
ILLEGAL_DATA_VALUE = 0x03
SERVER_FAILURE = 0x04

# How long a connection may sit idle before the server lets it go.
IDLE_TIMEOUT_S = 120.0


class ModbusBridge:
    """The shared image between the scan loop and the socket thread."""

    def __init__(self, runtime: Runtime, io_map: IoMap):
        self.runtime = runtime
        self.io_map = io_map
        self.lock = threading.Lock()
        self.coils: Dict[int, bool] = {}
        self.discrete_inputs: Dict[int, bool] = {}
        self.input_registers: Dict[int, int] = {}
        self.holding_registers: Dict[int, int] = {}
        self._writable: Dict[Tuple[str, int], Point] = {}
        self._pending: List[Tuple[str, int, int]] = []
        for point in io_map.points:
            # SCADA may drive the memory area. Writing to %Q would just be
            # overwritten by the next scan, so those stay read-only.
            if point.address.prefix == "M":
                self._writable[(point.table, point.offset)] = point
        self.refresh()

    # -- called from the scan loop ----------------------------------------

    def refresh(self) -> None:
        rt = self.runtime
        coils: Dict[int, bool] = {}
        discrete: Dict[int, bool] = {}
        inputs: Dict[int, int] = {}
        holding: Dict[int, int] = {}
        for point in self.io_map.points:
            value = rt.get_path(point.name)
            if point.table == "discrete_input":
                discrete[point.offset] = bool(value)
            elif point.table == "coil":
                coils[point.offset] = bool(value)
            elif point.words == 2:
                high, low = float_to_words(float(value))
                target = inputs if point.table == "input_register" else holding
                target[point.offset] = high
                target[point.offset + 1] = low
            else:
                word = int_to_word(int(value))
                target = inputs if point.table == "input_register" else holding
                target[point.offset] = word
        with self.lock:
            self.coils = coils
            self.discrete_inputs = discrete
            self.input_registers = inputs
            self.holding_registers = holding

    def apply_writes(self) -> int:
        """Push anything a client wrote into the PLC. Returns the count."""
        with self.lock:
            pending, self._pending = self._pending, []
        applied = 0
        for table, offset, raw in pending:
            point = self._writable.get((table, offset))
            if point is None:
                # A write to the low word of a float: find the owning point.
                point = self._writable.get((table, offset - 1))
                if point is None or point.words != 2:
                    continue
            if point.table == "coil":
                self.runtime.set_path(point.name, bool(raw))
            elif point.words == 2:
                high = self.holding_registers.get(point.offset, 0)
                low = self.holding_registers.get(point.offset + 1, 0)
                if offset == point.offset:
                    high = raw
                else:
                    low = raw
                self.runtime.set_path(point.name, words_to_float(high, low))
            else:
                self.runtime.set_path(point.name, word_to_int(raw))
            applied += 1
        if applied:
            self.refresh()
        return applied

    # -- called from the socket thread -------------------------------------

    def queue_write(self, table: str, offset: int, raw: int) -> bool:
        key = (table, offset)
        if key not in self._writable and (table, offset - 1) not in self._writable:
            return False
        with self.lock:
            self._pending.append((table, offset, raw))
            if table == "coil":
                self.coils[offset] = bool(raw)
            else:
                self.holding_registers[offset] = raw & 0xFFFF
        return True

    def read_bits(self, table: str, address: int, count: int) -> Optional[List[bool]]:
        source = self.coils if table == "coil" else self.discrete_inputs
        with self.lock:
            return [bool(source.get(address + i, False)) for i in range(count)]

    def read_words(self, table: str, address: int, count: int) -> Optional[List[int]]:
        source = self.input_registers if table == "input_register" else self.holding_registers
        with self.lock:
            return [int(source.get(address + i, 0)) & 0xFFFF for i in range(count)]


def build_response(bridge: ModbusBridge, unit: int, pdu: bytes) -> bytes:
    if not pdu:
        return b""
    function = pdu[0]
    try:
        if function in (1, 2, 3, 4):
            address, count = struct.unpack(">HH", pdu[1:5])
            if function in (1, 2):
                if not 1 <= count <= 2000:
                    return _exception(function, ILLEGAL_DATA_VALUE)
                table = "coil" if function == 1 else "discrete_input"
                bits = bridge.read_bits(table, address, count)
                payload = _pack_bits(bits)
                return bytes([function, len(payload)]) + payload
            if not 1 <= count <= 125:
                return _exception(function, ILLEGAL_DATA_VALUE)
            table = "holding_register" if function == 3 else "input_register"
            words = bridge.read_words(table, address, count)
            payload = b"".join(struct.pack(">H", w) for w in words)
            return bytes([function, len(payload)]) + payload

        if function == 5:
            address, value = struct.unpack(">HH", pdu[1:5])
            if value not in (0x0000, 0xFF00):
                return _exception(function, ILLEGAL_DATA_VALUE)
            if not bridge.queue_write("coil", address, 1 if value else 0):
                return _exception(function, ILLEGAL_DATA_ADDRESS)
            return pdu[:5]

        if function == 6:
            address, value = struct.unpack(">HH", pdu[1:5])
            if not bridge.queue_write("holding_register", address, value):
                return _exception(function, ILLEGAL_DATA_ADDRESS)
            return pdu[:5]

        if function == 15:
            address, count, byte_count = struct.unpack(">HHB", pdu[1:6])
            data = pdu[6 : 6 + byte_count]
            if count < 1 or byte_count != (count + 7) // 8 or len(data) != byte_count:
                return _exception(function, ILLEGAL_DATA_VALUE)
            for index in range(count):
                bit = (data[index // 8] >> (index % 8)) & 1
                if not bridge.queue_write("coil", address + index, bit):
                    return _exception(function, ILLEGAL_DATA_ADDRESS)
            return pdu[:5]

        if function == 16:
            address, count, byte_count = struct.unpack(">HHB", pdu[1:6])
            if count < 1 or byte_count != count * 2 or len(pdu) < 6 + byte_count:
                return _exception(function, ILLEGAL_DATA_VALUE)
            for index in range(count):
                (value,) = struct.unpack(">H", pdu[6 + index * 2 : 8 + index * 2])
                if not bridge.queue_write("holding_register", address + index, value):
                    return _exception(function, ILLEGAL_DATA_ADDRESS)
            return pdu[:5]
    except struct.error:
        return _exception(function, ILLEGAL_DATA_VALUE)
    except Exception:  # noqa: BLE001 - a server must not die on a bad request
        return _exception(function, SERVER_FAILURE)

    return _exception(function, ILLEGAL_FUNCTION)


def _exception(function: int, code: int) -> bytes:
    return bytes([function | 0x80, code])


def _pack_bits(bits: List[bool]) -> bytes:
    out = bytearray((len(bits) + 7) // 8)
    for index, bit in enumerate(bits):
        if bit:
            out[index // 8] |= 1 << (index % 8)
    return bytes(out)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        bridge: ModbusBridge = self.server.bridge  # type: ignore[attr-defined]
        # SCADA clients hold a connection open between polls and a poll may be
        # a minute apart. Five seconds would drop them mid-cycle.
        self.request.settimeout(IDLE_TIMEOUT_S)
        buffer = b""
        while True:
            try:
                chunk = self.request.recv(4096)
            except (socket.timeout, OSError):
                return
            if not chunk:
                return
            buffer += chunk
            while len(buffer) >= 8:
                transaction, protocol, length, unit = struct.unpack(">HHHB", buffer[:7])
                if protocol != 0 or length < 2:
                    return
                total = 6 + length
                if len(buffer) < total:
                    break
                pdu = buffer[7:total]
                buffer = buffer[total:]
                response = build_response(bridge, unit, pdu)
                if not response:
                    continue
                header = struct.pack(">HHHB", transaction, 0, len(response) + 1, unit)
                try:
                    self.request.sendall(header + response)
                except OSError:
                    return


class ModbusServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, bridge: ModbusBridge, host: str = "127.0.0.1", port: int = 5020):
        self.bridge = bridge
        super().__init__((host, port), _Handler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    def serve_in_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="modbus-tcp", daemon=True)
        thread.start()
        return thread


class ModbusClient:
    """Just enough client to drive the server from a script or a test."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5020, timeout: float = 5.0):
        self.sock = socket.create_connection((host, port), timeout)
        self.transaction = 0

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self) -> "ModbusClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(self, pdu: bytes, unit: int = 1) -> bytes:
        self.transaction = (self.transaction + 1) & 0xFFFF
        header = struct.pack(">HHHB", self.transaction, 0, len(pdu) + 1, unit)
        self.sock.sendall(header + pdu)
        head = self._recv_exactly(7)
        _tid, _pid, length, _unit = struct.unpack(">HHHB", head)
        body = self._recv_exactly(length - 1)
        if body[0] & 0x80:
            raise IOError("Modbus exception {0} for function {1}".format(body[1], body[0] & 0x7F))
        return body

    def _recv_exactly(self, count: int) -> bytes:
        out = b""
        while len(out) < count:
            chunk = self.sock.recv(count - len(out))
            if not chunk:
                raise IOError("connection closed by the server")
            out += chunk
        return out

    def read_discrete_inputs(self, address: int, count: int) -> List[bool]:
        body = self._request(struct.pack(">BHH", 2, address, count))
        return _unpack_bits(body[2:], count)

    def read_coils(self, address: int, count: int) -> List[bool]:
        body = self._request(struct.pack(">BHH", 1, address, count))
        return _unpack_bits(body[2:], count)

    def read_holding_registers(self, address: int, count: int) -> List[int]:
        body = self._request(struct.pack(">BHH", 3, address, count))
        return list(struct.unpack(">" + "H" * count, body[2 : 2 + count * 2]))

    def read_input_registers(self, address: int, count: int) -> List[int]:
        body = self._request(struct.pack(">BHH", 4, address, count))
        return list(struct.unpack(">" + "H" * count, body[2 : 2 + count * 2]))

    def read_float(self, table: str, address: int) -> float:
        words = (
            self.read_input_registers(address, 2)
            if table == "input_register"
            else self.read_holding_registers(address, 2)
        )
        return words_to_float(words[0], words[1])

    def write_coil(self, address: int, value: bool) -> None:
        self._request(struct.pack(">BHH", 5, address, 0xFF00 if value else 0x0000))

    def write_register(self, address: int, value: int) -> None:
        self._request(struct.pack(">BHH", 6, address, value & 0xFFFF))


def _unpack_bits(payload: bytes, count: int) -> List[bool]:
    return [bool((payload[index // 8] >> (index % 8)) & 1) for index in range(count)]
