import math

META = {
    "id": "starlit_canopy",
    "name": "Starlit Canopy (Room-Wide)",
    "description": "A slow night sky over the whole room: drifting galaxy band, individually twinkling fixtures and a comet that crosses every zone. Runs without music.",
}

PARAMS = [
    {
        "key": "palette",
        "label": "Night Palette",
        "type": "select",
        "default": "moonlight",
        "options": [
            ["moonlight", "Moonlight (Indigo / Cyan)"],
            ["ice", "Glacier (Cyan / Emerald)"],
            ["synth", "Velvet Night (Teal / Magenta)"],
            ["acid", "Aurora (Lime / Violet)"],
            ["sunset", "Dusk (Rose / Gold)"],
            ["ember", "Ember Night (Amber / Crimson)"],
        ],
    },
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 0.95},
    {"key": "drift", "label": "Sky Drift", "type": "range", "min": 0.10, "max": 2.0, "step": 0.05, "default": 0.50},
    {"key": "twinkle", "label": "Star Twinkle", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.85},
    {"key": "comet", "label": "Comet Brightness", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.75},
    {"key": "audio_influence", "label": "Audio Influence (0 = pure ambient)", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.10},
]


def _p3(item):
    if item is None:
        return (0.5, 0.5, 0.5)
    vals = list(item) + [0.5, 0.5, 0.5]
    return (float(vals[0]), float(vals[1]), float(vals[2]))


def _hash01(a, b, c):
    v = math.sin(a * 12.9898 + b * 78.233 + c * 37.719) * 43758.5453
    return v - math.floor(v)


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

    pal = p.get("palette", "moonlight")
    master = float(p.get("brightness", 0.95))
    drift = float(p.get("drift", 0.50))
    twinkle_gain = float(p.get("twinkle", 0.85))
    comet_gain = float(p.get("comet", 0.75))
    audio = max(0.0, min(1.0, float(p.get("audio_influence", 0.10))))

    pulse = float(env.get("pulse", 0.0))
    bright = float(env.get("bright", 0.0))
    energy = float(env.get("energy", 0.0))

    ang = now * 0.012 * drift
    ca, sa = math.cos(ang), math.sin(ang)

    # One comet per ~17 s. It fades in, crosses the room, then burns out.
    comet_env = 0.0
    chx = chy = chz = 0.0
    if comet_gain > 0.0:
        period = 17.0
        cycle = now / period
        idx = int(math.floor(cycle))
        frac = cycle - idx
        if frac < 0.62:
            travel = frac / 0.62
            comet_env = u.smoothstep(0.0, 0.10, frac) * (1.0 - u.smoothstep(0.50, 0.62, frac))
            ax = 0.92 * math.sin(idx * 1.71)
            ay = 0.92 * math.sin(idx * 2.33 + 1.1)
            az = 0.70 * math.sin(idx * 3.17 + 2.2)
            bx = -0.92 * math.sin(idx * 0.93 + 0.7)
            by = -0.92 * math.sin(idx * 1.37 + 3.0)
            bz = -0.70 * math.sin(idx * 2.03 + 4.4)
            chx = ax + (bx - ax) * travel + 0.14 * math.sin(3.14159 * travel) * math.sin(idx * 0.7)
            chy = ay + (by - ay) * travel + 0.14 * math.sin(3.14159 * travel) * math.cos(idx * 0.9)
            chz = az + (bz - az) * travel

    px = []
    for i in range(n):
        x, y, z = _p3(pos[i] if i < len(pos) else None)
        nx = (x - cx) / hx
        ny = (y - cy) / hy
        nz = (z - cz) / hz

        s = nx * ca + ny * sa
        q = -nx * sa + ny * ca

        band_center = 0.22 * math.sin(now * 0.022 * drift) + 0.10 * math.sin(now * 0.007 + nz * 0.8)
        galaxy = math.exp(-((s - band_center) ** 2) / (2.0 * 0.60 * 0.60))
        nebula = 0.5 + 0.5 * math.sin(6.28318 * (q * 0.55 + now * 0.009 * drift) + 1.2 * math.sin(now * 0.013 + s))
        sky = 0.05 + 0.17 * galaxy + 0.055 * nebula
        sky += audio * (0.04 * pulse + 0.02 * energy)

        # Each fixture is its own star. The seed comes from world position, so
        # all zones agree on which lamp twinkles when, without shared state.
        seed = _hash01(x * 0.37 + 1.3, y * 0.71 + 2.7, z * 0.53 + 4.1)
        rate = 0.045 + 0.11 * seed
        phase = 6.28318 * _hash01(x * 1.7 + 11.0, y * 2.3 + 5.0, z * 3.1 + 7.0)
        tw = 0.5 + 0.5 * math.sin(6.28318 * rate * now + phase)
        star = (tw ** 5) * twinkle_gain * (0.35 + 0.65 * seed)
        star *= 1.0 + audio * 0.40 * bright

        dist = math.hypot(nx - chx, ny - chy, (nz - chz) * 0.85)
        comet_glow = comet_env * comet_gain * (
            math.exp(-(dist / 0.10) ** 2) + 0.35 * math.exp(-(dist / 0.34) ** 2)
        )

        v = sky + 0.90 * star + 0.85 * comet_glow
        v = min(1.0, max(0.0, v)) * master

        col_t = 0.50 + s * 0.10 + 0.0009 * now * drift + 0.05 * galaxy
        col = u.gradient(pal, col_t)
        col = u.lerp(col, (160, 195, 255), galaxy * 0.32)
        col = u.lerp(col, (245, 248, 255), min(0.85, star * 0.90 + comet_glow * 0.80))
        px.append(u.scale(col, v))
    return px
