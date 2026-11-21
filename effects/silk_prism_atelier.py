import math

META = {
    "id": "silk_prism_atelier",
    "name": "Silk Prism Atelier (3-Color Custom Canvas + Shadow Pockets)",
    "description": "Bespoke 3-color architectural ribbon with drifting negative-space shadow pockets that sculpt depth across all zones.",
}

PARAMS = [
    {"key": "color", "label": "Color A (Warm Anchor)", "type": "color", "default": [255, 110, 20]},
    {"key": "color2", "label": "Color B (Mid Velvet)", "type": "color", "default": [175, 15, 80]},
    {"key": "color3", "label": "Color C (Deep Royal)", "type": "color", "default": [10, 85, 220]},
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 0.95},
    {"key": "flow_speed", "label": "Silk Ribbon Speed", "type": "range", "min": 0.02, "max": 1.0, "step": 0.02, "default": 0.18},
    {"key": "shadow_depth", "label": "Shadow Pocket Depth (Negative Space)", "type": "range", "min": 0.0, "max": 0.95, "step": 0.05, "default": 0.75},
    {"key": "shadow_scale", "label": "Shadow Pocket Size", "type": "range", "min": 0.5, "max": 3.0, "step": 0.1, "default": 1.4},
    {"key": "audio_mod", "label": "Audio Shimmer", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.15},
]


def _p3(item):
    if item is None:
        return (0.5, 0.5, 0.5)
    vals = list(item) + [0.5, 0.5, 0.5]
    return (float(vals[0]), float(vals[1]), float(vals[2]))


def _tri_color(u, c1, c2, c3, t):
    """Smooth circular interpolation through 3 user colors."""
    p = float(t) % 1.0
    if p < 0.3333:
        w = u.smoothstep(0.0, 0.3333, p)
        return u.lerp(c1, c2, w)
    elif p < 0.6667:
        w = u.smoothstep(0.3333, 0.6667, p)
        return u.lerp(c2, c3, w)
    else:
        w = u.smoothstep(0.6667, 1.0, p)
        return u.lerp(c3, c1, w)


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

    master = float(p.get("brightness", 0.95))
    speed = float(p.get("flow_speed", 0.18))
    sh_depth = float(p.get("shadow_depth", 0.75))
    sh_scale = float(p.get("shadow_scale", 1.4))
    audio_mod = float(p.get("audio_mod", 0.15))

    c1 = p.get("color", [255, 110, 20])
    c2 = p.get("color2", [175, 15, 80])
    c3 = p.get("color3", [10, 85, 220])

    pulse = float(env.get("pulse", 0.0)) * audio_mod
    bright = float(env.get("bright", 0.0)) * audio_mod

    sx = max(0.5, float(hi[0]) - float(lo[0]))
    sy = max(0.5, float(hi[1]) - float(lo[1]))

    t = now * speed
    px = []

    for i in range(n):
        wx, wy, _ = _p3(pos[i] if i < len(pos) else None)
        nx = (wx - float(lo[0])) / sx
        ny = (wy - float(lo[1])) / sy

        # 2D spatial coordinate along the room diagonal + gentle organic curl
        spatial_pos = (nx * 0.45 + ny * 0.75) + 0.12 * math.sin(ny * 3.1415 - t * 1.3)
        color_phase = spatial_pos * 0.55 - t * 0.35 + 0.08 * bright
        col = _tri_color(u, c1, c2, c3, color_phase)

        # Travelling negative-space shadow pockets (two counter-drifting waves)
        s1 = math.sin((nx + ny) * math.pi * sh_scale + t * 1.1)
        s2 = math.cos((ny - nx * 0.6) * math.pi * sh_scale * 0.7 - t * 0.68)
        pocket = ((s1 * s2) + 1.0) * 0.5  # 0..1

        # Carve deep, velvety shadows between the lit pockets
        light_mask = (1.0 - sh_depth) + sh_depth * (pocket ** 1.8)
        v = min(1.0, light_mask + pulse * 0.22)

        px.append(u.scale(col, v * master))

    return px
