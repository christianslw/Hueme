import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request

from hueme.config import default_config, save_config
from hueme.engine import LightingEngine
from hueme.web import create_http_server


class SmokeTest(unittest.TestCase):
    def test_end_to_end_engine_and_http_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = os.path.join(tmp, "gateway.json")
            cfg = default_config()
            cfg["audio"]["source"] = "synthetic"
            # Use high ephemeral UDP ports so tests never collide
            cfg["groups"][0]["inputs"]["ddp"]["port"] = 54048
            cfg["groups"][0]["inputs"]["raw"]["port"] = 52391
            cfg["groups"][0]["inputs"]["wled"]["port"] = 51324
            cfg["groups"][0]["inputs"]["e131"]["enabled"] = False
            save_config(cfg, cfg_path)

            eng = LightingEngine(config_path=cfg_path)
            eng.start()
            srv = create_http_server(eng, host="127.0.0.1", port=0)
            port = srv.server_address[1]
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()

            try:
                time.sleep(1.3)
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
                    health = json.loads(r.read().decode("utf-8"))
                self.assertTrue(health["ok"])

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
                    st = json.loads(r.read().decode("utf-8"))
                self.assertGreater(st["fps"], 5.0)
                self.assertLess(st["latency_p95_ms"], 35.0)
                self.assertTrue(st["audio"]["alive"])
                self.assertEqual(len(st["groups"][0]["pixels"]), 6)

                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/protocols", timeout=2) as r:
                    proto = json.loads(r.read().decode("utf-8"))
                self.assertIn("drivers", proto)
            finally:
                srv.shutdown()
                srv.server_close()
                eng.stop()


if __name__ == "__main__":
    unittest.main()
