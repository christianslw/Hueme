META = {
    "id": "solid",
    "name": "Solid Static Color",
    "description": "Static non-reactive color output across all fixtures in the zone.",
}

PARAMS = [
    {"key": "color", "label": "Color", "type": "color", "default": [255, 180, 100]},
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.02, "max": 1.0, "step": 0.02, "default": 1.0},
]


def render(ctx):
    n = ctx["n"]
    p = ctx["params"]
    u = ctx["utils"]
    col = u.scale(p.get("color", [255, 180, 100]), float(p.get("brightness", 1.0)))
    return [col] * n
