"""Encrypted Modbus TCP client for a Sungrow WiNet/RAK dongle. Reads FC3/FC4; writes only the feed-in limit."""
import socket
import struct
import threading

from Cryptodome.Cipher import AES

PRIV = b"Grow#0*2Sun68CbE"
UNIT = 1
REG_NOMINAL, REG_SWITCH, REG_LIMIT, REG_RATIO, REG_AC = 5000, 5009, 5010, 5014, 5030
SWITCH_ON = 0xAA


def _rx(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("dongle closed the connection")
        buf += chunk
    return buf


class Dongle:
    """The dongle serves one connection at a time, so every call is serialised."""

    def __init__(self, host, port=502, timeout=15):
        self.host, self.port, self.timeout = host, port, timeout
        self._aes = None
        self._lock = threading.Lock()

    def _connect(self):
        return socket.create_connection((self.host, self.port), timeout=self.timeout)

    def _fetch_key(self):
        sock = self._connect()
        try:
            sock.sendall(bytes.fromhex("686800000006f7040ae70008"))
            reply = _rx(sock, 25)
        finally:
            sock.close()
        return AES.new(bytes(a ^ b for a, b in zip(reply[9:], PRIV)), AES.MODE_ECB)

    def _call(self, fc, addr, arg):
        if self._aes is None:
            self._aes = self._fetch_key()
        req = struct.pack(">HHHBBHH", 0x6868, 0, 6, UNIT, fc, addr, arg)
        pad = 16 - len(req) % 16
        enc = self._aes.encrypt(b"\x68\x68" + req[2:] + b"\xff" * pad)
        sock = self._connect()
        try:
            sock.sendall(bytes([1, 0, len(req), pad]) + enc)
            head = _rx(sock, 4)
            body = self._aes.decrypt(_rx(sock, head[2] + head[3]))[: head[2]]
        finally:
            sock.close()
        if body[7] & 0x80:
            raise RuntimeError(f"modbus exception {body[8]} (fc{fc} @{addr})")
        return body

    def _read(self, fc, addr, count):
        body = self._call(fc, addr, count)
        return [int.from_bytes(body[9 + i : 11 + i], "big") for i in range(0, body[8], 2)]

    def _guard(self, fn):
        with self._lock:
            try:
                return fn()
            except Exception:
                self._aes = None  # key rotates daily; refetch on the next call
                raise

    def snapshot(self):
        def run():
            nominal = self._read(4, REG_NOMINAL, 1)[0] / 10  # kW
            regs = self._read(3, REG_SWITCH - 1, 7)  # 5008..5014
            ac = self._read(4, REG_AC, 2)
            return {"nominal_kw": nominal, "switch_on": regs[1] == SWITCH_ON, "limit_kw": regs[2] / 100,
                    "ratio_pct": regs[6] / 10, "ac_w": ac[0] + (ac[1] << 16)}
        return self._guard(run)

    def write_limit(self, kw, max_kw):
        centi = round(kw * 100)
        if not 0 <= centi <= round(max_kw * 100):
            raise ValueError("limit outside 0..nominal")
        self._guard(lambda: self._call(6, REG_LIMIT, centi))
