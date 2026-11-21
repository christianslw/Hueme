import math

META = {
    "id": "aurora_veil",
    "name": "Aurora Veil (Room-Wide)",
    "description": "One continuous aurora curtain across every zone: slow world-space ribbons, no beat required. Music only gently lifts the glow.",
}

PARAMS = [
    {
        "key": "palette",
        "label": "Aurora Palette",
        "type": "select",
        "default": "ice",
        "options": [
            ["ice", "Nordic Ice (Cyan / Emerald)"],
            ["moonlight", "Deep Ocean (Indigo / Cyan)"],
            ["synth", "Velvet Synth (Teal / Magenta)"],
            ["acid", "Bioluminescent (Lime / Violet)"],
            ["sunset", "Horizon (Rose / Gold)"],
            ["ember", "Ember (Amber / Crimson)"],
        ],
    },
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 0.90},
    {"key": "drift", "label": "Drift Speed", "type": "range", "min": 0.10, "max": 2.0, "step": 0.05, "default": 0.55},
    {"key": "scale", "label": "Curtain Scale", "type": "range", "min": 0.5, "max": 3.0, "step": 0.1, "default": 1.30},
    {"key": "audio_influence", "label": "Audio Influence (0 = pure ambient)", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.18},
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

    pal = p.get("palette", "ice")
    master = float(p.get("brightness", 0.90))
    drift = float(p.get("drift", 0.55))
    scale = float(p.get("scale", 1.30))
    audio = max(0.0, min(1.0, float(p.get("audio_influence", 0.18))))

    pulse = float(env.get("pulse", 0.0))
    energy = float(env.get("energy", 0.0))
    lift = 1.0 + audio * (0.55 * pulse + 0.25 * energy)

    # A slowly rotating projection makes the curtain breathe through the room
    # instead of looking like a fixed gradient. Every zone shares `now` and the
    # same scene bounds, so the field is continuous across zone borders.
    ang = now * 0.035 * drift
    ca, sa = math.cos(ang), math.sin(ang)
    breath = 0.015 + 0.02 * (0.5 + 0.5 * math.sin(now * 0.27))

    px = []
    for i in range(n):
        x, y, z = _p3(pos[i] if i < len(pos) else None)
        nx = (x - cx) / hx
        ny = (y - cy) / hy
        nz = (z - cz) / hz

        s = nx * ca + ny * sa
        q = -nx * sa + ny * ca

        # Two slow, cross-modulated flow octaves. Pure sinusoids keep every
        # frame continuous; nothing snaps or strobes.
        flow_a = 0.5 + 0.5 * math.sin(
            6.28318 * (s * scale * 0.34 + now * 0.052 * drift)
            + 1.25 * math.sin(now * 0.031 + q * 1.4)
            + nz * 0.55
        )
        flow_b = 0.5 + 0.5 * math.sin(
            6.28318 * (q * scale * 0.19 - now * 0.029 * drift)
            + 0.90 * math.sin(now * 0.023 + s * 1.1)
            - nz * 0.35
        )
        ribbon = (flow_a * 0.62 + flow_b * 0.38) ** 1.35

        # Soft band sweeping across the room. The 0.28 floor keeps the whole
        # scene in a dim aurora wash even when the bright band is elsewhere.
        cross = q - 0.50 * math.sin(now * 0.041 * drift + s * 0.85)
        curtain = 0.28 + 0.72 * math.exp(-(cross * cross) / (2.0 * 0.62 * 0.62))

        height = 0.50 + 0.50 * (0.5 + 0.5 * nz)
        v = 0.05 + 0.68 * ribbon * curtain + 0.10 * height + breath
        # Barely-there shimmer keeps large surfaces alive without flicker.
        v += 0.022 * math.sin(now * 0.81 + i * 2.17 + nx * 2.3) * (0.5 + 0.5 * nz)
        v = min(1.0, max(0.0, v * lift)) * master

        col_t = 0.50 + s * 0.15 + ribbon * 0.16 + 0.0019 * now * drift
        col = u.gradient(pal, col_t)
        crest = u.smoothstep(0.55, 1.0, ribbon * curtain)
        col = u.lerp(col, (225, 240, 255), crest * 0.20)
        px.append(u.scale(col, v))
    return px
