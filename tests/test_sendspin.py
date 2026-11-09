import unittest
from types import SimpleNamespace

import numpy as np

from hueme.sendspin_source import SendspinAudioSource, _AVAILABLE

try:
    from aiosendspin.models.types import AudioCodec
except Exception:
    AudioCodec = None


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


if __name__ == "__main__":
    unittest.main()
