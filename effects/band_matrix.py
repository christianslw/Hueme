META = {
    "id": "band_matrix",
    "name": "Spectral EQ Matrix",
    "description": "Bass, snare, mids and highs spread smoothly across spatial X with a curated gradient.",
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
            ["sunset", "Sunset"],
            ["ember", "Ember"],
            ["acid", "Acid Green"],
            ["moonlight", "Moonlight"],
        ],
    },
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "bass_boost", "label": "Bass Gain", "type": "range", "min": 0.5, "max": 2.5, "step": 0.1, "default": 1.3},
]


def _band_at(x, bands):
    stops = (0.0, 0.33, 0.66, 1.0)
    for i in range(3):
        if x <= stops[i + 1]:
            t = (x - stops[i]) / max(1e-6, stops[i + 1] - stops[i])
            return bands[i] * (1.0 - t) + bands[i + 1] * t
    return bands[-1]


def render(ctx):
    n = ctx["n"]
    af = ctx["features"]
    beat = ctx["beat"]
    env = ctx["env"]
    p = ctx["params"]
    pos_list = ctx.get("world_positions") or ctx["positions"]
    scene = ctx.get("scene") or {}
    harmony = ctx.get("harmony") or {}
    lo = scene.get("min") or [0.0, 0.0, 0.0]
    hi = scene.get("max") or [1.0, 1.0, 1.0]
    u = ctx["utils"]
    key = int(harmony.get("key", 0)) % 12

    scale = float(p.get("brightness", 1.0))
    boost = float(p.get("bass_boost", 1.3))
    pal = p.get("palette", "ice")
    axis = u.scene_axis(scene)
    span = max(1e-6, float(hi[axis]) - float(lo[axis]))
    lo_a = float(lo[axis])

    bands = (
        min(1.0, float(env.get("pulse", af.get("bass", 0.0))) * boost),
        float(af.get("snare", 0.0)),
        float(af.get("mids", 0.0)),
        float(env.get("bright", af.get("highs", 0.0))),
    )
    ph = float(beat.get("bar_phase", 0.0)) + float(env.get("energy", 0.0)) * 0.15

    px = []
    for i in range(n):
        item = pos_list[i] if i < len(pos_list) else None
        coord = float(item[axis]) if item is not None and axis < len(item) else 0.5
        xn = max(0.0, min(1.0, (coord - lo_a) / span))
        level = _band_at(xn, bands)
        col = u.gradient(pal, key / 12.0 + ph + xn * 0.55)
        px.append(u.scale(col, (0.9 * level) * scale))
    return px
