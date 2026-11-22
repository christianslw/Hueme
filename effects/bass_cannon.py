import math

META = {
    "id": "bass_cannon",
    "name": "Bass Cannon (Shockwave + Peak Hold)",
    "description": "Smooth centre-out kick wavefront with a decaying peak-hold ring.",
}

PARAMS = [
    {"key": "color", "label": "Core Color", "type": "color", "default": [255, 0, 85]},
    {"key": "color2", "label": "Wave Edge Color", "type": "color", "default": [0, 240, 255]},
    {"key": "peak_color", "label": "Peak Hold Color", "type": "color", "default": [255, 230, 80]},
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "bass_boost", "label": "Impact Gain", "type": "range", "min": 0.5, "max": 2.5, "step": 0.1, "default": 1.3},
    {"key": "peak_decay", "label": "Peak Hold Decay", "type": "range", "min": 0.2, "max": 3.0, "step": 0.1, "default": 0.9},
]


def setup(state):
    state["peak"] = 0.0


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
    c_peak = p.get("peak_color", [255, 230, 80])
    boost = float(p.get("bass_boost", 1.3))
    decay = float(p.get("peak_decay", 0.9))

    pulse = float(env.get("pulse", 0.0))
    kick = min(1.0, float(env.get("kick", 0.0)) * boost)
    snare = float(env.get("snare", 0.0))
    bright = float(env.get("bright", 0.0))
    energy = float(env.get("energy", 0.0))

    # Peak hold moves smoothly instead of snapping between fixtures.
    peak = max(float(st.get("peak", 0.0)) - dt * decay * 0.5, kick)
    st["peak"] = peak

    ph = float(beat.get("bar_phase", 0.0)) + energy * 0.1
    coords = u.axis_positions(pos)
    px = []
    for i in range(n):
        d = coords[i] if i < len(coords) else i / max(1, n - 1)
        d = min(1.0, abs(d - 0.5) * 2.0)
        # Travelling front + central body, both continuous in kick/pulse.
        front = math.exp(-((d - (1.0 - kick) * 1.1) ** 2) / (2.0 * 0.22 ** 2))
        body = math.exp(-(d / 0.55) ** 2)
        ring = math.exp(-((d - (1.0 - peak) * 1.1) ** 2) / (2.0 * 0.10 ** 2))
        color = u.lerp(c_kick, c_snare, d)
        if bright > 0.15:
            color = u.lerp(color, u.gradient("ember", ph + d * 0.4), bright * 0.25)
        v = 0.42 * body * pulse + 0.45 * front * kick
        if ring > 0.05:
            color = u.lerp(color, c_peak, min(1.0, ring * 0.55))
            v = max(v, 0.35 * ring * peak)
        if snare > 0.3:
            v = min(1.0, v + 0.10 * snare * d)
        px.append(u.scale(color, min(1.0, v) * scale))
    return px
