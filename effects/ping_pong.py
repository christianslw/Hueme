META = {
    "id": "ping_pong",
    "name": "Beat Ping-Pong Scanner",
    "description": "High-contrast kinetic scanner that inverts travel direction on Kick transients.",
}

PARAMS = [
    {"key": "color", "label": "Scanner Primary", "type": "color", "default": [255, 0, 85]},
    {"key": "color2", "label": "Snare Flash Color", "type": "color", "default": [0, 240, 255]},
    {
        "key": "palette",
        "label": "Beat Cycle Palette",
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
    {"key": "speed", "label": "Base Speed", "type": "range", "min": 0.5, "max": 6.0, "step": 0.25, "default": 2.4},
]


def setup(state):
    state["pos"] = 0.0
    state["dir"] = 1.0
    state["last_beat"] = -1


def render(ctx):
    n = ctx["n"]
    dt = ctx["dt"]
    af = ctx["features"]
    p = ctx["params"]
    st = ctx["state"]
    u = ctx["utils"]

    scale = float(p.get("brightness", 1.0))
    base_speed = float(p.get("speed", 2.4))
    pal = u.palette(p.get("palette", "cyberpunk"))
    c_snare = p.get("color2", [0, 240, 255])

    bass = float(af.get("bass", 0.0))
    snare = float(af.get("snare_env", 0.0))
    b_cnt = int(af.get("beat_count", 0))

    if b_cnt != st.get("last_beat", -1):
        st["last_beat"] = b_cnt
        st["dir"] = -float(st.get("dir", 1.0))

    step = (base_speed + bass * 8.0) * dt * float(st.get("dir", 1.0))
    pos = (float(st.get("pos", 0.0)) + step) % max(1, n)
    st["pos"] = pos

    active_c = pal[b_cnt % len(pal)]
    px = []
    for i in range(n):
        d = abs(i - pos)
        d = min(d, n - d)
        v = max(0.0, 1.0 - d * 1.15) ** 2.0
        col = u.scale(active_c, v * scale)
        if snare > 0.45:
            col = u.add(col, u.scale(c_snare, snare * 0.7 * scale))
        px.append(col)
    return px
