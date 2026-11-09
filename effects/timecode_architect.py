import math

META = {
    "id": "timecode_architect",
    "name": "Timecode Architect (Build / Cut / Drop)",
    "description": "Dramatic stage director: narrows the room to a tight center beam during build-ups, cuts to black before the impact, and explodes wide on drops.",
}

PARAMS = [
    {"key": "color", "label": "Groove Primary", "type": "color", "default": [255, 25, 60]},
    {"key": "color2", "label": "Drop / Climax Impact", "type": "color", "default": [0, 240, 255]},
    {"key": "tension_color", "label": "Build-Up Tension Beam", "type": "color", "default": [255, 220, 160]},
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "punch", "label": "Stab Sharpness", "type": "range", "min": 0.3, "max": 1.0, "step": 0.05, "default": 0.75},
]


def setup(state):
    state["last_beat"] = -1
    state["drop_timer"] = 0.0


def render(ctx):
    n = ctx["n"]
    dt = max(0.001, min(0.1, float(ctx["dt"])))
    af = ctx["features"]
    beat = ctx["beat"]
    env = ctx["env"]
    section = ctx.get("section") or {}
    p = ctx["params"]
    st = ctx["state"]
    u = ctx["utils"]

    master = float(p.get("brightness", 1.0))
    sharpness = float(p.get("punch", 0.75))
    c_groove = p.get("color", [255, 25, 60])
    c_drop = p.get("color2", [0, 240, 255])
    c_build = p.get("tension_color", [255, 220, 160])

    b_count = int(beat.get("count", 0))
    b_phase = float(beat.get("phase", 0.0))
    bar_pos = b_count % 4
    bar_phase = float(beat.get("bar_phase", 0.0))

    build = float(section.get("build", 0.0))
    is_drop = bool(section.get("drop"))
    kick = float(env.get("kick", 0.0))
    snare = float(env.get("snare", 0.0))

    drop_timer = float(st.get("drop_timer", 0.0))
    if is_drop and af.get("kick"):
        drop_timer = 1.0
    else:
        drop_timer = max(0.0, drop_timer - dt * 1.2)
    st["drop_timer"] = drop_timer

    center = (n - 1) / 2.0
    max_d = max(1.0, center)

    # Beat-locked envelope with sharp decay
    decay_len = max(0.18, 1.05 - sharpness * 0.75)
    beat_env = max(0.0, 1.0 - (b_phase / decay_len)) ** 1.6

    px = []

    # --- STATE 1: BUILD-UP COMPRESSION (Spatial narrowing + double-time 8th pulse) ---
    if build > 0.30 and not is_drop:
        # As build rises from 0.3 -> 1.0, the active aperture shrinks toward the center spot
        aperture = max(0.05, 1.0 - (build - 0.25) * 1.25)
        # Pre-drop blackout on Beat 4, second half of the beat!
        if bar_pos == 3 and b_phase > 0.55 and build > 0.55:
            return [(0, 0, 0)] * n

        # Double-time 8th-note grid pulse during high build
        eighth_phase = (b_phase * 2.0) % 1.0
        pulse_gate = max(0.0, 1.0 - eighth_phase * 1.6) if build > 0.6 else beat_env
        col = u.lerp(c_groove, c_build, min(1.0, build * 1.2))

        for i in range(n):
            d_norm = abs(i - center) / max_d
            if d_norm <= aperture:
                v = (0.35 + 0.65 * pulse_gate) * master
                px.append(u.scale(col, v))
            else:
                px.append((0, 0, 0))
        return px

    # --- STATE 2: DROP CLIMAX (Wide architectural wall blinder + alternating center/wing slam) ---
    if is_drop or drop_timer > 0.25:
        even_eight = int(b_phase * 2.0) % 2
        for i in range(n):
            d_norm = abs(i - center) / max_d
            is_wing = d_norm >= 0.5
            # On beat first half: massive outer wings in drop color; on off-beat/kick: center core slam
            if (b_phase < 0.52 and is_wing) or (b_phase >= 0.52 and not is_wing):
                col = c_drop if is_wing else c_build
                lvl = max(0.7, beat_env, kick) * master
                px.append(u.scale(col, lvl))
            else:
                px.append(u.scale(c_groove, 0.06 * master))
        return px

    # --- STATE 3: STRUCTURED GROOVE (4-Beat Architectural Block Quadrants) ---
    # Divides the ceiling into 4 distinct spatial zones corresponding to Beat 1, 2, 3, 4
    # Beat 1: Left End | Beat 2: Right End | Beat 3: Inner Left/Right | Beat 4: Dead Center
    for i in range(n):
        norm_pos = i / max(1, n - 1) if n > 1 else 0.5
        d_norm = abs(i - center) / max_d

        hit = False
        if bar_pos == 0 and norm_pos <= 0.30:
            hit = True
        elif bar_pos == 1 and norm_pos >= 0.70:
            hit = True
        elif bar_pos == 2 and (0.22 < norm_pos < 0.78) and d_norm > 0.15:
            hit = True
        elif bar_pos == 3 and d_norm <= 0.35:
            hit = True

        if hit:
            col = c_drop if (bar_pos in (1, 3) and snare > 0.3) else c_groove
            lvl = (0.15 + 0.85 * max(beat_env, kick * 0.8)) * master
            px.append(u.scale(col, lvl))
        else:
            # Strict negative space: 3% deep floor so active block cuts like a laser
            px.append(u.scale(c_groove, 0.03 * master))

    return px
