META = {
    "id": "ambient_uplight",
    "name": "Ambient Architectural Uplight",
    "description": "Smooth gradient wash with gentle bass floor and alternating snare shimmer.",
}

PARAMS = [
    {"key": "color", "label": "Primary Wash", "type": "color", "default": [255, 0, 85]},
    {"key": "color2", "label": "Accent Shimmer", "type": "color", "default": [0, 240, 255]},
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "base_floor", "label": "Ambient Floor", "type": "range", "min": 0.05, "max": 0.6, "step": 0.05, "default": 0.18},
]


def render(ctx):
    n = ctx["n"]
    af = ctx["features"]
    p = ctx["params"]
    u = ctx["utils"]

    scale = float(p.get("brightness", 1.0))
    floor = float(p.get("base_floor", 0.18))
    c1 = p.get("color", [255, 0, 85])
    c2 = p.get("color2", [0, 240, 255])

    bass = float(af.get("bass", 0.0))
    mids = float(af.get("mids", 0.0))
    snare = float(af.get("snare_env", 0.0))

    px = []
    for i in range(n):
        phase_offset = (i % 2) * 0.25
        base_glow = max(floor, bass ** 1.3)
        c_base = u.lerp(c1, c2, min(1.0, mids + phase_offset))
        col = u.scale(c_base, base_glow * scale)
        col = u.lerp(col, u.scale(c2, scale), snare * 0.55)
        px.append(col)
    return px
