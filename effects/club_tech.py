META = {
    "id": "club_tech",
    "name": "Club Tech (Anchor Split)",
    "description": "Breathing club wash with kick weight in the centre and snare accents toward the edges.",
}

PARAMS = [
    {"key": "color", "label": "Kick Anchor Color", "type": "color", "default": [255, 0, 85]},
    {"key": "color2", "label": "Snare Fill Color", "type": "color", "default": [0, 240, 255]},
    {
        "key": "palette",
        "label": "Accent Palette",
        "type": "select",
        "default": "cyberpunk",
        "options": [
            ["cyberpunk", "Cyberpunk"],
            ["fire_ice", "Fire & Ice"],
            ["rave", "Laser Rave"],
            ["sunset", "Sunset Club"],
            ["warm_gold", "Warm Gold"],
        ],
    },
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "bass_boost", "label": "Kick Weight", "type": "range", "min": 0.5, "max": 2.5, "step": 0.1, "default": 1.3},
    {"key": "snare_flash", "label": "Snare Gain", "type": "range", "min": 0.5, "max": 2.5, "step": 0.1, "default": 1.0},
]


def render(ctx):
    n = ctx["n"]
    pos = ctx["positions"]
    env = ctx["env"]
    beat = ctx["beat"]
    harmony = ctx.get("harmony") or {}
    p = ctx["params"]
    u = ctx["utils"]
    key = int(harmony.get("key", 0)) % 12

    scale = float(p.get("brightness", 1.0))
    kick_gain = float(p.get("bass_boost", 1.3))
    snare_gain = float(p.get("snare_flash", 1.0))
    c_kick = p.get("color", [255, 0, 85])
    c_snare = p.get("color2", [0, 240, 255])

    pulse = float(env.get("pulse", 0.0))
    kick = min(1.0, float(env.get("kick", 0.0)) * kick_gain)
    snare = min(1.0, float(env.get("snare", 0.0)) * snare_gain)
    bright = float(env.get("bright", 0.0))
    energy = float(env.get("energy", 0.0))

    base = 0.42 * pulse
    # Slow palette tilt across the bar instead of threshold flicker.
    tilt = 0.5 + 0.5 * float(beat.get("bar_phase", 0.0)) + energy * 0.1
    coords = u.axis_positions(pos)

    px = []
    for i in range(n):
        x = coords[i] if i < len(coords) else i / max(1, n - 1)
        center = 1.0 - min(1.0, abs(x - 0.5) * 2.0)
        edge = 1.0 - center
        color = u.gradient(p.get("palette", "cyberpunk"), key / 12.0 + tilt * 0.25 + x * 0.45)
        color = u.lerp(color, c_kick, kick * 0.30 * center)
        color = u.lerp(color, c_snare, snare * 0.35 * edge)
        v = base + 0.5 * kick * center + 0.32 * snare * edge + 0.12 * bright * edge
        px.append(u.scale(color, min(1.0, v) * scale))
    return px
