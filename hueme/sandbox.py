"""Isolated child process for executing effect scripts with restricted builtins and memory limits."""

import colorsys
import math
import random
import traceback

PALETTES = {
    "cyberpunk": [(255, 0, 85), (0, 240, 255), (180, 0, 255)],
    "fire_ice": [(255, 40, 0), (255, 160, 0), (0, 180, 255)],
    "toxic": [(0, 255, 60), (200, 255, 0), (140, 0, 255)],
    "sunset": [(255, 20, 60), (255, 110, 0), (255, 210, 80)],
    "rave": [(255, 0, 0), (0, 255, 0), (0, 80, 255)],
    "warm_gold": [(255, 90, 0), (255, 180, 60), (255, 240, 180)],
}

# Cosine gradients in Íñigo Quílez form: a + b·cos(2π(c·t + d)).
# Curated so shipped presets never fall back to a naked HSV rainbow.
COSINE_PALETTES = {
    "ember": ((0.55, 0.18, 0.05), (0.40, 0.25, 0.05), (1.0, 1.0, 1.0), (0.00, 0.10, 0.15)),
    "synth": ((0.50, 0.12, 0.50), (0.50, 0.10, 0.50), (1.0, 1.0, 1.0), (0.00, 0.25, 0.50)),
    "sunset": ((0.60, 0.20, 0.12), (0.35, 0.18, 0.10), (1.0, 1.0, 1.0), (0.00, 0.05, 0.10)),
    "moonlight": ((0.30, 0.40, 0.55), (0.25, 0.25, 0.30), (1.0, 1.0, 1.0), (0.10, 0.20, 0.30)),
    "acid": ((0.35, 0.50, 0.15), (0.30, 0.40, 0.15), (1.0, 1.0, 1.0), (0.00, 0.10, 0.20)),
    "ice": ((0.35, 0.55, 0.70), (0.20, 0.25, 0.25), (1.0, 1.0, 1.0), (0.00, 0.10, 0.20)),
}

# Old effect configs still name legacy palettes; map them onto the curated
# cosine gradients instead of falling back to a grey/rainbow mix.
GRADIENT_ALIASES = {
    "cyberpunk": "synth",
    "fire_ice": "ember",
    "rave": "synth",
    "sunset": "sunset",
    "warm_gold": "ember",
    "toxic": "acid",
    "rainbow": "synth",
}

DEFAULT_CUE = {
    "available": False,
    "section": "intro",
    "section_progress": 0.0,
    "beats_to_drop": 0.0,
    "beats_in_section": 0.0,
    "harmonic_swell": 0.0,
    "percussive_punch": 0.0,
    "prominence": 0.0,
}

ALLOWED_IMPORTS = {
    "math": math,
    "random": random,
    "colorsys": colorsys,
}


def _cue_context(features):
    cue = dict(DEFAULT_CUE)
    raw = features.get("cue")
    if isinstance(raw, dict):
        cue.update(raw)
    return cue


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name in ALLOWED_IMPORTS:
        return ALLOWED_IMPORTS[name]
    raise ImportError(f"Import of '{name}' is not allowed in effect sandbox")


SAFE_BUILTINS = {
    "__import__": _safe_import,
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "pow": pow,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    "True": True,
    "False": False,
    "None": None,
}


class EffectUtils:
    """Math and color helpers exposed to every effect as ctx['utils']."""

    palettes = PALETTES
    gradients = COSINE_PALETTES

    @staticmethod
    def clamp(v, lo=0.0, hi=1.0):
        return max(lo, min(hi, v))

    @staticmethod
    def smoothstep(edge0, edge1, x):
        if edge0 == edge1:
            return 0.0 if x < edge0 else 1.0
        t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
        return t * t * (3.0 - 2.0 * t)

    @staticmethod
    def lerp(c1, c2, t):
        t = max(0.0, min(1.0, float(t)))
        return (
            int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t),
        )

    @staticmethod
    def scale(c, factor):
        f = max(0.0, min(1.0, float(factor)))
        return (int(c[0] * f), int(c[1] * f), int(c[2] * f))

    @staticmethod
    def add(c1, c2):
        return (
            min(255, max(0, int(c1[0] + c2[0]))),
            min(255, max(0, int(c1[1] + c2[1]))),
            min(255, max(0, int(c1[2] + c2[2]))),
        )

    @staticmethod
    def hsv(h, s=1.0, v=1.0):
        r, g, b = colorsys.hsv_to_rgb(float(h) % 1.0, max(0.0, min(1.0, float(s))), max(0.0, min(1.0, float(v))))
        return (int(r * 255), int(g * 255), int(b * 255))

    @staticmethod
    def palette(name):
        return PALETTES.get(name, PALETTES["cyberpunk"])

    @staticmethod
    def gradient(name, t):
        """Sample a curated cosine gradient (ember, synth, sunset, moonlight, acid, ice)."""
        key = str(name)
        if key not in COSINE_PALETTES:
            key = GRADIENT_ALIASES.get(key, "synth")
        a, b, c, d = COSINE_PALETTES[key]
        tt = float(t)
        rgb = []
        for i in range(3):
            v = a[i] + b[i] * math.cos(2.0 * math.pi * (c[i] * tt + d[i]))
            rgb.append(int(max(0.0, min(1.0, v)) * 255))
        return tuple(rgb)

    @staticmethod
    def quantize(value, grid=16):
        """Snap a 0..1 value to a musical grid (16 = sixteenth notes)."""
        g = max(1, int(grid))
        return round(float(value) * g) / g

    @staticmethod
    def axis_positions(positions):
        """Normalize fixtures along their widest spatial axis (0..1).

        Legacy 1D effects assumed the zone ran left-to-right on X. Real rooms
        often run along Y (or a diagonal), so pick whichever axis actually
        spreads the fixtures before mapping into the effect.
        """
        if not positions:
            return []
        dims = min(len(p) for p in positions)
        best, best_spread = 0, -1.0
        for d in range(max(1, dims)):
            vals = [float(p[d]) for p in positions]
            spread = max(vals) - min(vals)
            if spread > best_spread:
                best, best_spread = d, spread
        vals = [float(p[best]) for p in positions]
        lo, hi = min(vals), max(vals)
        span = hi - lo
        if span < 1e-6:
            return [0.5] * len(positions)
        return [(v - lo) / span for v in vals]

    @staticmethod
    def scene_axis(scene):
        """Index of the world axis with the largest scene extent (0=X, 1=Y, 2=Z)."""
        try:
            lo = scene.get("min") or [0.0, 0.0, 0.0]
            hi = scene.get("max") or [1.0, 1.0, 1.0]
            spans = [float(hi[i]) - float(lo[i]) for i in range(3)]
        except (AttributeError, TypeError, IndexError):
            return 0
        return max(range(3), key=lambda i: spans[i])

    @staticmethod
    def cct_to_rgb(kelvin, brightness=1.0):
        """Approximate 1800K..6500K blackbody locus for Dim-to-Warm lighting."""
        k = max(1500.0, min(6500.0, float(kelvin))) / 100.0
        r = 255.0
        g = max(0.0, min(255.0, 99.4708 * math.log(max(1.0, k)) - 161.1195))
        if k <= 19.0:
            b = 0.0
        else:
            b = max(0.0, min(255.0, 138.5177 * math.log(max(1.0, k - 10.0)) - 305.0448))
        s = max(0.0, min(1.0, float(brightness)))
        return (int(r * s), int(g * s), int(b * s))


UTILS = EffectUtils()


def _apply_memory_limit():
    # ponytail: Linux RLIMIT_AS caps memory leaks at 256MB; on Windows timeout+respawn is the ceiling
    try:
        import resource
        limit = 256 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except Exception:
        pass


def _compile_effect(effect_id: str, source: str):
    code = compile(source, f"<effect:{effect_id}>", "exec")
    ns = {
        "__builtins__": SAFE_BUILTINS,
        "math": math,
        "random": random,
        "colorsys": colorsys,
        "utils": UTILS,
        "PALETTES": PALETTES,
    }
    exec(code, ns, ns)
    if "render" not in ns or not callable(ns["render"]):
        raise ValueError(f"Effect '{effect_id}' must define callable render(ctx)")
    return ns


def _sanitize_pixels(raw_px, n):
    out = []
    if isinstance(raw_px, (list, tuple)):
        for i in range(min(n, len(raw_px))):
            item = raw_px[i]
            if isinstance(item, (list, tuple)) and len(item) >= 3:
                out.append((
                    max(0, min(255, int(item[0]))),
                    max(0, min(255, int(item[1]))),
                    max(0, min(255, int(item[2]))),
                ))
            else:
                out.append((0, 0, 0))
    while len(out) < n:
        out.append((0, 0, 0))
    return out


def sandbox_worker(conn):
    """Entrypoint for the isolated effect sandbox subprocess."""
    _apply_memory_limit()
    loaded = {}  # effect_id -> namespace dict

    while True:
        try:
            if not conn.poll(1.0):
                continue
            msg = conn.recv()
        except (EOFError, OSError):
            break

        if not isinstance(msg, dict):
            continue

        op = msg.get("op")
        if op == "stop":
            for ns in loaded.values():
                td = ns.get("teardown")
                if callable(td):
                    try:
                        td({})
                    except Exception:
                        pass
            break

        elif op == "load":
            errors = {}
            for item in msg.get("effects", []):
                eid = item["id"]
                src = item["source"]
                try:
                    ns = _compile_effect(eid, src)
                    old_ns = loaded.get(eid)
                    if old_ns and callable(old_ns.get("teardown")):
                        try:
                            old_ns["teardown"]({})
                        except Exception:
                            pass
                    loaded[eid] = ns
                except Exception as e:
                    errors[eid] = f"{type(e).__name__}: {e}"
            conn.send({"op": "loaded", "errors": errors})

        elif op == "render":
            frame_id = msg.get("frame", 0)
            results = []
            for req in msg.get("requests", []):
                eid = req.get("id")
                n = max(1, int(req.get("n", 1)))
                state = req.get("state") if isinstance(req.get("state"), dict) else {}
                ns = loaded.get(eid)
                if not ns:
                    results.append({
                        "group_id": req.get("group_id"),
                        "id": eid,
                        "pixels": [(0, 0, 0)] * n,
                        "state": state,
                        "error": f"Effect '{eid}' not loaded",
                    })
                    continue

                try:
                    if not state.get("_initialized"):
                        setup_fn = ns.get("setup")
                        if callable(setup_fn):
                            setup_fn(state)
                        state["_initialized"] = True

                    features = req.get("features") or {}
                    positions = req.get("positions") or [(i / max(1, n - 1), 0.5, 0.5) for i in range(n)]
                    ctx = {
                        "n": n,
                        "now": float(req.get("now", 0.0)),
                        "dt": float(req.get("dt", 0.033)),
                        "features": features,
                        "params": req.get("params") or {},
                        "state": state,
                        "positions": positions,
                        "world_positions": req.get("world_positions") or positions,
                        "roles": req.get("roles") or ["center"] * n,
                        "scene": req.get("scene") or {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0], "center": [0.5, 0.5, 0.5]},
                        "env": features.get("env") or {},
                        "beat": features.get("beat") or {"phase": 0.0, "bar_phase": 0.0, "count": 0, "on_beat": False, "on_downbeat": False},
                        "groove": features.get("groove") or {},
                        "mood": features.get("mood") or {},
                        "harmony": features.get("harmony") or {"key": 0, "key_conf": 0.0, "chroma": [0.0] * 12},
                        "section": features.get("section") or {"name": "idle", "build": 0.0, "drop": False},
                        "cue": _cue_context(features),
                        "utils": UTILS,
                    }
                    raw_px = ns["render"](ctx)
                    px = _sanitize_pixels(raw_px, n)
                    results.append({
                        "group_id": req.get("group_id"),
                        "id": eid,
                        "pixels": px,
                        "state": state,
                        "error": None,
                    })
                except Exception as e:
                    tb = traceback.format_exc(limit=3)
                    results.append({
                        "group_id": req.get("group_id"),
                        "id": eid,
                        "pixels": [(0, 0, 0)] * n,
                        "state": state,
                        "error": f"{type(e).__name__}: {e}\n{tb}",
                    })
            conn.send({"op": "rendered", "frame": frame_id, "results": results})
