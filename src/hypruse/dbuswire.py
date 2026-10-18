"""Minimal D-Bus client, for the accessibility bus.

AT-SPI reads used to run one busctl process per call: about 10 ms each,
and nearly 20 through Flatpak's D-Bus proxy, so walking a large web app's
tree (ZapZap's 904 objects) took 41 seconds. One connection held open
answers the same call in about 1.5 ms. This is only what hypruse needs: a
unix socket, EXTERNAL authentication, method calls one at a time and their
replies, little-endian messages. An address it cannot use raises
Unreachable, and the caller falls back to busctl.

Wire format (D-Bus specification, "Message Protocol"):
    header: y endianness 'l', y type, y flags, y version 1, u body length,
            u serial, a(yv) header fields, padded to 8
    body:   the arguments, marshalled per the SIGNATURE header field
Every value is aligned to its own size (structs and dict entries to 8),
counted from the start of the message.
"""

from __future__ import annotations

import contextlib
import os
import socket
import struct
import threading
import time
from typing import Any
from urllib.parse import unquote


class DBusError(RuntimeError):
    """A call did not complete: no answer in time, or the connection broke."""


class Unreachable(DBusError):
    """No connection could be made to this address."""


class CallError(DBusError):
    """The other side answered the call with a D-Bus error."""

    def __init__(self, name: str, message: str):
        super().__init__(f"{name}: {message}" if message else name)
        self.name = name


_BUS = "org.freedesktop.DBus"
_METHOD_CALL, _METHOD_RETURN, _ERROR = 1, 2, 3
# header field codes
_PATH, _INTERFACE, _MEMBER, _ERROR_NAME, _REPLY_SERIAL, _DESTINATION = 1, 2, 3, 4, 5, 6
_SIGNATURE = 8
_MAX_MESSAGE = 1 << 27  # the specification's limit

_ALIGN = {"y": 1, "b": 4, "n": 2, "q": 2, "i": 4, "u": 4, "x": 8, "t": 8, "d": 8,
          "h": 4, "s": 4, "o": 4, "g": 1, "a": 4, "(": 8, "{": 8, "v": 1}
_FIXED = {"y": "B", "b": "I", "n": "h", "q": "H", "i": "i", "u": "I", "x": "q",
          "t": "Q", "d": "d", "h": "I"}


def split_signature(sig: str) -> list[str]:
    """The single complete types of a signature: "a(so)ib" is ["a(so)", "i", "b"]."""
    out, i = [], 0
    while i < len(sig):
        j = _type_end(sig, i)
        out.append(sig[i:j])
        i = j
    return out


def _type_end(sig: str, i: int) -> int:
    if i >= len(sig):
        raise DBusError(f"incomplete signature {sig!r}")
    c = sig[i]
    if c == "a":
        return _type_end(sig, i + 1)
    if c in "({":
        close = ")" if c == "(" else "}"
        j = i + 1
        while j < len(sig) and sig[j] != close:
            j = _type_end(sig, j)
        if j >= len(sig):
            raise DBusError(f"unclosed {c!r} in signature {sig!r}")
        return j + 1
    if c not in _ALIGN:
        raise DBusError(f"unknown type {c!r} in signature {sig!r}")
    return i + 1


class _Writer:
    def __init__(self, prefix: bytes = b""):
        self.buf = bytearray(prefix)

    def _pad(self, n: int) -> None:
        self.buf += b"\0" * (-len(self.buf) % n)

    def put(self, t: str, v: Any) -> None:
        c = t[0]
        self._pad(_ALIGN[c])
        if c in _FIXED:
            self.buf += struct.pack("<" + _FIXED[c], v)
        elif c in "so":
            data = str(v).encode()
            self.buf += struct.pack("<I", len(data)) + data + b"\0"
        elif c == "g":
            data = str(v).encode()
            self.buf += bytes([len(data)]) + data + b"\0"
        elif c == "v":
            sig, value = v
            self.put("g", sig)
            self.put(sig, value)
        elif c == "a":
            item = t[1:]
            at = len(self.buf)
            self.buf += b"\0\0\0\0"
            self._pad(_ALIGN[item[0]])  # even for an empty array
            start = len(self.buf)
            for x in (v.items() if item[0] == "{" else v):
                self.put(item, x)
            struct.pack_into("<I", self.buf, at, len(self.buf) - start)
        elif c in "({":
            for st, sv in zip(split_signature(t[1:-1]), v, strict=True):
                self.put(st, sv)


class _Reader:
    def __init__(self, data: bytes, offset: int = 0):
        self.data, self.off = data, offset

    def _pad(self, n: int) -> None:
        self.off += -self.off % n

    def _need(self, n: int) -> None:
        if self.off + n > len(self.data):
            raise DBusError("truncated message")

    def get(self, t: str) -> Any:
        c = t[0]
        self._pad(_ALIGN[c])
        if c in _FIXED:
            fmt = "<" + _FIXED[c]
            size = struct.calcsize(fmt)
            self._need(size)
            (v,) = struct.unpack_from(fmt, self.data, self.off)
            self.off += size
            return bool(v) if c == "b" else v
        if c in "so":
            self._need(4)
            (n,) = struct.unpack_from("<I", self.data, self.off)
            self._need(4 + n + 1)
            v = self.data[self.off + 4:self.off + 4 + n].decode("utf-8", "replace")
            self.off += 4 + n + 1
            return v
        if c == "g":
            self._need(1)
            n = self.data[self.off]
            self._need(1 + n + 1)
            v = self.data[self.off + 1:self.off + 1 + n].decode("ascii", "replace")
            self.off += 1 + n + 1
            return v
        if c == "v":
            return self.get(self.get("g"))
        if c == "a":
            self._need(4)
            (n,) = struct.unpack_from("<I", self.data, self.off)
            self.off += 4
            item = t[1:]
            self._pad(_ALIGN[item[0]])
            end = self.off + n
            if end > len(self.data):
                raise DBusError("truncated message")
            out = []
            while self.off < end:
                out.append(self.get(item))
            return dict(out) if item[0] == "{" else out
        if c in "({":
            # a list, not a tuple: the shape busctl's JSON gave every caller
            return [self.get(st) for st in split_signature(t[1:-1])]
        raise DBusError(f"unknown type {c!r}")


def marshal(sig: str, args: list[Any] | tuple[Any, ...]) -> bytes:
    """The body bytes for these arguments (a body starts 8-aligned)."""
    types = split_signature(sig)
    if len(types) != len(args):
        raise DBusError(f"signature {sig!r} takes {len(types)} arguments, got {len(args)}")
    w = _Writer()
    for t, v in zip(types, args, strict=True):
        w.put(t, v)
    return bytes(w.buf)


def unmarshal(sig: str, data: bytes, offset: int = 0) -> list[Any]:
    r = _Reader(data, offset)
    return [r.get(t) for t in split_signature(sig)]


def socket_path(address: str) -> str:
    """The socket of the first unix entry of a bus address, an abstract one
    with its leading NUL. Raises Unreachable for anything else."""
    for entry in address.split(";"):
        kind, _, params = entry.partition(":")
        if kind != "unix":
            continue
        keys = dict(p.split("=", 1) for p in params.split(",") if "=" in p)
        if "path" in keys:
            return unquote(keys["path"])
        if "abstract" in keys:
            return "\0" + unquote(keys["abstract"])
    raise Unreachable(f"no unix socket in the bus address {address!r}")


class Connection:
    """One authenticated connection to a message bus. Calls run one at a
    time (a lock, since the server runs tools on threads), each waiting for
    its own reply. A call that times out or breaks closes the connection,
    so a late reply can never be taken for the next call's."""

    def __init__(self, address: str, timeout: float = 5.0):
        self.closed = False
        self._buf = bytearray()
        self._serial = 0
        self._lock = threading.Lock()
        path = socket_path(address)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        try:
            self._sock.connect(path)
            self._authenticate()
            self.unique_name = self.call(_BUS, "/org/freedesktop/DBus", _BUS, "Hello",
                                         timeout=timeout)[0]
        except (OSError, DBusError) as exc:
            self.close()
            raise Unreachable(f"cannot connect to the bus at {address!r}: {exc}") from None

    def _authenticate(self) -> None:
        uid = str(os.getuid()).encode().hex().encode()
        self._sock.sendall(b"\0AUTH EXTERNAL " + uid + b"\r\n")
        line = self._line()
        if not line.startswith(b"OK "):
            raise DBusError(f"the bus refused authentication ({line.decode(errors='replace')})")
        self._sock.sendall(b"BEGIN\r\n")

    def _line(self) -> bytes:
        while b"\r\n" not in self._buf:
            if len(self._buf) > 16384:
                raise DBusError("no end to the bus's authentication reply")
            chunk = self._sock.recv(4096)
            if not chunk:
                raise DBusError("the bus closed the connection")
            self._buf += chunk
        line, _, rest = bytes(self._buf).partition(b"\r\n")
        self._buf = bytearray(rest)
        return line

    def close(self) -> None:
        self.closed = True
        with contextlib.suppress(OSError):
            self._sock.close()

    def call(self, dest: str, path: str, iface: str, member: str, sig: str = "",
             args: list[Any] | tuple[Any, ...] = (), timeout: float = 10.0) -> list[Any]:
        """The reply's arguments as a list. Raises CallError for an error
        reply (the connection stays usable), DBusError when no reply came
        (the connection is closed)."""
        body = marshal(sig, args)
        with self._lock:
            if self.closed:
                raise DBusError("the connection is closed")
            self._serial += 1
            serial = self._serial
            fields: list[tuple[int, tuple[str, str]]] = [
                (_PATH, ("o", path)), (_INTERFACE, ("s", iface)), (_MEMBER, ("s", member)),
                (_DESTINATION, ("s", dest))]
            if sig:
                fields.append((_SIGNATURE, ("g", sig)))
            head = _Writer(b"l" + bytes([_METHOD_CALL, 0, 1])
                           + struct.pack("<II", len(body), serial))
            head.put("a(yv)", fields)
            head._pad(8)
            try:
                self._sock.settimeout(timeout)
                self._sock.sendall(bytes(head.buf) + body)
                return self._reply(serial, time.monotonic() + timeout)
            except (OSError, DBusError) as exc:
                if isinstance(exc, CallError):
                    raise
                self.close()
                if isinstance(exc, TimeoutError):
                    raise DBusError(f"no answer to {member} within {timeout:g} s (the app "
                                    "may still act on it)") from None
                raise DBusError(f"{member}: {exc}") from None

    def _reply(self, serial: int, deadline: float) -> list[Any]:
        while True:
            msg, body_at = self._message(deadline)
            kind = msg[1]
            fields = dict(_Reader(msg, 12).get("a(yv)"))
            if kind not in (_METHOD_RETURN, _ERROR) or fields.get(_REPLY_SERIAL) != serial:
                continue  # a signal (NameAcquired), or nothing of ours
            values = unmarshal(fields.get(_SIGNATURE, ""), msg, body_at)
            if kind == _ERROR:
                text = values[0] if values and isinstance(values[0], str) else ""
                raise CallError(str(fields.get(_ERROR_NAME, "error")), text)
            return values

    def _message(self, deadline: float) -> tuple[bytes, int]:
        while True:
            if len(self._buf) >= 16:
                if self._buf[0:1] != b"l":
                    raise DBusError("a big-endian message, which this client does not read")
                body_len, _, fields_len = struct.unpack_from("<III", self._buf, 4)
                body_at = 16 + fields_len + (-(16 + fields_len) % 8)
                total = body_at + body_len
                if total > _MAX_MESSAGE:
                    raise DBusError("a message larger than D-Bus allows")
                if len(self._buf) >= total:
                    msg = bytes(self._buf[:total])
                    del self._buf[:total]
                    return msg, body_at
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            self._sock.settimeout(left)
            chunk = self._sock.recv(65536)
            if not chunk:
                raise DBusError("the bus closed the connection")
            self._buf += chunk


_connections: dict[str, Connection] = {}
_connections_lock = threading.Lock()


def connection(address: str) -> Connection:
    """The process's connection to this bus, opened on first use and again
    after one breaks. Raises Unreachable."""
    with _connections_lock:
        conn = _connections.get(address)
        if conn is None or conn.closed:
            conn = _connections[address] = Connection(address)
        return conn
