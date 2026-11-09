import math

META = {
    "id": "ferro_pendulum",
    "name": "Ferrofluid Pendulum (Inertial Physics)",
    "description": "Simulates a heavy magnetic light mass with real momentum, spring return, bass-driven viscosity, and snare prism splitting.",
}

PARAMS = [
    {
        "key": "palette",
        "label": "Fluid Palette",
        "type": "select",
        "default": "ember",
        "options": [
            ["ember", "Molten Gold & Ember"],
            ["synth", "Cybernetic Plasma (Teal / Magenta)"],
            ["sunset", "Solar Flare (Crimson / Amber)"],
            ["ice", "Liquid Nitrogen (Cyan / Deep Blue)"],
            ["moonlight", "Mercury & Indigo"],
            ["acid", "Venom & Ultraviolet"],
        ],
    },
    {"key": "brightness", "label": "Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "mass", "label": "Fluid Inertia (Heaviness)", "type": "range", "min": 0.3, "max": 2.0, "step": 0.1, "default": 1.0},
    {"key": "viscosity", "label": "Body Width", "type": "range", "min": 0.12, "max": 0.55, "step": 0.02, "default": 0.24},
    {"key": "dispersion", "label": "Snare Prism Split", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.65},
]


def setup(state):
    state["pos"] = 0.5
    state["vel"] = 0.0
    state["dir"] = 1.0
    state["split"] = 0.0
    state["afterglow"] = []


def render(ctx):
    n = ctx["n"]
    dt = max(0.001, min(0.1, float(ctx["dt"])))
    pos_list = ctx.get("world_positions") or ctx["positions"]
    env = ctx["env"]
    beat = ctx["beat"]
    groove = ctx.get("groove") or {}
    harmony = ctx.get("harmony") or {}
    p = ctx["params"]
    st = ctx["state"]
    u = ctx["utils"]

    scale = float(p.get("brightness", 1.0))
    mass = max(0.2, float(p.get("mass", 1.0)))
    base_width = float(p.get("viscosity", 0.24))
    disp_gain = float(p.get("dispersion", 0.65))
    pal = p.get("palette", "ember")

    pulse = float(env.get("pulse", 0.0))
    kick = float(env.get("kick", 0.0))
    snare = float(env.get("snare", 0.0))
    bright = float(env.get("bright", 0.0))
    energy = float(env.get("energy", 0.0))
    swing = float(groove.get("swing", 0.0))
    bar_phase = float(beat.get("bar_phase", 0.0))
    beat_phase = float(beat.get("phase", 0.0))
    key_shift = (int(harmony.get("key", 0)) % 12) / 12.0

    x_pos = float(st.get("pos", 0.5))
    vel = float(st.get("vel", 0.0))
    direction = float(st.get("dir", 1.0))
    split = float(st.get("split", 0.0))

    # Beat-locked harmonic anchor gently guides the equilibrium point across the room
    anchor = 0.5 + 0.28 * math.sin(2.0 * math.pi * (bar_phase + swing * 0.08))

    # Kick impulse injects kinetic energy into the pendulum
    if ctx["features"].get("kick") and kick > 0.2:
        # Alternate impulse direction or push away from walls
        if x_pos > 0.72:
            direction = -1.0
        elif x_pos < 0.28:
            direction = 1.0
        else:
            direction = -direction
        impulse = (1.6 + 1.8 * kick) / mass
        vel += direction * impulse

    # Spring-damper physics: F = -k*(x - anchor) - c*v
    spring_k = (6.5 + 4.0 * energy) / mass
    damping_c = 2.6 / math.sqrt(mass)
    accel = -spring_k * (x_pos - anchor) - damping_c * vel
    vel += accel * dt
    x_pos += vel * dt

    # Soft magnetic wall reflection (never clips harshly)
    if x_pos < 0.05:
        x_pos = 0.05 + (0.05 - x_pos) * 0.5
        vel = abs(vel) * 0.75
        direction = 1.0
    elif x_pos > 0.95:
        x_pos = 0.95 - (x_pos - 0.95) * 0.5
        vel = -abs(vel) * 0.75
        direction = -1.0

    # Snare creates chromatic aberration (prism split around the core)
    split_target = snare * disp_gain * 0.28
    if split_target > split:
        split += (split_target - split) * min(1.0, dt / 0.025)
    else:
        split *= math.exp(-dt / 0.22)

    st["pos"] = x_pos
    st["vel"] = vel
    st["dir"] = direction
    st["split"] = split

    afterglow = st.get("afterglow") or []
    if len(afterglow) != n:
        afterglow = [0.0] * n

    coords = u.axis_positions(pos_list)
    # Width breathes with sub-bass pulse
    sigma = base_width * (0.75 + 0.65 * pulse)
    speed_norm = min(1.0, abs(vel) / 2.2)

    px = []
    new_glow = []
    base_hue_t = key_shift + bar_phase * 0.20

    for i in range(n):
        fx = coords[i] if i < len(coords) else i / max(1, n - 1)

        # Main ferrofluid core + twin prism lobes when snare hits
        d_core = fx - x_pos
        d_left = fx - (x_pos - split)
        d_right = fx - (x_pos + split)

        w_core = math.exp(-(d_core * d_core) / (2.0 * sigma * sigma))
        sigma_lobe = max(0.08, sigma * 0.65)
        w_left = math.exp(-(d_left * d_left) / (2.0 * sigma_lobe * sigma_lobe))
        w_right = math.exp(-(d_right * d_right) / (2.0 * sigma_lobe * sigma_lobe))

        # Thermal trail (afterglow where the fluid just passed)
        instant_energy = max(w_core * (0.35 + 0.65 * max(pulse, kick)), (w_left + w_right) * 0.45 * snare)
        trail = max(instant_energy, afterglow[i] * math.exp(-dt / (0.28 + 0.15 * energy)))
        new_glow.append(trail)

        # Color composition: core is warm/hot, trail cools down through the palette, prism lobes shift +-0.33
        col_core = u.gradient(pal, base_hue_t + 0.12 * speed_norm)
        col_trail = u.gradient(pal, base_hue_t + 0.48)
        col = u.lerp(col_trail, col_core, w_core)

        if split > 0.02:
            col_l = u.gradient(pal, base_hue_t - 0.28)
            col_r = u.gradient(pal, base_hue_t + 0.32)
            lobe_mix = min(1.0, (w_left + w_right) * snare * disp_gain)
            prism_col = u.lerp(col_l, col_r, w_right / max(1e-4, w_left + w_right))
            col = u.lerp(col, prism_col, lobe_mix * 0.75)

        # Subtle high-frequency shimmer at the edges of the fluid
        rim = abs(d_core) / max(0.1, sigma)
        shimmer = bright * 0.18 * math.exp(-((rim - 1.2) ** 2) / 0.3) * (0.5 + 0.5 * math.sin(beat_phase * 12.566 + i * 2.1))

        v = min(1.0, 0.02 * energy + trail * 0.88 + shimmer)
        px.append(u.scale(col, v * scale))

    st["afterglow"] = new_glow
    return px
