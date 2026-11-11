import importlib.util, os, socketserver, struct, threading, unittest

from Cryptodome.Cipher import AES

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "sungrow_modbus", os.path.join(HERE, "..", "custom_components", "sungrow_export_limit", "sungrow_modbus.py"))
sm = importlib.util.module_from_spec(spec); spec.loader.exec_module(sm)

PUB = bytes(range(16))
KEY = bytes(a ^ b for a, b in zip(PUB, sm.PRIV))
REGS = {4: {5000: [50], 5030: [1234, 0]}, 3: {5008: [0, 0xAA, 500, 0, 0, 0, 1000]}}
WRITES = []


def rx(s, n):
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise ConnectionError
        b += c
    return b


class Fake(socketserver.BaseRequestHandler):
    def handle(self):
        s = self.request
        first = s.recv(12)
        if first == bytes.fromhex("686800000006f7040ae70008"):
            s.sendall(b"\x68\x68\x00\x00\x00\x13\xf7\x04\x10" + PUB)  # 9 header + 16 key = 25
            return
        head = first + rx(s, 4 - len(first)) if len(first) < 4 else first[:4]
        rest = first[4:] if len(first) > 4 else b""
        ln, pad = head[2], head[3]
        enc = rest + rx(s, ln + pad - len(rest))
        aes = AES.new(KEY, AES.MODE_ECB)
        req = aes.decrypt(enc)[:ln]
        fc, addr, arg = req[7], *struct.unpack(">HH", req[8:12])
        if fc == 6:
            WRITES.append((addr, arg))
            body = b"\x68\x68" + req[2:12]
        else:
            vals = REGS[fc][addr][: arg] if addr in REGS[fc] else REGS[fc][5008][addr - 5008 : addr - 5008 + arg]
            data = b"".join(v.to_bytes(2, "big") for v in vals)
            body = b"\x68\x68\x00\x00" + struct.pack(">H", 3 + len(data)) + bytes([req[6], fc, len(data)]) + data
        p = 16 - len(body) % 16
        s.sendall(bytes([1, 0, len(body), p]) + aes.encrypt(body + b"\xff" * p))


class Client(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.dongle = sm.Dongle("127.0.0.1", cls.srv.server_address[1], timeout=5)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_snapshot(self):
        s = self.dongle.snapshot()
        self.assertEqual(s, {"nominal_kw": 5.0, "switch_on": True, "limit_kw": 5.0, "ratio_pct": 100.0,
                             "ac_w": 1234})

    def test_write_limit_encodes_register_5010(self):
        WRITES.clear()
        self.dongle.write_limit(0, 5.0)
        self.dongle.write_limit(2.5, 5.0)
        self.assertEqual(WRITES, [(5010, 0), (5010, 250)])

    def test_write_refuses_out_of_range(self):
        WRITES.clear()
        for bad in (-0.01, 5.01):
            with self.assertRaises(ValueError):
                self.dongle.write_limit(bad, 5.0)
        self.assertEqual(WRITES, [])


if __name__ == "__main__":
    unittest.main()
