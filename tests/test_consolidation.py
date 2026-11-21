import json
import unittest

from hueme.drivers import CMD_FRAME, CMD_SYNC_STOP, ZCL_SNAP_SMOOTHING, HueZ2MDriver, smoothing_for_fps


class FakeMqttClient:
    def __init__(self):
        self.published = []
        self.subscriptions = []
        self.callbacks = {}

    def message_callback_add(self, topic, cb):
        self.callbacks[topic] = cb

    def subscribe(self, topic):
        self.subscriptions.append(topic)

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, json.loads(payload)))


class TestHueConsolidation(unittest.TestCase):
    def test_multi_group_under_10_consolidates_into_single_frame(self):
        fake = FakeMqttClient()
        drv = HueZ2MDriver(
            {"base_topic": "zigbee2mqtt"},
            {"enabled": True, "proxies": ["proxy_main"], "max_fixtures_per_frame": 10, "light_balance": False},
            mqtt_client=fake,
        )
        drv.start()

        # Group 1 (4 spots) + Group 2 (4 spots) = 8 spots total -> 1 consolidated ZCL frame
        recs = [
            (f"Spot_{i}", 0x1000 + i, (255, 0, 85), "proxy_main")
            for i in range(8)
        ]
        fake.published.clear()
        drv.send_consolidated(recs, fps=30.0)

        frame_msgs = [
            (t, p) for t, p in fake.published
            if p.get("zclcommand", {}).get("command") == CMD_FRAME
        ]
        self.assertEqual(len(frame_msgs), 1)
        topic, msg = frame_msgs[0]
        self.assertEqual(topic, "zigbee2mqtt/proxy_main/set")
        # 4B counter + 2B smoothing + 8 * 7B records = 62 bytes
        self.assertEqual(len(msg["zclcommand"]["payload"]["data"]), 6 + 8 * 7)

    def test_23_fixtures_multiplexes_across_two_proxies(self):
        fake = FakeMqttClient()
        drv = HueZ2MDriver(
            {"base_topic": "zigbee2mqtt"},
            {"enabled": True, "proxies": ["proxy_1", "proxy_2"], "max_fixtures_per_frame": 10, "light_balance": False},
            mqtt_client=fake,
        )
        drv.start()
        recs = [(f"Spot_{i}", 0x2000 + i, (0, 240, 255), "") for i in range(23)]
        drv.setup_session({r[0]: r[1] for r in recs}, do_balance=False)
        fake.published.clear()

        drv.send_consolidated(recs, fps=30.0)
        frame_msgs = [
            (t, p) for t, p in fake.published
            if p.get("zclcommand", {}).get("command") == CMD_FRAME
        ]
        # 23 fixtures -> chunks of 10, 10, 3 sent to proxy_1, proxy_2, proxy_1
        self.assertEqual(len(frame_msgs), 3)
        self.assertEqual(frame_msgs[0][0], "zigbee2mqtt/proxy_1/set")
        self.assertEqual(len(frame_msgs[0][1]["zclcommand"]["payload"]["data"]), 6 + 10 * 7)
        self.assertEqual(frame_msgs[1][0], "zigbee2mqtt/proxy_2/set")
        self.assertEqual(len(frame_msgs[1][1]["zclcommand"]["payload"]["data"]), 6 + 10 * 7)
        self.assertEqual(frame_msgs[2][0], "zigbee2mqtt/proxy_1/set")
        self.assertEqual(len(frame_msgs[2][1]["zclcommand"]["payload"]["data"]), 6 + 3 * 7)

    def test_transient_frames_use_hard_snap_and_decay_uses_fps_ramp(self):
        fake = FakeMqttClient()
        drv = HueZ2MDriver(
            {"base_topic": "zigbee2mqtt"},
            {"enabled": True, "proxies": ["proxy_main"], "max_fixtures_per_frame": 10, "light_balance": False},
            mqtt_client=fake,
        )
        drv.start()
        recs = [(f"Spot_{i}", 0x4000 + i, (255, 120, 0), "") for i in range(4)]
        drv.setup_session({r[0]: r[1] for r in recs}, do_balance=False)

        fake.published.clear()
        drv.send_consolidated(recs, fps=30.0, snap=True)
        frame = [p for _, p in fake.published if p.get("zclcommand", {}).get("command") == CMD_FRAME][0]
        data = frame["zclcommand"]["payload"]["data"]
        self.assertEqual(int.from_bytes(bytes(data[4:6]), "little"), ZCL_SNAP_SMOOTHING)

        fake.published.clear()
        drv.send_consolidated(recs, fps=30.0, snap=False)
        frame = [p for _, p in fake.published if p.get("zclcommand", {}).get("command") == CMD_FRAME][0]
        data = frame["zclcommand"]["payload"]["data"]
        self.assertEqual(int.from_bytes(bytes(data[4:6]), "little"), smoothing_for_fps(30.0))

    def test_single_proxy_over_10_deterministically_interleaves_and_stops_on_idle(self):
        fake = FakeMqttClient()
        drv = HueZ2MDriver(
            {"base_topic": "zigbee2mqtt"},
            {"enabled": True, "proxies": ["only_proxy"], "max_fixtures_per_frame": 10, "light_balance": False},
            mqtt_client=fake,
        )
        drv.start()
        recs = [(f"Spot_{i}", 0x3000 + i, (255, 120, 0), "") for i in range(15)]
        drv.setup_session({r[0]: r[1] for r in recs}, do_balance=False)
        fake.published.clear()

        # Frame 1 -> chunk 0 (10 fixtures)
        drv.send_consolidated(recs, fps=30.0)
        self.assertEqual(len(fake.published), 1)
        self.assertEqual(len(fake.published[0][1]["zclcommand"]["payload"]["data"]), 6 + 10 * 7)

        # Frame 2 -> chunk 1 (5 fixtures)
        fake.published.clear()
        drv.send_consolidated(recs, fps=30.0)
        self.assertEqual(len(fake.published), 1)
        self.assertEqual(len(fake.published[0][1]["zclcommand"]["payload"]["data"]), 6 + 5 * 7)

        # Empty active records -> stops session with CMD_SYNC_STOP
        fake.published.clear()
        drv.send_consolidated([], fps=30.0)
        self.assertFalse(drv.streaming)
        self.assertTrue(any(p.get("zclcommand", {}).get("command") == CMD_SYNC_STOP for _, p in fake.published))


if __name__ == "__main__":
    unittest.main()
