import os
import tempfile
import unittest

from hueme.config import default_config, load_config, migrate_v1_to_v2, save_config, validate_config


class TestConfig(unittest.TestCase):
    def test_v1_to_v2_migration(self):
        v1 = {
            "mqtt": {"host": "10.0.0.5", "port": 1883, "username": "u", "password": "p", "base_topic": "z2m"},
            "pulse_device": "MySink.monitor",
            "radar_global": {"enabled": True, "host": "10.0.0.99"},
            "groups": [
                {
                    "id": "g1",
                    "name": "Wohnzimmer",
                    "enabled": True,
                    "proxy": "SpotA",
                    "proxy_addr": "0x1234",
                    "fps": 25.0,
                    "max_brightness": 1800,
                    "brightness_mode": "luminance",
                    "ddp_enabled": True,
                    "ddp_port": 4048,
                    "e131_enabled": True,
                    "e131_universe": 2,
                    "lamps": [
                        {"name": "SpotA", "addr": "0x1234"},
                        {"name": "SpotB", "addr": "0x5678"},
                    ],
                    "wled_state": {
                        "on": True,
                        "bri": 128,
                        "effect": "chase",
                        "palette": "fire_ice",
                        "color": [10, 20, 30],
                        "color2": [40, 50, 60],
                    },
                }
            ],
        }
        v2 = migrate_v1_to_v2(v1)
        self.assertEqual(v2["version"], 2)
        self.assertEqual(v2["mqtt"]["host"], "10.0.0.5")
        self.assertEqual(v2["audio"]["device"], "MySink.monitor")
        self.assertIn("_legacy_radar", v2)
        self.assertEqual(v2["hue"]["max_brightness"], 1800)
        self.assertEqual(v2["hue"]["brightness_mode"], "luminance")
        self.assertEqual(v2["hue"]["proxies"], ["SpotA"])

        g = v2["groups"][0]
        self.assertEqual(len(g["fixtures"]), 2)
        self.assertEqual(g["fixtures"][0]["x"], 0.0)
        self.assertEqual(g["fixtures"][1]["x"], 1.0)
        self.assertEqual(g["effect"]["id"], "diagnostic_chase")
        self.assertTrue(g["effect"]["enabled"])

    def test_validation_catches_port_collision_and_bad_addr(self):
        cfg = default_config()
        g2 = dict(cfg["groups"][0], id="group_2", name="Zone 2")
        cfg["groups"].append(g2)
        ok, errs = validate_config(cfg)
        self.assertFalse(ok)
        self.assertTrue(any("collision" in e for e in errs))

        cfg2 = default_config()
        cfg2["groups"][0]["fixtures"][0]["addr"] = "not_hex"
        ok2, errs2 = validate_config(cfg2)
        self.assertFalse(ok2)
        self.assertTrue(any("address" in e for e in errs2))

    def test_atomic_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "gateway.json")
            cfg = default_config()
            cfg["web"]["port"] = 9091
            save_config(cfg, p)
            loaded = load_config(p, legacy_path=None)
            self.assertEqual(loaded["web"]["port"], 9091)


if __name__ == "__main__":
    unittest.main()
