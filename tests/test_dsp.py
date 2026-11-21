import math
import unittest

import numpy as np

from hueme.audio import AudioProcessor, SyntheticSource


def click_track(bpm, secs=8.0, rate=48000):
    """Synthetic s16-style click/kick track with off-beat hats."""
    t = np.arange(int(secs * rate)) / rate
    period = 60.0 / bpm
    ph = np.mod(t, period) / period
    sig = 0.7 * np.exp(-ph * 30.0) * np.sin(2 * np.pi * (90 + 60 * np.exp(-ph * 30)) * ph * period)
    ph8 = np.mod(t, period * 0.5) / (period * 0.5)
    sig += 0.05 * np.exp(-ph8 * 35.0) * np.sin(2 * np.pi * 6000 * t)
    return sig.astype(np.float32)


class TestAudioDsp(unittest.TestCase):
    def test_synthetic_128bpm_kick_snare_and_bpm(self):
        rate = 48000
        block = 512
        src = SyntheticSource(rate=rate, block=block, bpm=128.0, realtime=False)
        dsp = AudioProcessor(rate=rate, window_size=1024, sensitivity=1.3)

        kicks = 0
        snares = 0
        # Simulate 8 seconds of audio (~750 blocks)
        n_blocks = int((8.0 * rate) / block)
        last_feat = None
        for _ in range(n_blocks):
            samples = src.read()
            last_feat = dsp.process_block(samples)
            if last_feat["kick"]:
                kicks += 1
            if last_feat["snare_hit"]:
                snares += 1

        self.assertGreaterEqual(kicks, 12)
        self.assertGreaterEqual(snares, 5)
        self.assertTrue(115 <= last_feat["bpm"] <= 140, f"Expected ~128 BPM, got {last_feat['bpm']}")

    def test_agc_normalizes_quiet_vs_loud_and_gates_silence(self):
        rate = 48000
        block = 512
        src_q = SyntheticSource(rate=rate, block=block, bpm=128.0, realtime=False)
        src_l = SyntheticSource(rate=rate, block=block, bpm=128.0, realtime=False)
        dsp_q = AudioProcessor(rate=rate, window_size=1024, agc_cfg={"enabled": True, "target_rms": 0.08, "tau_s": 1.0, "silence_db": -55})
        dsp_l = AudioProcessor(rate=rate, window_size=1024, agc_cfg={"enabled": True, "target_rms": 0.08, "tau_s": 1.0, "silence_db": -55})

        for _ in range(300):
            s_q = src_q.read() * 0.12
            s_l = np.clip(src_l.read() * 1.5, -1.0, 1.0)
            fq = dsp_q.process_block(s_q)
            fl = dsp_l.process_block(s_l)

        self.assertGreater(fq["agc_gain"], fl["agc_gain"])

        # Feed dead silence -> levels decay toward 0 without boosting noise
        zeros = np.zeros(block, dtype=np.float32)
        for _ in range(150):
            fs = dsp_q.process_block(zeros)
        self.assertLess(fs["bass"], 0.02)
        self.assertEqual(fs["agc_gain"], 1.0)

    def test_agc_freezes_below_quantization_noise_floor(self):
        rate = 48000
        block = 512
        t = np.arange(int(2.0 * rate)) / rate
        quiet = (0.008 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
        dsp = AudioProcessor(
            rate=rate, window_size=1024,
            agc_cfg={"enabled": True, "target_rms": 0.08, "tau_s": 1.0, "silence_db": -45.0},
        )
        for i in range(0, len(quiet) - block, block):
            dsp.process_block(quiet[i:i + block])
        # 0.008 RMS is below the 0.012 noise-floor clamp: hold gain instead of
        # ramping toward the 2.5x ceiling.
        self.assertLessEqual(dsp.agc_gain, 1.05)

    # -- Phase A acceptance: rhythm stage --------------------------------
    def _run_click(self, bpm, secs=8.0):
        rate = 48000
        block = 512
        src = click_track(bpm, secs, rate)
        dsp = AudioProcessor(rate=rate, window_size=1024)
        last = None
        errs = []
        snares = 0
        t = 0.0
        period = 60.0 / bpm
        for i in range(0, len(src) - block, block):
            last = dsp.process_block(src[i:i + block])
            snares += int(last["snare_hit"])
            t = (i + block) / rate
            if t > 5.0 and last["conf"]["bpm"] > 0.5:
                expected = (t % period) / period
                errs.append(abs(((last["beat"]["phase"] - expected + 0.5) % 1.0) - 0.5))
        return last, t, period, errs, snares

    def test_click_tracks_lock_bpm_and_phase(self):
        for bpm in (90, 120, 140):
            with self.subTest(bpm=bpm):
                last, t, period, errs, snares = self._run_click(bpm)
                self.assertLessEqual(abs(last["bpm"] - bpm), 2, f"{bpm}: bpm={last['bpm']}")
                self.assertGreater(last["conf"]["bpm"], 0.5, f"{bpm}: confidence={last['conf']}")
                self.assertLessEqual(snares, 1, f"{bpm}: false snare triggers={snares}")
                expected = (t % period) / period
                err = abs(((last["beat"]["phase"] - expected + 0.5) % 1.0) - 0.5)
                self.assertLess(err, 0.08, f"{bpm}: phase error {err:.3f}")
                self.assertLess(np.median(errs), 0.08, f"{bpm}: median phase error")

    def test_white_noise_has_no_tempo_lock(self):
        rng = np.random.default_rng(7)
        rate = 48000
        block = 512
        for amp in (0.05, 0.15, 0.3):
            with self.subTest(amp=amp):
                dsp = AudioProcessor(rate=rate, window_size=1024)
                noise = rng.normal(0.0, amp, (8 * rate,)).astype(np.float32)
                last = None
                for i in range(0, len(noise) - block, block):
                    last = dsp.process_block(noise[i:i + block])
                self.assertLess(last["conf"]["bpm"], 0.2, f"amp {amp}: {last['conf']}")

    def test_snapshot_v3_perception_fields(self):
        rate = 48000
        block = 512
        src = SyntheticSource(rate=rate, block=block, bpm=128.0, realtime=False)
        dsp = AudioProcessor(rate=rate, window_size=1024)
        last = None
        for _ in range(int(8.0 * rate / block)):
            last = dsp.process_block(src.read())
        for key in ("hat_hit", "groove", "mood", "harmony", "section"):
            self.assertIn(key, last)
        for key in ("density", "syncopation", "swing", "punch", "backbeat"):
            self.assertIn(key, last["groove"])
        for key in ("energy", "brightness", "warmth", "complexity", "harmonic"):
            self.assertIn(key, last["mood"])
        self.assertEqual(len(last["harmony"]["chroma"]), 12)
        self.assertTrue(0 <= last["harmony"]["key"] < 12)
        for key in ("bar", "bar_pos", "phrase", "next_in"):
            self.assertIn(key, last["beat"])
        self.assertIn(last["section"]["name"], ("idle", "groove", "build", "drop"))

    def test_envelopes_slew_limit_and_silence_release(self):
        rate = 48000
        block = 512
        src = click_track(120, 4.0, rate)
        dsp = AudioProcessor(rate=rate, window_size=1024)
        prev = None
        prev_accent = False
        max_delta = 0.0
        for i in range(0, len(src) - block, block):
            feat = dsp.process_block(src[i:i + block])
            accent = feat["kick"] or feat["snare_hit"]
            if prev is not None and not (accent or prev_accent):
                for k in ("pulse", "kick", "snare", "bright", "energy"):
                    max_delta = max(max_delta, abs(feat["env"][k] - prev["env"][k]))
            prev = feat
            prev_accent = accent
        self.assertLessEqual(max_delta, 0.35, f"envelope jumped by {max_delta:.3f}")

        # Silence: level must glide down, never cut vertically.
        bass_before = prev["bass"]
        max_step = 0.0
        last = prev
        zeros = np.zeros(block, dtype=np.float32)
        for _ in range(int(1.0 * rate / block)):
            prev = last
            last = dsp.process_block(zeros)
            max_step = max(max_step, abs(last["bass"] - prev["bass"]))
        self.assertGreater(bass_before, 0.0)
        self.assertLessEqual(max_step, 0.35, f"silence cut {max_step:.3f}")
        self.assertLessEqual(last["bass"], max(0.05, bass_before * 0.2))
        self.assertGreaterEqual(last["bass"], 0.0)


if __name__ == "__main__":
    unittest.main()
