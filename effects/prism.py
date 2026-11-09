import math

META = {
    "id": "prism",
    "name": "Prism (Harmonic Flow)",
    "description": "Groove-aware harmonic wash: chroma, key, energy and section shape a smooth flowing scene with soft kick/snare/hat accents.",
}

PARAMS = [
    {
        "key": "palette",
        "label": "Palette",
        "type": "select",
        "default": "synth",
        "options": [
            ["synth", "Synth (teal / magenta)"],
            ["ember", "Ember"],
            ["sunset", "Sunset"],
            ["moonlight", "Moonlight"],
            ["acid", "Acid Green"],
            ["ice", "Ice"],
        ],
    },
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "flow", "label": "Flow", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.55},
    {"key": "accents", "label": "Beat Accents", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.6},
]


def _p3(item):
    if item is None:
        return (0.5, 0.5, 0.5)
    vals = list(item) + [0.5, 0.5, 0.5]
    return (float(vals[0]), float(vals[1]), float(vals[2]))


def render(ctx):
    n = ctx["n"]
    pos = ctx.get("world_positions") or ctx["positions"]
    scene = ctx.get("scene") or {}
    center = scene.get("center") or [0.5, 0.5, 0.5]
    lo = scene.get("min") or [0.0, 0.0, 0.0]
    hi = scene.get("max") or [1.0, 1.0, 1.0]
    env = ctx["env"]
    beat = ctx["beat"]
    groove = ctx.get("groove") or {}
    mood = ctx.get("mood") or {}
    harmony = ctx.get("harmony") or {}
    section = ctx.get("section") or {}
    p = ctx["params"]
    u = ctx["utils"]

    scale = float(p.get("brightness", 1.0))
    flow = float(p.get("flow", 0.55))
    accent_gain = float(p.get("accents", 0.6))
    pal = p.get("palette", "synth")

    pulse = float(env.get("pulse", 0.0))
    kick = float(env.get("kick", 0.0))
    snare = float(env.get("snare", 0.0))
    hat = float(env.get("hat", 0.0))
    energy = float(env.get("energy", 0.0))
    build = float(section.get("build", 0.0))
    drop = 1.0 if section.get("drop") else 0.0
    density = float(groove.get("density", 0.0))
    swing = float(groove.get("swing", 0.0))
    punch = float(groove.get("punch", 0.0))
    brightness = float(mood.get("brightness", 0.0))
    key = int(harmony.get("key", 0)) % 12
    chroma = harmony.get("chroma") or [0.0] * 12
    now = float(ctx["now"])

    cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
    diag = math.hypot(float(hi[0]) - float(lo[0]), float(hi[1]) - float(lo[1]), float(hi[2]) - float(lo[2]))
    diag = max(0.8, diag)
    flow_speed = 0.08 + flow * (0.18 + 0.22 * density)
    flow_phase = now * flow_speed + swing * 0.04 + build * 0.1
    wave_front = kick * diag * (0.5 + 0.25 * punch)
    wave_width = max(0.12, diag * (0.13 + 0.05 * drop))

    # A slow harmonic haze plus a beat-locked edge highlight.
    base = 0.03 + 0.38 * pulse + 0.18 * energy + 0.12 * build + 0.10 * drop
    ph = key / 12.0 + float(beat.get("bar_phase", 0.0)) * 0.18 + brightness * 0.10
    px = []
    for i in range(n):
        x, y, z = _p3(pos[i] if i < len(pos) else None)
        r = math.hypot(x - cx, y - cy, (z - cz) * 0.7)
        bin_idx = int(max(0.0, min(11.999, (r / diag) * 12.0)))
        chroma_lvl = float(chroma[bin_idx]) if bin_idx < len(chroma) else 0.0
        # Soft front + a slow positional drift through the palette.
        w = math.exp(-((r - wave_front) ** 2) / (2.0 * wave_width ** 2))
        drift = 0.35 * (r / diag) + flow_phase * 0.6
        sparkle = accent_gain * 0.22 * hat * (0.5 + 0.5 * math.sin(now * 2.6 + i * 1.7))
        col = u.gradient(pal, ph + drift + chroma_lvl * 0.18)
        v = base + accent_gain * (0.38 + 0.20 * punch) * w + sparkle + 0.12 * snare + 0.35 * chroma_lvl
        px.append(u.scale(col, min(1.0, v) * scale))
    return px
