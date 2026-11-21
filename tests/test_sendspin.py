import time
import unittest
from types import SimpleNamespace

import numpy as np

from hueme.audio import AudioEngine, LOOKAHEAD_S
from hueme.sendspin_source import SendspinAudioSource, _AVAILABLE

try:
    from aiosendspin.models.types import AudioCodec
except Exception:
    AudioCodec = None


class TestSendspinTrackMetadata(unittest.TestCase):
    def test_item_id_and_playing_state_follow_metadata(self):
        src = SendspinAudioSource(rate=48000, channels=2, block=512)
        progress = SimpleNamespace(
            track_progress=12_000,
            playback_speed=1_000,
            track_duration=236_250,
        )
        meta = SimpleNamespace(
            title="Dance Loft 128",
            artist="Demo Artist",
            item_id="ma-item-1",
            progress=progress,
        )
        src._on_metadata(SimpleNamespace(metadata=meta))
        self.assertEqual(src.track_item_id, "ma-item-1")
        self.assertEqual(src.track_key, "Demo Artist|Dance Loft 128")
        self.assertTrue(src.track_playing)
        self.assertAlmostEqual(src.track_progress_s, 12.0, places=1)

        progress.playback_speed = 0
        src._on_metadata(SimpleNamespace(metadata=meta))
        self.assertFalse(src.track_playing)


@unittest.skipUnless(_AVAILABLE and AudioCodec is not None, "aiosendspin not installed")
class TestSendspinSource(unittest.TestCase):
    def test_pcm_chunks_are_buffered_as_mono_blocks(self):
        src = SendspinAudioSource(rate=48000, channels=2, block=512)
        pcm = SimpleNamespace(channels=2, sample_rate=48000, bit_depth=16)
        fmt = SimpleNamespace(codec=AudioCodec.PCM, pcm_format=pcm)
        frames = np.zeros((512, 2), dtype=np.int16)
        frames[:, 0] = 1200
        frames[:, 1] = -1200
        src._on_audio(0, frames.tobytes(), fmt, 0)
        out = src.read()
        self.assertEqual(len(out), 512)
        self.assertLess(abs(float(np.mean(out))), 1e-4)

    def test_non_pcm_is_ignored(self):
        src = SendspinAudioSource(rate=48000, channels=2, block=512)
        fmt = SimpleNamespace(codec="flac", pcm_format=SimpleNamespace(channels=2, sample_rate=48000, bit_depth=16))
        src._on_audio(0, b"\x00" * 4096, fmt, 0)
        self.assertEqual(len(src._buf), 0)

    def test_block_play_time_tracks_partial_chunks(self):
        src = SendspinAudioSource(rate=48000, channels=2, block=256)
        src._client = SimpleNamespace(
            now_us=lambda: 5_000_000,
            compute_play_time=lambda ts: 6_000_000 + ts,
        )
        fmt = SimpleNamespace(
            codec=AudioCodec.PCM,
            pcm_format=SimpleNamespace(channels=2, sample_rate=48000, bit_depth=16),
        )
        frames = np.zeros((512, 2), dtype=np.int16)
        src._on_audio(1_000_000, frames.tobytes(), fmt, 12_000)

        self.assertAlmostEqual(src.last_send_ahead_s, 0.012)
        self.assertAlmostEqual(src.play_lead_s, 2.0, places=2)
        src.read()
        t0 = src.last_play_time
        src.read()
        self.assertAlmostEqual(src.last_play_time - t0, 256.0 / 48000.0, places=5)


class TestAudioLookahead(unittest.TestCase):
    def test_features_follow_play_time_and_do_not_leak_future_audio(self):
        eng = AudioEngine({"source": "synthetic", "rate": 48000, "block": 512})
        eng.source = SimpleNamespace(connected=True, latency=0.25, last_error=None)
        now = time.monotonic()
        eng.last_update = now

        def feat(tag, kick=False):
            return {
                "alive": True,
                "bpm": 120,
                "beat": {"phase": 0.0, "bar_phase": 0.0, "count": 0},
                "kick": kick,
                "snare_hit": False,
                "aligned_kick": kick,
                "aligned_snare": False,
                "env": {},
                "tag": tag,
            }

        eng._latest = feat("latest")
        eng._history.clear()
        eng._history.append((now - 0.1, now - 0.1, feat("past", True)))
        eng._history.append((now + 0.02, now - 0.005, feat("near", True)))
        eng._history.append((now + 0.3, now, feat("future", True)))

        out = eng.get_features(consume_triggers=True)
        self.assertEqual(out["tag"], "near")
        self.assertTrue(out["kick"])  # hit delivered ~40 ms before it plays
        self.assertEqual(out["lookahead_ms"], int(LOOKAHEAD_S * 1000))
        self.assertAlmostEqual(out["play_lead_ms"], 20.0, delta=15.0)
        self.assertEqual(eng._history[0][2]["tag"], "future")


if __name__ == "__main__":
    unittest.main()
