import math

META = {
    "id": "kinetic_polyrhythm",
    "name": "Kinetic Polyrhythm (Bar Pendulum vs. Beat Lock)",
    "description": "Two interlocking musical time-scales: a rock-steady 1-bar pendulum glides across the ceiling while counter-posed beat markers snap into place on 1-2-3-4.",
}

PARAMS = [
    {"key": "color", "label": "Bar Pendulum Color (1-Bar Glide)", "type": "color", "default": [255, 140, 20]},
    {"key": "color2", "label": "Beat Counter-Lock Color (1/4 Snap)", "type": "color", "default": [0, 195, 255]},
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "beam_width", "label": "Pendulum Focus (Sharp <-> Wide)", "type": "range", "min": 0.08, "max": 0.35, "step": 0.02, "default": 0.16},
]


def render(ctx):
    n = ctx["n"]
    beat = ctx["beat"]
    env = ctx["env"]
    p = ctx["params"]
    u = ctx["utils"]
    pos = ctx.get("world_positions") or ctx["positions"]

    master = float(p.get("brightness", 1.0))
    width = float(p.get("beam_width", 0.16))
    c_pend = p.get("color", [255, 140, 20])
    c_snap = p.get("color2", [0, 195, 255])

    b_count = int(beat.get("count", 0))
    b_phase = float(beat.get("phase", 0.0))
    bar_phase = float(beat.get("bar_phase", 0.0))
    bar_num = b_count // 4
    bar_pos = b_count % 4

    kick = float(env.get("kick", 0.0))
    snare = float(env.get("snare", 0.0))

    coords = u.axis_positions(pos)

    # Layer 1: Continuous 2-bar triangle/cosine pendulum (Even bars: 0 -> 1, Odd bars: 1 -> 0)
    two_bar_phase = (bar_num % 2 + bar_phase) * 0.5
    pendulum_x = 0.5 - 0.5 * math.cos(2.0 * math.pi * two_bar_phase)

    # Layer 2: Discrete quantized counter-anchor that jumps ONLY on 1-2-3-4
    # Mirrors the pendulum on the opposite side of the ceiling
    snap_targets = (0.0, 1.0, 0.33, 0.67)
    snap_x = snap_targets[bar_pos]
    snap_env = max(0.0, 1.0 - b_phase * 1.45) ** 1.5
    snap_strength = max(snap_env, kick * 0.8, snare * 0.9)

    px = []
    for i in range(n):
        x = coords[i] if i < len(coords) else i / max(1, n - 1)

        # Gaussian profile for smooth bar pendulum
        d_pend = abs(x - pendulum_x)
        w_pend = math.exp(-(d_pend * d_pend) / (2.0 * width * width))

        # Tight lock for the discrete beat marker (snaps to nearest physical spot)
        d_snap = abs(x - snap_x)
        w_snap = 1.0 if d_snap <= (0.55 / max(1, n - 1)) else 0.0

        col_p = u.scale(c_pend, w_pend * 0.85)
        col_s = u.scale(c_snap, w_snap * snap_strength)

        # Clean additive blend with negative space everywhere else
        combined = u.add(col_p, col_s)
        px.append(u.scale(combined, master))

    return px
