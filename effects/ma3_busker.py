import math

META = {
    "id": "ma3_busker",
    "name": "MA3 Busker (Structured Concert Choreography)",
    "description": "Zero-flicker deterministic lighting desk: strict spatial geometry (Center/Wings, Odd/Even, Step-Bar, 1-2-3-4 Bar Stack) that snaps ONLY on beats and holds clean shapes.",
}

PARAMS = [
    {
        "key": "mode",
        "label": "Choreography Mechanics",
        "type": "select",
        "default": "auto_phrase",
        "options": [
            ["auto_phrase", "Auto-Director (Switches Geometry Every 4 Bars)"],
            ["chevron", "1. Center <-> Wings (Kick Center, Snare Edges)"],
            ["odd_even", "2. Odd / Even Architectural Grid Flip"],
            ["bar_stack", "3. 1-2-3-4 Bar Stack Builder (Progressive Fill)"],
            ["ping_step", "4. Quantized Beat Stepper (Snap & Hold)"],
        ],
    },
    {"key": "color", "label": "Primary Beam (Kick / Beat)", "type": "color", "default": [255, 30, 0]},
    {"key": "color2", "label": "Secondary Beam (Snare / Counter)", "type": "color", "default": [0, 180, 255]},
    {"key": "bg_color", "label": "Architectural Floor (Idle)", "type": "color", "default": [18, 8, 40]},
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "bg_level", "label": "Background Floor Level", "type": "range", "min": 0.0, "max": 0.35, "step": 0.05, "default": 0.08},
    {"key": "decay_shape", "label": "Beat Hold vs. Punch", "type": "range", "min": 0.2, "max": 0.95, "step": 0.05, "default": 0.65},
]


def setup(state):
    state["last_beat"] = -1
    state["step_idx"] = 0
    state["step_dir"] = 1
    state["wing_state"] = 0
    state["beat_age"] = 0.0
    state["snare_latch"] = 0.0


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
    bg_lvl = float(p.get("bg_level", 0.08))
    hold = float(p.get("decay_shape", 0.65))
    mode = str(p.get("mode", "auto_phrase"))

    c_main = p.get("color", [255, 30, 0])
    c_sec = p.get("color2", [0, 180, 255])
    c_bg = p.get("bg_color", [18, 8, 40])

    b_count = int(beat.get("count", 0))
    b_phase = float(beat.get("phase", 0.0))
    bar_pos = b_count % 4              # 0, 1, 2, 3 (The 1-2-3-4 count of the bar!)
    phrase_idx = (b_count // 16) % 4   # Changes every 4 bars (16 beats)

    kick_hit = bool(af.get("kick"))
    snare_hit = bool(af.get("snare_hit"))
    kick_env = float(env.get("kick", 0.0))
    snare_env = float(env.get("snare", 0.0))
    energy = float(env.get("energy", 0.0))

    # --- STRICT STATE transitions ONLY on discrete beat edges ---
    last_beat = int(st.get("last_beat", -1))
    new_beat = (b_count != last_beat)
    if new_beat:
        st["last_beat"] = b_count
        st["beat_age"] = 0.0
        # Advance deterministic ping-pong stepper on every beat
        s_idx = int(st.get("step_idx", 0))
        s_dir = int(st.get("step_dir", 1))
        if n > 1:
            if s_idx + s_dir >= n:
                s_dir = -1
            elif s_idx + s_dir < 0:
                s_dir = 1
            s_idx = max(0, min(n - 1, s_idx + s_dir))
        st["step_idx"] = s_idx
        st["step_dir"] = s_dir
        st["wing_state"] = b_count % 2
    else:
        st["beat_age"] = float(st.get("beat_age", 0.0)) + dt

    # Snare latch: clean 180ms hold so snare accents are clearly readable blocks
    snare_latch = float(st.get("snare_latch", 0.0))
    if snare_hit or (new_beat and bar_pos in (1, 3) and snare_env > 0.35):
        snare_latch = 1.0
    else:
        snare_latch *= math.exp(-dt / 0.16)
    st["snare_latch"] = snare_latch

    # Beat envelope shaped by `hold`: high hold = steady architectural blocks; low hold = sharp techno stabs
    # Uses beat_phase (0..1) so it is 100% immune to audio RMS jitter!
    beat_shaper = max(0.0, 1.0 - (b_phase / max(0.25, hold * 1.1))) ** 1.4
    beat_level = max(beat_shaper, kick_env * 0.85)

    # Select active choreography geometry
    active_mode = mode
    if mode == "auto_phrase":
        order = ("chevron", "bar_stack", "odd_even", "ping_step")
        active_mode = order[phrase_idx]

    bg_pixel = u.scale(c_bg, bg_lvl * master)
    px = []
    center = (n - 1) / 2.0
    max_d = max(1.0, center)

    for i in range(n):
        dist_norm = abs(i - center) / max_d  # 0.0 at center spot, 1.0 at outer wing spots

        if active_mode == "chevron":
            # GEOMETRY 1: Center Anchor vs. Outer Wings
            # Center (dist < 0.45) locks to Kick / Beat 1&3. Outer Wings lock to Snare / Beat 2&4.
            is_center = dist_norm <= 0.46
            is_wing = dist_norm >= 0.55

            if snare_latch > 0.25:
                # Snare hits: Wings blaze in secondary color, center drops to dark floor
                if is_wing:
                    col = u.lerp(c_bg, c_sec, min(1.0, snare_latch))
                    lvl = bg_lvl + (1.0 - bg_lvl) * snare_latch
                    px.append(u.scale(col, lvl * master))
                else:
                    px.append(bg_pixel)
            else:
                # Beat 1/3 (even beats) -> Center block; Beat 2/4 (odd beats) -> Mid/Wing ring
                if (bar_pos % 2 == 0 and is_center) or (bar_pos % 2 == 1 and not is_center):
                    active_c = c_main if (bar_pos % 2 == 0) else u.lerp(c_main, c_sec, 0.5)
                    lvl = bg_lvl + (1.0 - bg_lvl) * beat_level
                    px.append(u.scale(active_c, lvl * master))
                else:
                    px.append(bg_pixel)

        elif active_mode == "odd_even":
            # GEOMETRY 2: Crisp Binary Architectural Grid (Even spots vs Odd spots flip on every beat)
            active_parity = b_count % 2
            if (i % 2) == active_parity:
                # Active grid half
                col = c_sec if (bar_pos in (1, 3) or snare_latch > 0.4) else c_main
                lvl = bg_lvl + (1.0 - bg_lvl) * max(0.35 * hold, beat_level)
                px.append(u.scale(col, lvl * master))
            else:
                # Inactive grid half stays strictly at background floor -> maximum contrast!
                px.append(bg_pixel)

        elif active_mode == "bar_stack":
            # GEOMETRY 3: 1-2-3-4 Musical Bar Progress Bar
            # Beat 1 lights first 25%, Beat 2 lights 50%, Beat 3 lights 75%, Beat 4 lights 100%!
            spot_progress = i / max(1, n - 1) if n > 1 else 0.0
            bar_fill = (bar_pos + 1) / 4.0  # 0.25, 0.50, 0.75, 1.00

            if spot_progress <= bar_fill + 0.02:
                # Spot is part of the current bar stack
                is_leading_edge = abs(spot_progress - bar_fill) <= (1.0 / max(1, n))
                col = c_sec if (bar_pos == 3 or is_leading_edge) else c_main
                # Leading edge pulses with beat_level, stacked spots hold steady at 75%
                lvl = (0.75 + 0.25 * beat_level) if not is_leading_edge else max(0.5, beat_level)
                px.append(u.scale(col, lvl * master))
            else:
                px.append(bg_pixel)

        else:  # "ping_step"
            # GEOMETRY 4: Quantized Single-Beam Stepper (Moves ONLY on beat edge, zero between-frame drift)
            s_idx = int(st.get("step_idx", 0))
            dist_spots = abs(i - s_idx)
            if dist_spots == 0:
                col = c_sec if (bar_pos in (1, 3)) else c_main
                lvl = max(0.55 * hold, beat_level)
                px.append(u.scale(col, lvl * master))
            elif dist_spots == 1:
                # Subtle neighbor halo so the step feels wide and expensive
                col = u.lerp(c_bg, c_main, 0.5)
                lvl = bg_lvl + 0.30 * beat_level
                px.append(u.scale(col, lvl * master))
            else:
                px.append(bg_pixel)

    return px
