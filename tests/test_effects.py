import math
import os
import shutil
import tempfile
import unittest

from hueme.effects import EffectManager, validate_effect_source
from hueme.engine import grade_pixel, smooth_pixels


class TestEffectsAndSandbox(unittest.TestCase):
    def test_grade_pixel_preserves_primary_colors(self):
        self.assertEqual(grade_pixel((255, 0, 0)), (255, 0, 0))
        blue = grade_pixel((0, 0, 255))
        self.assertGreater(blue[2], 240)
        self.assertLess(blue[0], 10)
        self.assertEqual(grade_pixel((0, 0, 0)), (0, 0, 0))
        # Deep-black S-curve: dim values are darkened (not lifted!) for contrast
        dim = grade_pixel((64, 0, 0))
        self.assertLess(dim[0], 64)
        self.assertGreater(dim[0], 10)

    def test_output_smoother_is_fast_up_and_slow_down(self):
        black = [(0, 0, 0)]
        bright = [(200, 200, 200)]
        a = smooth_pixels(black, bright, 1 / 30.0)
        b = smooth_pixels(a, bright, 1 / 30.0)
        self.assertGreater(a[0][0], 100)
        self.assertGreaterEqual(b[0][0], 155)
        c = smooth_pixels(b, black, 1 / 30.0)
        self.assertGreater(c[0][0], 100)
        self.assertLess(c[0][0], b[0][0])
        self.assertEqual(smooth_pixels(black, [(0, 0, 0)], 1 / 30.0), [(0, 0, 0)])

    def test_all_bundled_effects_render_valid_rgb(self):
        mgr = EffectManager(effects_dir="effects", poll_s=10.0, timeout_ms=100)
        mgr.start()
        try:
            effects = mgr.list_effects()
            self.assertGreaterEqual(len(effects), 11)
            for e in effects:
                self.assertIsNone(e["error"], f"Effect {e['id']} had error: {e['error']}")

            features = {
                "bass": 0.7, "snare": 0.5, "mids": 0.4, "highs": 0.6,
                "kick": True, "snare_hit": False, "kick_env": 0.9, "snare_env": 0.6,
                "beat_count": 4, "bpm": 128,
            }
            for n in (2, 5, 10):
                reqs = [
                    {
                        "group_id": f"g_{e['id']}",
                        "id": e["id"],
                        "n": n,
                        "now": 10.5,
                        "dt": 0.033,
                        "features": features,
                        "params": mgr.default_params_for(e["id"]),
                        "state": {},
                        "positions": [(i / max(1, n - 1), 0.5) for i in range(n)],
                    }
                    for e in effects
                ]
                res = mgr.render_batch(reqs)
                for e in effects:
                    r = res[f"g_{e['id']}"]
                    self.assertIsNone(r["error"])
                    px = r["pixels"]
                    self.assertEqual(len(px), n)
                    for rgb in px:
                        self.assertEqual(len(rgb), 3)
                        for c in rgb:
                            self.assertTrue(0 <= c <= 255)
        finally:
            mgr.stop()

    def test_anti_flicker_guardrail_on_new_grammar_effects(self):
        """§7 QA rule: no fixture may jump >0.35 RGB outside kick/snare accent frames."""
        effects = [
            "scene_pulse", "prism", "spectrum_sweep", "shockwave", "rainbow", "band_matrix",
            "club_tech", "ambient_uplight", "breathe", "ping_pong", "bass_cannon",
            "dim2warm", "solid", "strobe_drop", "ma3_busker",
        ]
        mgr = EffectManager(effects_dir="effects", poll_s=10.0, timeout_ms=250)
        mgr.start()
        try:
            n = 8
            positions = [(i / (n - 1), 0.5, 0.3 + 0.4 * (i % 3) / 2) for i in range(n)]
            roles = ["edge" if abs(p[0] - 0.5) > 0.24 else "center" for p in positions]
            state = {}
            prev = {}
            kick_levels = {e: 0.0 for e in effects}
            recent_kick = {e: 0 for e in effects}
            recent_snare = {e: 0 for e in effects}
            violations = []
            for frame in range(90):
                t = frame / 30.0
                beat_period = 0.5
                kick_now = (frame % 15) == 0
                snare_now = frame % 30 == 7
                pulse = 0.45 + 0.18 * math.sin(2 * math.pi * t / 2.0)
                snare = 0.25 + 0.08 * math.sin(2 * math.pi * t / 4.0)
                bright = 0.3 + 0.1 * math.sin(2 * math.pi * t / 3.0)
                snare_phase = ((frame - 7) % 30) / 30.0
                snare_accent = 0.75 * math.exp(-snare_phase * (30.0 / 0.18))
                for e in effects:
                    if kick_now:
                        kick_levels[e] = 0.95
                        recent_kick[e] = 3
                    else:
                        kick_levels[e] *= math.exp(-(1 / 30.0) / 0.26)
                    if snare_now:
                        recent_snare[e] = 3
                env = {"pulse": pulse, "kick": 0.0, "snare": snare_accent, "bright": bright, "energy": 0.5}
                beat = {
                    "phase": (t % beat_period) / beat_period,
                    "bar_phase": ((t % (beat_period * 4)) / (beat_period * 4)),
                    "count": int(t / beat_period),
                    "on_beat": (t % beat_period) < 0.035,
                    "on_downbeat": int(t / beat_period) % 4 == 0 and kick_now,
                }
                features = {
                    "bass": pulse, "snare": snare, "mids": 0.4, "highs": bright,
                    "kick": kick_now, "snare_hit": snare_now,
                    "kick_env": kick_levels[effects[0]], "snare_env": snare_accent, "beat_count": beat["count"],
                    "bpm": 120, "env": env, "beat": beat,
                    "aligned_kick": kick_now, "aligned_snare": snare_now,
                    "conf": {"bpm": 0.9, "music": 0.9, "grid": 0.8},
                }
                reqs = []
                for e in effects:
                    req_features = dict(features)
                    req_features["env"] = dict(env, kick=kick_levels[e])
                    req_features["kick_env"] = kick_levels[e]
                    reqs.append({
                        "group_id": f"g_{e}", "id": e, "n": n, "now": t, "dt": 1 / 30.0,
                        "features": req_features, "params": mgr.default_params_for(e),
                        "state": state.setdefault(e, {}), "positions": positions, "roles": roles,
                    })
                res = mgr.render_batch(reqs)
                for e in effects:
                    r = res[f"g_{e}"]
                    px = r.get("pixels")
                    if px is None:
                        prev.pop(e, None)
                        continue
                    if e in prev:
                        safe = recent_kick[e] == 0 and recent_snare[e] == 0
                        for i in range(n):
                            d = max(abs(px[i][c] - prev[e][i][c]) for c in range(3)) / 255.0
                            if safe and d > 0.35:
                                violations.append((e, frame, i, round(d, 3)))
                    prev[e] = px
                    recent_kick[e] = max(0, recent_kick[e] - 1)
                    recent_snare[e] = max(0, recent_snare[e] - 1)
            self.assertEqual(violations[:5], [], f"Flicker guardrail violations: {violations[:5]}")
        finally:
            mgr.stop()

    def test_syntax_validation_and_sandbox_hang_recovery(self):
        bad = validate_effect_source("META = {'name': 'x'}\ndef render(ctx)\n    return []", "bad")
        self.assertFalse(bad["ok"])
        self.assertIsNotNone(bad["line"])

        with tempfile.TemporaryDirectory() as tmp:
            for fname in os.listdir("effects"):
                if fname.endswith(".py"):
                    shutil.copyfile(os.path.join("effects", fname), os.path.join(tmp, fname))

            mgr = EffectManager(effects_dir=tmp, poll_s=10.0, timeout_ms=60)
            mgr.start()
            try:
                hang_src = (
                    "META = {'id': 'hang', 'name': 'Hang'}\n"
                    "PARAMS = []\n"
                    "def render(ctx):\n"
                    "    while True:\n"
                    "        pass\n"
                )
                saved = mgr.save_effect("hang", hang_src)
                self.assertTrue(saved["ok"])

                res = mgr.render_batch([{
                    "group_id": "g1", "id": "hang", "n": 4, "now": 1.0, "dt": 0.03,
                    "features": {}, "params": {}, "state": {}, "positions": [],
                }])
                self.assertIsNotNone(res["g1"]["error"])
                self.assertGreaterEqual(mgr.restarts, 1)

                # Verify sandbox immediately recovers and renders normal effects on next tick
                res_ok = mgr.render_batch([{
                    "group_id": "g1", "id": "solid", "n": 4, "now": 1.1, "dt": 0.03,
                    "features": {}, "params": {"color": [100, 150, 200], "brightness": 1.0},
                    "state": {}, "positions": [],
                }])
                self.assertIsNone(res_ok["g1"]["error"])
                self.assertEqual(len(res_ok["g1"]["pixels"]), 4)
            finally:
                mgr.stop()


if __name__ == "__main__":
    unittest.main()
