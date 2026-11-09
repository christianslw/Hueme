META = {
    "id": "diagnostic_chase",
    "name": "Sequential Diagnostic Chase",
    "description": "Steps spot-by-spot in sequence with an index0 marker to verify physical fixture sorting.",
}

PARAMS = [
    {"key": "color", "label": "Active Spot Color", "type": "color", "default": [0, 240, 255]},
    {"key": "color2", "label": "Spot #0 Marker Color", "type": "color", "default": [255, 0, 85]},
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "speed", "label": "Step Speed (Hz)", "type": "range", "min": 0.5, "max": 8.0, "step": 0.5, "default": 2.5},
]


def render(ctx):
    n = ctx["n"]
    now = ctx["now"]
    p = ctx["params"]
    u = ctx["utils"]

    scale = float(p.get("brightness", 1.0))
    speed = float(p.get("speed", 2.5))
    c_main = p.get("color", [0, 240, 255])
    c_zero = p.get("color2", [255, 0, 85])

    idx = int(now * speed) % max(1, n)
    px = []
    for i in range(n):
        if i == idx:
            px.append(u.scale(c_zero if i == 0 else c_main, scale))
        else:
            px.append(u.scale(c_main, scale * 0.06))
    return px
