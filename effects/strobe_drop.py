import math

META = {
    "id": "strobe_drop",
    "name": "Drop Wash (Soft)",
    "description": "Smooth beat-locked wash that swells with kick/snare accents; no strobing or random jumps.",
}

PARAMS = [
    {"key": "color", "label": "Kick Wash Color", "type": "color", "default": [255, 0, 85]},
    {"key": "color2", "label": "Snare Wash Color", "type": "color", "default": [0, 240, 255]},
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
]


def setup(state):
    state["pos"] = 0.0


def render(ctx):
    n = ctx["n"]
    dt = ctx["dt"]
    pos = ctx["positions"]
    env = ctx["env"]
    beat = ctx["beat"]
    p = ctx["params"]
    st = ctx["state"]
    u = ctx["utils"]

    scale = float(p.get("brightness", 1.0))
    c_kick = p.get("color", [255, 0, 85])
    c_snare = p.get("color2", [0, 240, 255])

    pulse = float(env.get("pulse", 0.0))
    kick = float(env.get("kick", 0.0))
    snare = float(env.get("snare", 0.0))
    bright = float(env.get("bright", 0.0))

    coords = u.axis_positions(pos)
    pos_now = (float(st.get("pos", 0.0)) + dt * (0.15 + 0.35 * pulse)) % 1.0
    st["pos"] = pos_now

    px = []
    for i in range(n):
        x = coords[i] if i < len(coords) else i / max(1, n - 1)
        d = abs(x - pos_now)
        d = min(d, 1.0 - d)
        glow = math.exp(-((d / 0.28) ** 2))
        color = u.lerp(c_kick, c_snare, min(1.0, x * 0.8 + snare * 0.2))
        v = 0.35 * pulse + 0.45 * kick * glow + 0.28 * snare * glow + 0.10 * bright * glow
        px.append(u.scale(color, min(1.0, v) * scale))
    return px
