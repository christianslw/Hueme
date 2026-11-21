import math

META = {
    "id": "symphonic_director",
    "name": "Symphonic Director (ShowGraph Acts)",
    "description": "Five-act architectural show driven by ctx['cue']: negative-space intro, mirrored verse, narrowing build, pre-drop vacuum, and full-stage drop.",
}

PARAMS = [
    {"key": "color", "label": "Verse Primary", "type": "color", "default": [255, 90, 45]},
    {"key": "color2", "label": "Drop / Wing Impact", "type": "color", "default": [0, 220, 255]},
    {"key": "intro_color", "label": "Intro Indigo", "type": "color", "default": [55, 32, 135]},
    {"key": "warm_cct", "label": "Intro Warm CCT", "type": "range", "min": 1800, "max": 2600, "step": 50, "default": 2000},
    {"key": "cool_cct", "label": "Build Cool CCT", "type": "range", "min": 4200, "max": 6500, "step": 100, "default": 6200},
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "wave_speed", "label": "Intro Wave Speed", "type": "range", "min": 0.4, "max": 2.0, "step": 0.1, "default": 0.9},
]


def setup(state):
    state["waves"] = []
    state["wave_dir"] = 1.0
    state["last_wave_t"] = -10.0


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def render(ctx):
    n = max(1, int(ctx["n"]))
    dt = max(0.001, min(0.10, float(ctx["dt"])))
    now = float(ctx["now"])
    af = ctx["features"]
    env = ctx["env"]
    beat = ctx["beat"]
    section_cfg = ctx.get("section") or {}
    cue = ctx.get("cue") or {}
    p = ctx["params"]
    st = ctx["state"]
    u = ctx["utils"]

    master = _clamp(p.get("brightness", 1.0), 0.1, 1.0)
    c_primary = p.get("color", [255, 90, 45])
    c_drop = p.get("color2", [0, 220, 255])
    c_intro = p.get("intro_color", [55, 32, 135])
    warm_cct = float(p.get("warm_cct", 2000))
    cool_cct = float(p.get("cool_cct", 6200))
    wave_speed = float(p.get("wave_speed", 0.9))

    b_phase = _clamp(beat.get("phase", 0.0))
    bar_pos = int(beat.get("bar_pos", int(beat.get("count", 0)) % 4))
    kick = bool(af.get("kick"))
    snare = bool(af.get("snare_hit"))
    kick_env = _clamp(env.get("kick", 0.0))
    pulse = _clamp(env.get("pulse", 0.0))
    cue_ok = bool(cue.get("available"))
    if cue_ok:
        pierce = _clamp(cue.get("percussive_punch", max(kick_env, _clamp(env.get("snare", 0.0)))))
        harmonic = _clamp(cue.get("harmonic_swell", max(pulse, env.get("energy", 0.0))))
        prominence = _clamp(cue.get("prominence", env.get("energy", 0.0)))
    else:
        pierce = max(kick_env, _clamp(env.get("snare", 0.0)))
        harmonic = max(pulse, _clamp(env.get("energy", 0.0)))
        prominence = _clamp(env.get("energy", 0.0))

    # Cue fallback for the pre-ShowGraph live path.
    if cue_ok:
        section = str(cue.get("section", "verse"))
        progress = _clamp(cue.get("section_progress", 0.0))
        beats_to_drop = max(0.0, float(cue.get("beats_to_drop", 0.0) or 0.0))
    else:
        legacy = str(section_cfg.get("name", "idle"))
        if legacy == "drop":
            section = "drop"
        elif legacy == "build":
            section = "build"
        elif legacy == "idle":
            section = "intro"
        else:
            section = "verse"
        progress = _clamp(section_cfg.get("build", 0.0))
        beats_to_drop = max(0.0, (1.0 - progress) * 8.0) if section == "build" else 0.0

    center = (n - 1) / 2.0
    max_d = max(1.0, center)
    zone_depth = n <= 3

    # Update intro's solitary ballistic waves.
    waves = st.setdefault("waves", [])
    for w in waves:
        w["pos"] += w["vel"] * dt
        w["life"] *= math.exp(-dt * 0.85)
    st["waves"] = [w for w in waves if w["life"] > 0.02 and -0.15 < w["pos"] < 1.15]

    # --- Act 4: pre-drop vacuum (checked before build/drop) -----------------
    if section == "pre_drop" or (0.0 < beats_to_drop <= 1.5):
        return [(0, 0, 0)] * n

    # --- Act 1: intro / breakdown ------------------------------------------
    if section in ("intro", "breakdown"):
        if (kick or snare or pierce > 0.72) and now - float(st.get("last_wave_t", -10.0)) > 0.20:
            direction = float(st.get("wave_dir", 1.0))
            waves.append({
                "pos": 0.0 if direction > 0 else 1.0,
                "vel": direction * (0.45 + 0.55 * wave_speed) * (0.7 + 0.6 * pierce),
                "amp": 0.35 + 0.65 * pierce,
                "life": 1.0,
            })
            st["wave_dir"] = -direction
            st["last_wave_t"] = now
        waves = st["waves"]

        warm = u.cct_to_rgb(warm_cct + 200.0 * harmonic, 1.0)
        base_col = u.lerp(c_intro, warm, _clamp(0.10 + harmonic * 1.15))
        base_level = (0.035 + 0.38 * harmonic + 0.20 * prominence) * master
        wave_col = u.cct_to_rgb(warm_cct + 120.0, 1.0)
        px = []
        for i in range(n):
            # Deterministic 2-of-7 mask: ~70% of the room stays in true black.
            lit = ((i * 5 + 2) % 7) < 2
            level = base_level if lit else 0.0
            x = (i / max(1, n - 1)) if n > 1 else 0.5
            for w in waves:
                g = math.exp(-((x - w["pos"]) / 0.17) ** 2)
                level = max(level, g * w["amp"] * w["life"] * master)
            if level > 0.001:
                # Indigo wash and warm transient waves stay on the same locus.
                col = u.lerp(base_col, wave_col, _clamp(level)) if not lit else base_col
                px.append(u.scale(col, _clamp(level)))
            else:
                px.append((0, 0, 0))
        return px

    # --- Act 3: build-up aperture (smooth cinematic focus) ------------------
    if section == "build":
        aperture = _clamp(1.0 - progress * 0.75)
        if zone_depth:
            aperture *= 0.85
        # One smooth breath per beat, never 16th-note strobing.
        gate = 0.30 + 0.70 * (0.5 + 0.5 * math.cos(2.0 * math.pi * b_phase))
        temp = warm_cct + (cool_cct - warm_cct) * progress
        col = u.cct_to_rgb(temp, 1.0)
        px = []
        for i in range(n):
            d = abs(i - center) / max_d
            if d > aperture:
                px.append((0, 0, 0))
                continue
            centre_weight = 1.0 - _clamp(d * 1.5)
            level = (0.25 + 0.75 * gate) * (0.60 + 0.40 * centre_weight) * master
            px.append(u.scale(col, _clamp(level)))
        return px

    # --- Act 5: drop (clean power architecture, no strobe) ------------------
    if section == "drop":
        step = bar_pos % 2
        step_env = max(0.25, 1.0 - b_phase * 1.1) ** 1.4
        px = []
        for i in range(n):
            d = abs(i - center) / max_d
            is_wing = d >= 0.45
            if (step == 0 and not is_wing) or (step == 1 and is_wing):
                col = c_drop if is_wing else c_primary
                level = max(0.50, step_env) * master
                px.append(u.scale(col, _clamp(level)))
            else:
                # Architectural shadow, not a black strobe frame.
                px.append(u.scale(c_primary, 0.04 * master))
        return px

    # --- Act 2: verse / steady groove --------------------------------------
    if zone_depth:
        # Zone 2: complementary depth only, never a second main stage.
        level = (0.06 + 0.20 * harmonic + 0.08 * prominence) * master
        col = u.lerp(c_intro, c_primary, 0.45)
        return [u.scale(col, _clamp(level)) for _ in range(n)]

    pair_offset = 1.0 if bar_pos % 2 == 0 else 2.0
    step_env = math.exp(-((b_phase * 4.0) % 1.0) * 2.4)
    anchor = max(kick_env, pulse, 0.20 + 0.50 * harmonic)
    px = []
    for i in range(n):
        d = abs(i - center)
        if d < 0.45:
            level = 0.18 + 0.82 * anchor
            col = c_primary
        elif abs(d - pair_offset) < 0.45:
            level = 0.08 + 0.58 * step_env + 0.22 * anchor
            col = c_drop if snare else c_primary
        elif abs(d - (pair_offset + 1.0)) < 0.45:
            level = 0.04 + 0.24 * step_env
            col = c_primary
        else:
            level = 0.012 + 0.035 * harmonic * prominence
            col = u.lerp(c_intro, c_primary, 0.35)
        px.append(u.scale(col, _clamp(level * master)))
    return px
