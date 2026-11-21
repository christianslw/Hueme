import math

META = {
    "id": "celestial_orrery",
    "name": "Celestial Orrery (3D Orbital Bodies)",
    "description": "Two luminous celestial bodies orbit across the 3D room on independent ellipses, casting inverse-square light and flaring when they eclipse.",
}

PARAMS = [
    {"key": "sun_color", "label": "Primary Body (Sun)", "type": "color", "default": [255, 100, 15]},
    {"key": "moon_color", "label": "Secondary Body (Moon)", "type": "color", "default": [0, 165, 255]},
    {"key": "eclipse_color", "label": "Eclipse / Conjunction Flare", "type": "color", "default": [255, 240, 210]},
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "orbit_speed", "label": "Orbital Speed", "type": "range", "min": 0.04, "max": 1.2, "step": 0.04, "default": 0.24},
    {"key": "radius", "label": "Body Aura Radius", "type": "range", "min": 0.12, "max": 0.65, "step": 0.02, "default": 0.28},
    {"key": "orbit_ratio", "label": "Moon/Sun Speed Ratio", "type": "range", "min": 0.5, "max": 2.5, "step": 0.1, "default": 1.6},
    {"key": "audio_mod", "label": "Audio Gravity Pull", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.25},
]


def _p3(item):
    if item is None:
        return (0.5, 0.5, 0.5)
    vals = list(item) + [0.5, 0.5, 0.5]
    return (float(vals[0]), float(vals[1]), float(vals[2]))


def render(ctx):
    n = max(1, int(ctx["n"]))
    now = float(ctx["now"])
    pos = ctx.get("world_positions") or ctx["positions"]
    scene = ctx.get("scene") or {}
    lo = scene.get("min") or [0.0, 0.0, 0.0]
    hi = scene.get("max") or [1.0, 1.0, 1.0]
    env = ctx.get("env") or {}
    p = ctx["params"]
    u = ctx["utils"]

    master = float(p.get("brightness", 1.0))
    speed = float(p.get("orbit_speed", 0.24))
    r_aura = max(0.08, float(p.get("radius", 0.28)))
    ratio = float(p.get("orbit_ratio", 1.6))
    audio_mod = float(p.get("audio_mod", 0.25))

    c_sun = p.get("sun_color", [255, 100, 15])
    c_moon = p.get("moon_color", [0, 165, 255])
    c_eclipse = p.get("eclipse_color", [255, 240, 210])

    pulse = float(env.get("pulse", 0.0)) * audio_mod
    kick = float(env.get("kick", 0.0)) * audio_mod

    sx = max(0.5, float(hi[0]) - float(lo[0]))
    sy = max(0.5, float(hi[1]) - float(lo[1]))

    # 3D normalized orbital trajectories (0..1 space across the whole scene)
    t1 = now * speed
    t2 = -now * speed * ratio + 1.2

    sun_x = 0.5 + 0.42 * math.cos(t1)
    sun_y = 0.5 + 0.45 * math.sin(t1)

    moon_x = 0.5 + 0.44 * math.cos(t2)
    moon_y = 0.5 + 0.42 * math.sin(t2 * 0.85)

    r_sun = r_aura * (1.0 + 0.45 * pulse + 0.35 * kick)
    r_moon = r_aura * 0.85 * (1.0 + 0.30 * pulse)

    px = []
    for i in range(n):
        wx, wy, _ = _p3(pos[i] if i < len(pos) else None)
        nx = (wx - float(lo[0])) / sx
        ny = (wy - float(lo[1])) / sy

        d_sun = math.hypot(nx - sun_x, ny - sun_y)
        d_moon = math.hypot(nx - moon_x, ny - moon_y)

        # Smooth inverse-square + gaussian falloff so spots never snap harshly
        i_sun = 1.0 / (1.0 + (d_sun / r_sun) ** 2.6)
        i_moon = 1.0 / (1.0 + (d_moon / r_moon) ** 2.6)

        # Conjunction / Eclipse flare when both bodies illuminate the same spot!
        eclipse_overlap = (i_sun * i_moon) ** 1.2

        col_s = u.scale(c_sun, i_sun)
        col_m = u.scale(c_moon, i_moon)
        combined = u.add(col_s, col_m)

        if eclipse_overlap > 0.08:
            combined = u.lerp(combined, c_eclipse, min(1.0, eclipse_overlap * 1.4))

        px.append(u.scale(combined, master))

    return px
