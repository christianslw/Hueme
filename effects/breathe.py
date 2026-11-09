import math

META = {
    "id": "breathe",
    "name": "Dual-Color Breathe",
    "description": "Smooth sinusoidal breathing between primary and secondary colors.",
}

PARAMS = [
    {"key": "color", "label": "Primary Color", "type": "color", "default": [255, 0, 85]},
    {"key": "color2", "label": "Secondary Color", "type": "color", "default": [0, 240, 255]},
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "speed", "label": "Pulse Rate", "type": "range", "min": 0.2, "max": 5.0, "step": 0.2, "default": 1.8},
]


def render(ctx):
    n = ctx["n"]
    now = ctx["now"]
    p = ctx["params"]
    u = ctx["utils"]

    scale = float(p.get("brightness", 1.0))
    speed = float(p.get("speed", 1.8))
    c1 = p.get("color", [255, 0, 85])
    c2 = p.get("color2", [0, 240, 255])

    t = 0.5 + 0.5 * math.sin(now * speed)
    mix = u.scale(u.lerp(c1, c2, t), (0.2 + 0.8 * t) * scale)
    return [mix] * n
