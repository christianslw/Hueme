import math

META = {
    "id": "scene_pulse",
    "name": "Scene Pulse (Breath + Kick Wavefront)",
    "description": "Whole-scene breathing with a kick shockwave that travels through 3D fixture space.",
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
    {"key": "wave", "label": "Kick Wavefront", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.55},
    {"key": "sparkle", "label": "Sparkle Density", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.35},
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
    harmony = ctx.get("harmony") or {}
    section = ctx.get("section") or {}
    p = ctx["params"]
    u = ctx["utils"]

    scale = float(p.get("brightness", 1.0))
    wave_gain = float(p.get("wave", 0.55))
    sparkle_density = float(p.get("sparkle", 0.35))
    pal = p.get("palette", "synth")

    pulse = float(env.get("pulse", 0.0))
    kick = float(env.get("kick", 0.0))
    snare = float(env.get("snare", 0.0))
    hat = float(env.get("hat", 0.0))
    energy = float(env.get("energy", 0.0))
    build = float(section.get("build", 0.0))
    punch = float(groove.get("punch", 0.0))
    key = int(harmony.get("key", 0)) % 12
    now = float(ctx["now"])

    cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
    diag = math.hypot(float(hi[0]) - float(lo[0]), float(hi[1]) - float(lo[1]), float(hi[2]) - float(lo[2]))
    diag = max(0.8, diag)
    wave_front = kick * diag * (0.45 + 0.20 * punch)
    width = max(0.10, diag * 0.11)

    # Whole-scene breath + harmonically related palette.
    base = 0.02 + 0.42 * pulse + 0.18 * energy + 0.10 * build
    ph = key / 12.0 + float(beat.get("bar_phase", 0.0)) * 0.15 + energy * 0.10
    px = []
    for i in range(n):
        x, y, z = _p3(pos[i] if i < len(pos) else None)
        r = math.hypot(x - cx, y - cy, (z - cz) * 0.7)
        # Sharp gaussian front travelling outward from the scene centre.
        w = math.exp(-((r - wave_front) ** 2) / (2.0 * width ** 2))
        sparkle = sparkle_density * 0.22 * hat * (0.5 + 0.5 * math.sin(now * 2.4 + i * 1.7))
        col = u.gradient(pal, ph + 0.35 * (r / diag) + 0.08 * energy)
        v = min(1.0, base + wave_gain * (0.5 + 0.15 * punch) * w + sparkle + 0.14 * snare)
        px.append(u.scale(col, v * scale))
    return px
