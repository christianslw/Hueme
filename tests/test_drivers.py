import socket
import unittest

from hueme.drivers import (
    DdpOutputDriver,
    E131OutputDriver,
    WledOutputDriver,
    frame_payload,
    MODE_DEVICE,
    parse_ddp,
    parse_e131,
    parse_wled_udp,
    record,
    smoothing_for_fps,
)


class TestDrivers(unittest.TestCase):
    def test_hue_wire_format_builders(self):
        rec = record(0x8B3C, 2047, 0.3127, 0.3290)
        self.assertEqual(len(rec), 7)
        self.assertEqual(rec[0], 0x3C)
        self.assertEqual(rec[1], 0x8B)
        off = record(0x8B3C, 0, 0.3127, 0.3290)
        self.assertEqual(off[2] & 0x1F, MODE_DEVICE)
        self.assertEqual((off[2] >> 5) | (off[3] << 3), 0)
        fp = frame_payload(42, smoothing_for_fps(25.0), [rec, rec])
        self.assertEqual(len(fp), 4 + 2 + 14)

    def test_ddp_loopback(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        port = rx.getsockname()[1]
        rx.settimeout(1.0)
        drv = DdpOutputDriver({"enabled": True, "host": "127.0.0.1", "port": port})
        drv.start()
        try:
            drv.send_pixels([(255, 0, 85), (0, 240, 255), (10, 20, 30)])
            data, _ = rx.recvfrom(2048)
            parsed = parse_ddp(data)
            self.assertIsNotNone(parsed)
            off, payload = parsed
            self.assertEqual(off, 0)
            self.assertEqual(list(payload), [255, 0, 85, 0, 240, 255, 10, 20, 30])
            self.assertTrue(len(drv.stats["last_hex"]) > 0)
        finally:
            drv.stop()
            rx.close()

    def test_e131_loopback(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        port = rx.getsockname()[1]
        rx.settimeout(1.0)
        drv = E131OutputDriver({"enabled": True, "unicast": "127.0.0.1", "universe_start": 3})
        drv.start()
        try:
            drv.send_pixels([(12, 34, 56), (78, 90, 123)], universe_start=3, unicast_host="127.0.0.1", port=port)
            data, _ = rx.recvfrom(2048)
            parsed = parse_e131(data)
            self.assertIsNotNone(parsed)
            uni, dmx = parsed
            self.assertEqual(uni, 3)
            self.assertEqual(list(dmx[:6]), [12, 34, 56, 78, 90, 123])
        finally:
            drv.stop()
            rx.close()

    def test_wled_loopback(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        port = rx.getsockname()[1]
        rx.settimeout(1.0)
        drv = WledOutputDriver({"enabled": True, "host": "127.0.0.1", "port": port, "mode": "drgb"})
        drv.start()
        try:
            drv.send_pixels([(200, 100, 50), (10, 20, 30)])
            data, _ = rx.recvfrom(2048)
            res = parse_wled_udp(data)
            self.assertEqual(res[0], "drgb")
            self.assertEqual(list(res[1]), [200, 100, 50, 10, 20, 30])
        finally:
            drv.stop()
            rx.close()


if __name__ == "__main__":
    unittest.main()
