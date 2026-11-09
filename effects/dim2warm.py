import math

META = {
    "id": "dim2warm",
    "name": "Warm Palette Fade",
    "description": "Palette-based warm wash; low brightness bends toward ember, higher values toward moonlight.",
}

PARAMS = [
    {"key": "kelvin", "label": "Warmth (1800-5000)", "type": "range", "min": 1800, "max": 5000, "step": 100, "default": 2700},
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.02, "max": 1.0, "step": 0.02, "default": 0.85},
    {"key": "reactive", "label": "Audio Reactive Warmth", "type": "bool", "default": False},
]


def render(ctx):
    n = ctx["n"]
    now = ctx["now"]
    env = ctx["env"]
    beat = ctx["beat"]
    p = ctx["params"]
    u = ctx["utils"]

    kelvin = float(p.get("kelvin", 2700))
    bri = float(p.get("brightness", 0.85))
    reactive = bool(p.get("reactive", False))
    pulse = float(env.get("pulse", 0.0))
    # 1800 K stays deep in ember, 5000 K leans to the cool end of the palette.
    warmth = max(0.0, min(1.0, (kelvin - 1800.0) / 3200.0))
    drive = pulse if reactive else 0.35 + 0.25 * math.sin(now * 0.6 + beat.get("bar_phase", 0.0) * 6.283)
    t = 0.08 + warmth * 0.30 + 0.05 * math.sin(2.0 * math.pi * beat.get("bar_phase", 0.0))
    lvl = max(0.05, min(1.0, bri * (0.45 + 0.55 * drive)))

    px = []
    for i in range(n):
        px.append(u.scale(u.gradient("ember" if warmth < 0.5 else "moonlight", t + i * 0.01), lvl))
    return px
