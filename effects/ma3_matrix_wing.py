import math

META = {
    "id": "ma3_matrix_wing",
    "name": "MA3 Symmetrical Wings (MAtricks)",
    "description": "True GrandMA3 2-Wing symmetry desk: pairs of spots step Inside-Out, Outside-In, or Split-Cross strictly on the beat grid with zero between-beat wobble.",
}

PARAMS = [
    {
        "key": "wing_style",
        "label": "MAtricks Wing Mode",
        "type": "select",
        "default": "auto_phrase",
        "options": [
            ["auto_phrase", "Auto-Operator (Switches Every 2 Bars)"],
            ["inside_out", "1. Inside-Out (Center -> Walls on 1-2-3-4)"],
            ["outside_in", "2. Outside-In (Walls -> Center on 1-2-3-4)"],
            ["bounce_wing", "3. Symmetrical Bounce (Center <-> Walls)"],
            ["frame_lock", "4. Wall Frame vs. Core (Kick Core / Beat Frame)"],
        ],
    },
    {"key": "color", "label": "Primary Wing Beam", "type": "color", "default": [255, 45, 0]},
    {"key": "color2", "label": "Snare / Rim Strobe", "type": "color", "default": [0, 210, 255]},
    {"key": "bg_color", "label": "Dark Floor Tint", "type": "color", "default": [12, 6, 28]},
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "gate_time", "label": "Beam Gate (Short Stab <-> Long Hold)", "type": "range", "min": 0.15, "max": 0.95, "step": 0.05, "default": 0.55},
    {"key": "bg_level", "label": "Ambient Floor", "type": "range", "min": 0.0, "max": 0.25, "step": 0.02, "default": 0.04},
]


def setup(state):
    state["last_beat"] = -1
    state["wing_pos"] = 0
    state["wing_dir"] = 1
    state["snare_hold"] = 0.0


def render(ctx):
    n = ctx["n"]
    dt = max(0.001, min(0.1, float(ctx["dt"])))
    af = ctx["features"]
    beat = ctx["beat"]
    env = ctx["env"]
    p = ctx["params"]
    st = ctx["state"]
    u = ctx["utils"]

    master = float(p.get("brightness", 1.0))
    gate = float(p.get("gate_time", 0.55))
    bg_lvl = float(p.get("bg_level", 0.04))
    style = str(p.get("wing_style", "auto_phrase"))

    c_beam = p.get("color", [255, 45, 0])
    c_rim = p.get("color2", [0, 210, 255])
    c_bg = p.get("bg_color", [12, 6, 28])

    b_count = int(beat.get("count", 0))
    b_phase = float(beat.get("phase", 0.0))
    bar_pos = b_count % 4
    phrase = (b_count // 8) % 4

    # Number of symmetrical distance rings from center (e.g. for 7 spots: ring 0=spot4, 1=spot3&5, 2=spot2&6, 3=spot1&7)
    center = (n - 1) / 2.0
    n_rings = max(1, int(math.ceil(center)) + 1)

    last_beat = int(st.get("last_beat", -1))
    if b_count != last_beat:
        st["last_beat"] = b_count
        w_pos = int(st.get("wing_pos", 0))
        w_dir = int(st.get("wing_dir", 1))
        if n_rings > 1:
            if w_pos + w_dir >= n_rings:
                w_dir = -1
            elif w_pos + w_dir < 0:
                w_dir = 1
            w_pos = max(0, min(n_rings - 1, w_pos + w_dir))
        st["wing_pos"] = w_pos
        st["wing_dir"] = w_dir

    snare_hold = float(st.get("snare_hold", 0.0))
    if af.get("snare_hit") or (float(env.get("snare", 0.0)) > 0.55 and b_phase < 0.2):
        snare_hold = 1.0
    else:
        snare_hold *= math.exp(-dt / 0.11)
    st["snare_hold"] = snare_hold

    # Crisp gate envelope locked to beat_phase (zero audio-RMS noise!)
    if b_phase < gate:
        # Flat plateau for the first 35% of gate, then clean linear cut
        t_g = b_phase / max(0.05, gate)
        env_gate = 1.0 if t_g < 0.35 else max(0.0, 1.0 - (t_g - 0.35) / 0.65) ** 1.5
    else:
        env_gate = 0.0

    active_mode = style
    if style == "auto_phrase":
        active_mode = ("inside_out", "outside_in", "bounce_wing", "frame_lock")[phrase]

    if active_mode == "inside_out":
        active_ring = bar_pos % n_rings
    elif active_mode == "outside_in":
        active_ring = (n_rings - 1) - (bar_pos % n_rings)
    elif active_mode == "bounce_wing":
        active_ring = int(st.get("wing_pos", 0))
    else:  # frame_lock
        active_ring = 0 if (bar_pos % 2 == 0) else (n_rings - 1)

    bg_px = u.scale(c_bg, bg_lvl * master)
    px = []

    for i in range(n):
        ring_idx = int(round(abs(i - center)))
        is_outer_edge = (i == 0 or i == n - 1)

        # Snare override on outer architectural wall spots
        if is_outer_edge and snare_hold > 0.25 and active_ring != ring_idx:
            col = u.lerp(c_bg, c_rim, snare_hold)
            lvl = bg_lvl + (1.0 - bg_lvl) * snare_hold
            px.append(u.scale(col, lvl * master))
            continue

        if ring_idx == active_ring:
            # Downbeat (Beat 1) uses accent color on outer/center peak for clear bar orientation
            use_sec = (bar_pos == 3) or (active_mode == "frame_lock" and ring_idx == n_rings - 1)
            beam_c = c_rim if use_sec else c_beam
            lvl = bg_lvl + (1.0 - bg_lvl) * max(0.18, env_gate)
            px.append(u.scale(beam_c, lvl * master))
        else:
            px.append(bg_px)

    return px
