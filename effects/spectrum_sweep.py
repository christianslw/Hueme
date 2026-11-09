import math

META = {
    "id": "spectrum_sweep",
    "name": "Spectrum Sweep (Freq -> X)",
    "description": "Frequency bands mapped left-to-right, with a beat-locked sweep travelling across the zone.",
}

PARAMS = [
    {
        "key": "palette",
        "label": "Palette",
        "type": "select",
        "default": "ice",
        "options": [
            ["ice", "Ice"],
            ["synth", "Synth (teal / magenta)"],
            ["ember", "Ember"],
            ["sunset", "Sunset"],
            ["acid", "Acid Green"],
            ["moonlight", "Moonlight"],
        ],
    },
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "sweep", "label": "Sweep Depth", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.55},
]


def _band_at(x, bands):
    """Triangular interpolation across bass -> snare -> mids -> highs."""
    stops = (0.0, 0.33, 0.66, 1.0)
    for i in range(3):
        if x <= stops[i + 1]:
            t = (x - stops[i]) / max(1e-6, stops[i + 1] - stops[i])
            return bands[i] * (1.0 - t) + bands[i + 1] * t
    return bands[-1]


def render(ctx):
    n = ctx["n"]
    pos = ctx.get("world_positions") or ctx["positions"]
    scene = ctx.get("scene") or {}
    lo = scene.get("min") or [0.0, 0.0, 0.0]
    hi = scene.get("max") or [1.0, 1.0, 1.0]
    env = ctx["env"]
    beat = ctx["beat"]
    harmony = ctx.get("harmony") or {}
    p = ctx["params"]
    u = ctx["utils"]

    scale = float(p.get("brightness", 1.0))
    depth = float(p.get("sweep", 0.55))
    pal = p.get("palette", "ice")
    key = int(harmony.get("key", 0)) % 12
    chroma = harmony.get("chroma") or [0.0] * 12

    bands = (
        float(env.get("pulse", 0.0)),
        float(ctx["features"].get("snare", 0.0)),
        float(ctx["features"].get("mids", 0.0)),
        float(env.get("bright", ctx["features"].get("highs", 0.0))),
    )
    pulse = bands[0]
    energy = float(env.get("energy", 0.0))
    phase = float(beat.get("phase", 0.0))
    # Cosine so the sweep turns around at the beat instead of hard-cutting.
    sweep_pos = 0.5 - 0.5 * math.cos(2.0 * math.pi * phase)
    axis = u.scene_axis(scene)
    span = max(1e-6, float(hi[axis]) - float(lo[axis]))
    lo_a = float(lo[axis])

    px = []
    for i in range(n):
        item = pos[i] if i < len(pos) else None
        coord = float(item[axis]) if item is not None and axis < len(item) else 0.5
        xn = max(0.0, min(1.0, (coord - lo_a) / span))
        bin_idx = int(max(0.0, min(11.999, xn * 12.0)))
        chroma_lvl = float(chroma[bin_idx]) if bin_idx < len(chroma) else 0.0
        level = 0.55 * _band_at(xn, bands) + 0.45 * chroma_lvl
        s = math.exp(-((xn - sweep_pos) ** 2) / (2.0 * 0.18 ** 2))
        col = u.gradient(pal, key / 12.0 + xn * 0.55 + float(beat.get("bar_phase", 0.0)) * 0.1)
        v = min(1.0, 0.62 * level + depth * 0.35 * s * min(1.0, pulse * 1.5))
        px.append(u.scale(col, v * scale))
    return px
