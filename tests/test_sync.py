import unittest

from hueme.audio import AudioEngine
from hueme.engine import sync_test_pixels


class TestSyncTest(unittest.TestCase):
    def test_flash_on_kick_and_beat_fallback(self):
        idle = sync_test_pixels(3, {})
        self.assertEqual(len(idle), 3)
        self.assertLess(idle[0][0], 16)

        kick = sync_test_pixels(2, {"env": {"kick": 0.9}})
        self.assertGreater(kick[0][0], 200)
        self.assertEqual(kick[0], kick[1])

        beat = sync_test_pixels(1, {"beat": {"on_beat": True}, "conf": {"bpm": 0.8}})
        self.assertGreater(beat[0][0], 100)

        weak = sync_test_pixels(1, {"beat": {"on_beat": True}, "conf": {"bpm": 0.1}})
        self.assertLess(weak[0][0], 16)

        waiting = sync_test_pixels(1, {"alive": False}, now=0.0)
        self.assertGreater(waiting[0][0], 60)

    def test_set_latency_clamps_and_applies_live(self):
        eng = AudioEngine({"latency_ms": 0})
        self.assertEqual(eng.set_latency(75), 75.0)
        self.assertEqual(eng.cfg["latency_ms"], 75.0)
        self.assertEqual(eng.set_latency(5000), 2000.0)
        self.assertEqual(eng.set_latency(-5000), -500.0)


if __name__ == "__main__":
    unittest.main()
