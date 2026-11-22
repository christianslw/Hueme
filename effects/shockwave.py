import math

META = {
    "id": "shockwave",
    "name": "Shockwave (Beat Origin)",
    "description": "Kick launches a wave from a rotating fixture origin; radius and amplitude follow real wave physics.",
}

PARAMS = [
    {
        "key": "palette",
        "label": "Palette",
        "type": "select",
        "default": "ember",
        "options": [
            ["ember", "Ember"],
            ["synth", "Synth (teal / magenta)"],
            ["ice", "Ice"],
            ["sunset", "Sunset"],
            ["acid", "Acid Green"],
            ["moonlight", "Moonlight"],
        ],
    },
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "speed", "label": "Wave Speed", "type": "range", "min": 0.4, "max": 3.0, "step": 0.1, "default": 1.3},
    {"key": "width", "label": "Front Width", "type": "range", "min": 0.06, "max": 0.4, "step": 0.02, "default": 0.16},
]


def setup(state):
    state["r"] = 2.0
    state["amp"] = 0.0
    state["ox"] = 0.5
    state["oy"] = 0.5


def render(ctx):
    n = ctx["n"]
    dt = ctx["dt"]
    pos = ctx.get("world_positions") or ctx["positions"]
    scene = ctx.get("scene") or {}
    lo = scene.get("min") or [0.0, 0.0, 0.0]
    hi = scene.get("max") or [1.0, 1.0, 1.0]
    center = scene.get("center") or [0.5, 0.5, 0.5]
    env = ctx["env"]
    beat = ctx["beat"]
    harmony = ctx.get("harmony") or {}
    section = ctx.get("section") or {}
    p = ctx["params"]
    st = ctx["state"]
    u = ctx["utils"]
    key = int(harmony.get("key", 0)) % 12

    scale = float(p.get("brightness", 1.0))
    width_base = float(p.get("width", 0.16))
    pal = p.get("palette", "ember")

    kick = float(env.get("kick", 0.0))
    pulse = float(env.get("pulse", 0.0))
    count = int(beat.get("count", 0))
    af = ctx["features"]
    conf = af.get("conf") or {}
    kick_hit = bool(af.get("kick")) and (bool(af.get("aligned_kick", True)) or float(conf.get("bpm", 0.0)) < 0.25)

    x0, x1 = float(lo[0]), float(hi[0])
    y0, y1 = float(lo[1]), float(hi[1])
    diag = max(0.8, math.hypot(x1 - x0, y1 - y0, float(hi[2]) - float(lo[2])))
    width = max(0.08, width_base * max(1.0, diag * 0.5))

    # All zones compute the same origin for a given beat, so one wave crosses
    # the whole scene instead of one wave per zone.
    spots = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (float(center[0]), float(center[1]))]
    ox, oy = spots[count % len(spots)]

    # A fresh kick starts a new wave; the origin rotates through the scene.
    if kick_hit:
        st["ox"] = ox
        st["oy"] = oy
        st["r"] = 0.0
        st["amp"] = max(0.6, kick) * (1.35 if section.get("drop") else 1.0)

    r_now = float(st.get("r", diag)) + dt * max(0.4, float(p.get("speed", 1.3))) * max(1.0, diag * 0.5) * (0.7 + pulse)
    amp = float(st.get("amp", 0.0)) * math.exp(-dt / 0.45)
    st["r"] = r_now
    st["amp"] = amp
    ox = float(st.get("ox", float(center[0])))
    oy = float(st.get("oy", float(center[1])))

    ph = key / 12.0 + float(beat.get("bar_phase", 0.0)) + float(env.get("energy", 0.0)) * 0.1
    px = []
    for i in range(n):
        x, y = (float(pos[i][0]), float(pos[i][1])) if i < len(pos) else (0.5, 0.5)
        d = math.hypot(x - ox, y - oy)
        front = math.exp(-((d - r_now) ** 2) / (2.0 * width ** 2))
        v = min(1.0, 0.5 * pulse + 0.9 * amp * front)
        col = u.gradient(pal, ph + 0.6 * (d / diag))
        px.append(u.scale(col, v * scale))
    return px
