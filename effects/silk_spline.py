import math

META = {
    "id": "silk_spline",
    "name": "Silk Spline (Continuous Room Gradient)",
    "description": "Architectural fluid light field: spots act as a smooth spatial spline where bass shifts gravity and kicks send soft pressure waves.",
}

PARAMS = [
    {"key": "color", "label": "Primary Core Color", "type": "color", "default": [255, 75, 20]},
    {"key": "color2", "label": "Secondary Flow Color", "type": "color", "default": [0, 180, 255]},
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "flow_speed", "label": "Spline Drift Speed", "type": "range", "min": 0.2, "max": 2.0, "step": 0.1, "default": 0.8},
    {"key": "fluidity", "label": "Inertia / Smoothing", "type": "range", "min": 0.3, "max": 1.0, "step": 0.05, "default": 0.75},
]


def setup(state):
    state["spline_pos"] = 0.5
    state["smooth_vals"] = []


def render(ctx):
    n = max(1, int(ctx["n"]))
    dt = max(0.001, min(0.1, float(ctx["dt"])))
    env = ctx["env"]
    beat = ctx["beat"]
    p = ctx["params"]
    st = ctx["state"]
    u = ctx["utils"]

    master = float(p.get("brightness", 1.0))
    speed = float(p.get("flow_speed", 0.8))
    fluidity = float(p.get("fluidity", 0.75))

    c1 = p.get("color", [255, 75, 20])
    c2 = p.get("color2", [0, 180, 255])

    pulse = float(env.get("pulse", 0.0))
    kick = float(env.get("kick", 0.0))
    bar_phase = float(beat.get("bar_phase", 0.0))

    # Spline position drifts smoothly with bar phase and bass energy.
    target_pos = 0.5 + 0.35 * math.sin(2.0 * math.pi * bar_phase * speed)
    pos = float(st.get("spline_pos", 0.5))
    pos += (target_pos - pos) * min(1.0, dt * (2.0 + 3.0 * fluidity))
    st["spline_pos"] = pos

    smooth_vals = st.get("smooth_vals") or []
    if len(smooth_vals) != n:
        smooth_vals = [0.03] * n

    coords = u.axis_positions(ctx.get("positions", []))

    px = []
    for i in range(n):
        x = coords[i] if i < len(coords) else i / max(1, n - 1)

        # Distance from current fluid center of gravity.
        d = abs(x - pos)
        # Spatial bell curve (spline weight).
        weight = math.exp(-((d / 0.38) ** 2))

        # Kick adds a localized pressure bulge instead of a harsh strobe.
        kick_bulge = kick * math.exp(-(((x - 0.5) / 0.25) ** 2)) * 0.45

        # Target intensity for this spot.
        target_v = 0.04 + (0.32 * pulse + 0.64 * weight + kick_bulge)
        target_v = min(1.0, max(0.03, target_v))

        # Heavy physical inertia (silky smooth transitions, zero jitter).
        v_prev = smooth_vals[i]
        v = v_prev + (target_v - v_prev) * min(1.0, dt * (4.0 + 6.0 * fluidity))
        smooth_vals[i] = v

        # Color shifts smoothly across the spline length.
        col = u.lerp(c1, c2, x)
        px.append(u.scale(col, v * master))

    st["smooth_vals"] = smooth_vals
    return px
