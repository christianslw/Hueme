from types import SimpleNamespace
import os
import tempfile
import threading
import unittest

import numpy as np

from hueme.audio import AudioEngine
from hueme.show_analyzer import ShowAnalyzer


def sectioned_track(rate=48000, bpm=128.0, duration=14.0):
    """Pad intro -> groove -> build -> 1.5-beat vacuum -> drop."""
    t = np.arange(int(duration * rate)) / rate
    period = 60.0 / bpm
    phase = np.mod(t, period) / period
    kick = 0.8 * np.exp(-phase * 20.0) * np.sin(
        2.0 * np.pi * (55.0 + 55.0 * np.exp(-phase * 30.0)) * phase * period
    )
    hat = 0.08 * np.exp(-np.mod(t, period * 0.5) / (period * 0.5) * 30.0) * np.sin(2.0 * np.pi * 7000.0 * t)
    pad = 0.12 * np.sin(2.0 * np.pi * 220.0 * t) + 0.08 * np.sin(2.0 * np.pi * 330.0 * t)
    y = pad * 0.5
    y += ((t >= 3.0) & (t < 7.0)) * (kick + hat)
    build = np.clip((t - 7.0) / 3.0, 0.0, 1.0)
    y += ((t >= 7.0) & (t < 10.0)) * (kick * (0.4 + 0.6 * build) + hat * (0.5 + 0.5 * build))
    y += (t >= 10.9) * (kick * 0.9 + hat + 0.1 * np.sin(2.0 * np.pi * 110.0 * t))
    return np.clip(y.astype(np.float32), -1.0, 1.0)


class TestShowAnalyzer(unittest.TestCase):
    def test_beats_structure_pre_drop_and_cache(self):
        rate = 48000
        y = sectioned_track(rate)
        with tempfile.TemporaryDirectory() as tmp:
            analyzer = ShowAnalyzer(cache_dir=tmp)
            track = analyzer.analyze_audio(y, rate, track_key="test/drop-track", force=True)

            self.assertTrue(118.0 <= track.tempo <= 138.0, track.tempo)
            self.assertGreater(len(track.beats), 8)
            self.assertGreater(len(track.downbeats), 2)
            self.assertTrue(track.drops, "expected a drop event")
            self.assertTrue(track.drop_windows)
            self.assertTrue(track.pre_drops)
            bar_seconds = 4.0 * 60.0 / track.tempo
            min_section_s = 16.0 * bar_seconds
            if track.duration >= min_section_s:
                for sec in track.sections:
                    self.assertGreaterEqual(sec["end"] - sec["start"] + 1e-6, min_section_s)
            else:
                # Short test clip: everything collapses into one stable macro section.
                self.assertEqual(len(track.sections), 1)

            cache_path = analyzer.cache_path("test/drop-track")
            self.assertTrue(os.path.exists(cache_path))
            loaded = analyzer.load_cached("test/drop-track")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.tempo, track.tempo)
            self.assertEqual(len(loaded.sections), len(track.sections))

            for t in (1.0, 4.0, 9.0, 10.2, 10.95, 13.0):
                cue = track.cue_at(t)
                self.assertTrue(cue["available"])
                self.assertIn(cue["section"], ("intro", "verse", "breakdown", "build", "pre_drop", "drop", "outro"))
                self.assertTrue(0.0 <= cue["section_progress"] <= 1.0)
                self.assertTrue(0.0 <= cue["harmonic_swell"] <= 1.0)
                self.assertTrue(0.0 <= cue["percussive_punch"] <= 1.0)
                self.assertTrue(0.0 <= cue["prominence"] <= 1.0)

            drop_cue = track.cue_at(track.drops[0] + 0.2)
            self.assertEqual(drop_cue["section"], "drop")
            self.assertEqual(drop_cue["beats_to_drop"], 0.0)
            vac = track.pre_drops[0]
            self.assertEqual(track.cue_at((vac["start"] + vac["end"]) / 2.0)["section"], "pre_drop")

    def test_stream_capture_compiles_trackmap_in_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            class FakeShow:
                def __init__(self):
                    self.done = threading.Event()
                    self.key = None
                    self.samples = 0

                def cache_path(self, key):
                    return os.path.join(tmp, "missing.json")

                def load_cached(self, key):
                    return None

                def analyze_audio(self, samples, rate, track_key=None, force=False):
                    self.key = track_key
                    self.samples = len(samples)
                    self.done.set()
                    return SimpleNamespace(tempo=120.0, sections=[], drops=[])

            eng = AudioEngine({"source": "synthetic", "rate": 48000, "block": 512})
            fake = FakeShow()
            eng.show = fake
            eng.source = SimpleNamespace(track_key="Artist|Title", track_progress_s=0.0, track_duration_s=10.0)
            block = np.zeros(512, dtype=np.float32)
            for i in range(int(9.5 * 48000 / 512)):
                eng.source.track_progress_s = i * 512 / 48000.0
                eng._update_show_capture(block)
            self.assertTrue(fake.done.wait(2.0), "background TrackMap compile did not start")
            self.assertEqual(fake.key, "Artist|Title")
            self.assertGreaterEqual(fake.samples, int(8.5 * 48000))


if __name__ == "__main__":
    unittest.main()
