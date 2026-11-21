import math

META = {
    "id": "lava_lumen",
    "name": "Lava Lumen (Room-Wide Blobs)",
    "description": "Slow molten blobs wander through the whole room, so zones glow as parts of one lava lamp. No beat required; audio only adds a little heat.",
}

PARAMS = [
    {
        "key": "palette",
        "label": "Lava Palette",
        "type": "select",
        "default": "ember",
        "options": [
            ["ember", "Molten Ember (Amber / Crimson)"],
            ["sunset", "Solar Flare (Rose / Gold)"],
            ["synth", "Cyber Plasma (Teal / Magenta)"],
            ["acid", "Bioluminescent (Lime / Violet)"],
            ["ice", "Liquid Nitrogen (Cyan / Blue)"],
            ["moonlight", "Mercury & Indigo"],
        ],
    },
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 0.95},
    {"key": "blobs", "label": "Blob Count", "type": "range", "min": 2, "max": 5, "step": 1, "default": 4},
    {"key": "size", "label": "Blob Size", "type": "range", "min": 0.22, "max": 0.72, "step": 0.02, "default": 0.60},
    {"key": "speed", "label": "Flow Speed", "type": "range", "min": 0.15, "max": 1.5, "step": 0.05, "default": 0.75},
    {"key": "audio_influence", "label": "Audio Influence (0 = pure ambient)", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.20},
]


def _p3(item):
    if item is None:
        return (0.5, 0.5, 0.5)
    vals = list(item) + [0.5, 0.5, 0.5]
    return (float(vals[0]), float(vals[1]), float(vals[2]))


def render(ctx):
    n = max(1, int(ctx["n"]))
    now = float(ctx["now"])
    p = ctx["params"]
    u = ctx["utils"]
    env = ctx["env"]
    scene = ctx.get("scene") or {}
    pos = ctx.get("world_positions") or ctx["positions"]

    lo = scene.get("min") or [0.0, 0.0, 0.0]
    hi = scene.get("max") or [1.0, 1.0, 1.0]
    center = scene.get("center") or [0.5, 0.5, 0.5]
    cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
    hx = max(0.05, (float(hi[0]) - float(lo[0])) * 0.5)
    hy = max(0.05, (float(hi[1]) - float(lo[1])) * 0.5)
    hz = max(0.05, (float(hi[2]) - float(lo[2])) * 0.5)

    pal = p.get("palette", "ember")
    master = float(p.get("brightness", 0.95))
    count = max(2, min(5, int(float(p.get("blobs", 4)))))
    size = float(p.get("size", 0.60))
    speed = float(p.get("speed", 0.75))
    audio = max(0.0, min(1.0, float(p.get("audio_influence", 0.20))))

    pulse = float(env.get("pulse", 0.0))
    energy = float(env.get("energy", 0.0))
    lift = 1.0 + audio * (0.55 * pulse + 0.20 * energy)

    # Blob positions are pure functions of world time and a golden-ratio phase
    # spread, so every zone sees the same blobs at the same place even though
    # zones render independently.
    ang = now * speed * 0.055
    ca, sa = math.cos(ang), math.sin(ang)
    blobs = []
    for k in range(count):
        ph = 1.7 + k * 2.39996
        bx = 0.84 * math.sin(now * speed * 0.190 + ph * 1.3 + 0.4)
        by = 0.84 * math.sin(now * speed * 0.143 + ph * 2.1 + 2.2)
        bz = 0.72 * math.sin(now * speed * 0.107 + ph * 3.7 + 4.0)
        bx, by = bx * ca - by * sa, bx * sa + by * ca
        radius = size * (0.82 + 0.18 * math.sin(now * speed * 0.110 + ph * 0.7))
        radius *= 1.0 + audio * 0.25 * pulse
        col = u.gradient(pal, 0.04 + 0.17 * k + 0.05 * math.sin(now * 0.017 + ph))
        blobs.append((bx, by, bz, radius, col))

    base_col = u.gradient(pal, 0.02 + now * speed * 0.0015)
    px = []
    for i in range(n):
        x, y, z = _p3(pos[i] if i < len(pos) else None)
        nx = (x - cx) / hx
        ny = (y - cy) / hy
        nz = (z - cz) / hz

        total = 0.0
        red = 0.0
        green = 0.0
        blue = 0.0
        for bx, by, bz, radius, col in blobs:
            dx = nx - bx
            dy = ny - by
            dz = (nz - bz) * 0.85
            d2 = dx * dx + dy * dy + dz * dz
            w = math.exp(-d2 / (2.0 * radius * radius))
            total += w
            red += col[0] * w
            green += col[1] * w
            blue += col[2] * w

        if total > 1e-6:
            avg = (int(red / total), int(green / total), int(blue / total))
            col = u.lerp(base_col, avg, u.smoothstep(0.02, 0.30, total))
        else:
            col = base_col
        # Molten cores warm toward a soft incandescent highlight.
        col = u.lerp(col, (255, 225, 190), min(0.18, total * 0.12))

        v = 0.035 + 0.92 * (1.0 - math.exp(-2.0 * total * lift))
        v += 0.018 * math.sin(now * 0.90 + i * 1.93)
        px.append(u.scale(col, min(1.0, max(0.0, v)) * master))
    return px
