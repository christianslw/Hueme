import math

META = {
    "id": "caustic_abyss",
    "name": "Caustic Abyss (Optical Wave Refraction)",
    "description": "Three golden-ratio interference waves cross the entire 3D room, forming organic caustic light lenses and deep shadow valleys.",
}

PARAMS = [
    {
        "key": "palette",
        "label": "Color Atmosphere",
        "type": "select",
        "default": "moonlight",
        "options": [
            ["moonlight", "Bioluminescent Reef (Abyssal Blue / Cyan / Pearl)"],
            ["ember", "Molten Amber (Deep Umber / 1800K Gold / Sunburst)"],
            ["synth", "Nebula Silk (Deep Violet / Magenta / Cyan)"],
            ["sunset", "Golden Hour (Burgundy / Coral / Warm Champagne)"],
            ["ice", "Glacier Cave (Deep Teal / Ice Blue / White)"],
            ["acid", "Emerald Canopy (Forest / Jade / Sunbeam)"],
        ],
    },
    {"key": "caustic_tint", "label": "Focal Lens Highlight", "type": "color", "default": [255, 225, 175]},
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 0.95},
    {"key": "speed", "label": "Wave Current Speed", "type": "range", "min": 0.05, "max": 1.2, "step": 0.05, "default": 0.28},
    {"key": "scale", "label": "Wave Spatial Density", "type": "range", "min": 0.5, "max": 3.5, "step": 0.1, "default": 1.6},
    {"key": "focus", "label": "Lens Sharpness (Caustic Focus)", "type": "range", "min": 1.2, "max": 5.0, "step": 0.2, "default": 2.8},
    {"key": "shadow_floor", "label": "Deep Water Floor", "type": "range", "min": 0.0, "max": 0.35, "step": 0.02, "default": 0.06},
    {"key": "audio_mod", "label": "Audio Ripple Reactivity", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.25},
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

    master = float(p.get("brightness", 0.95))
    speed = float(p.get("speed", 0.28))
    spatial_k = float(p.get("scale", 1.6)) * math.pi
    focus = float(p.get("focus", 2.8))
    floor = float(p.get("shadow_floor", 0.06))
    audio_mod = float(p.get("audio_mod", 0.25))
    pal = p.get("palette", "moonlight")
    c_lens = p.get("caustic_tint", [255, 225, 175])

    pulse = float(env.get("pulse", 0.0)) * audio_mod
    kick = float(env.get("kick", 0.0)) * audio_mod

    sx = max(0.5, float(hi[0]) - float(lo[0]))
    sy = max(0.5, float(hi[1]) - float(lo[1]))
    sz = max(0.5, float(hi[2]) - float(lo[2]))

    t = now * speed
    px = []

    for i in range(n):
        wx, wy, wz = _p3(pos[i] if i < len(pos) else None)
        nx = (wx - float(lo[0])) / sx
        ny = (wy - float(lo[1])) / sy
        nz = (wz - float(lo[2])) / sz

        # Three non-orthogonal wave vectors with golden-ratio frequencies (never repeats!)
        w1 = math.sin(nx * spatial_k * 0.9 + ny * spatial_k * 1.1 - t * 1.000)
        w2 = math.sin(nx * spatial_k * 1.4 - ny * spatial_k * 0.7 + t * 0.618 + 1.7)
        w3 = math.cos((nx - ny + nz * 0.5) * spatial_k * 0.85 - t * 0.382 + 3.1)

        # Normalize constructive interference to 0..1 and apply optical power focusing
        raw_wave = (w1 + w2 + w3 + 3.0) / 6.0
        caustic = (raw_wave ** focus)

        # Optional gentle kick swell on the brightest caustic crests
        crest_boost = kick * 0.35 * (raw_wave ** 2.0)

        # Sample palette along wave height + slow temporal drift
        col_base = u.gradient(pal, 0.15 + raw_wave * 0.55 + t * 0.04)
        # At constructive wave peaks, refract toward the warm/pearl lens highlight
        lens_mix = min(1.0, (caustic ** 1.35) * 0.85)
        col = u.lerp(col_base, c_lens, lens_mix)

        v = floor + (1.0 - floor) * min(1.0, caustic + crest_boost + pulse * 0.15)
        px.append(u.scale(col, min(1.0, v) * master))

    return px
