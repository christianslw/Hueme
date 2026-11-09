META = {
    "id": "rainbow",
    "name": "Palette Drift",
    "description": "Curated cosine-gradient wash that drifts across fixture space, locked to the bar.",
}

PARAMS = [
    {
        "key": "palette",
        "label": "Palette",
        "type": "select",
        "default": "synth",
        "options": [
            ["synth", "Synth (teal / magenta)"],
            ["sunset", "Sunset"],
            ["ember", "Ember"],
            ["moonlight", "Moonlight"],
            ["acid", "Acid Green"],
            ["ice", "Ice"],
        ],
    },
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "speed", "label": "Drift Speed", "type": "range", "min": 0.05, "max": 1.5, "step": 0.05, "default": 0.25},
]


def render(ctx):
    n = ctx["n"]
    pos = ctx.get("world_positions") or ctx["positions"]
    scene = ctx.get("scene") or {}
    lo = scene.get("min") or [0.0, 0.0, 0.0]
    hi = scene.get("max") or [1.0, 1.0, 1.0]
    beat = ctx["beat"]
    env = ctx["env"]
    harmony = ctx.get("harmony") or {}
    p = ctx["params"]
    u = ctx["utils"]

    scale = float(p.get("brightness", 1.0))
    speed = float(p.get("speed", 0.25))
    pal = p.get("palette", "synth")
    key = int(harmony.get("key", 0)) % 12
    axis = u.scene_axis(scene)
    span = max(1e-6, float(hi[axis]) - float(lo[axis]))
    lo_a = float(lo[axis])
    ph = key / 12.0 + float(beat.get("bar_phase", 0.0)) * 0.25 + float(env.get("energy", 0.0)) * 0.15
    v = 0.02 + 0.55 * float(env.get("pulse", 0.0)) + 0.15 * float(env.get("energy", 0.0))
    px = []
    for i in range(n):
        item = pos[i] if i < len(pos) else None
        coord = float(item[axis]) if item is not None and axis < len(item) else 0.5
        xn = max(0.0, min(1.0, (coord - lo_a) / span))
        px.append(u.scale(u.gradient(pal, ph + speed * xn + xn * 0.35), v * scale))
    return px
