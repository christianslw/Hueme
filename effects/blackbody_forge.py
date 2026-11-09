import math

META = {
    "id": "blackbody_forge",
    "name": "Blackbody Forge (1800K Thermal Physics)",
    "description": "Thermodynamic simulation: spots heat up on kicks/bass from deep 1800K candle-amber to incandescent gold, diffusing heat to neighbors and cooling by radiative physics.",
}

PARAMS = [
    {"key": "base_kelvin", "label": "Idle Ember (Kelvin)", "type": "range", "min": 1600, "max": 2600, "step": 50, "default": 1850},
    {"key": "peak_kelvin", "label": "Peak Flash (Kelvin)", "type": "range", "min": 2800, "max": 5500, "step": 100, "default": 3800},
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "diffusion", "label": "Heat Conduction (Flow)", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 0.60},
    {"key": "cooling", "label": "Radiative Cooling Speed", "type": "range", "min": 0.5, "max": 3.0, "step": 0.1, "default": 1.3},
    {"key": "indigo_shadow", "label": "Deep Indigo Shadows", "type": "bool", "default": True},
]


def _thermal_rgb(temp_norm, k_min, k_max, indigo_shadow):
    """Map normalized temperature (0..1) to a rich blackbody curve with deep 1800K amber low-end."""
    t = max(0.0, min(1.0, temp_norm))
    kelvin = k_min + (k_max - k_min) * (t ** 1.15)
    k = kelvin / 100.0

    # Planckian locus approximation tuned for rich amber below 2200K
    r = 255.0
    if k <= 19.0:
        g = max(28.0, min(255.0, 48.0 + (k - 15.0) * 18.5))
        b = 0.0
    else:
        g = max(0.0, min(255.0, 99.4708 * math.log(max(1.0, k)) - 161.1195))
        b = max(0.0, min(255.0, 138.5177 * math.log(max(1.0, k - 10.0)) - 305.0448))

    # Optional twilight indigo undertone in the coolest embers for high-end architectural contrast
    if indigo_shadow and t < 0.22:
        shadow_mix = (0.22 - t) / 0.22
        r = r * (1.0 - 0.45 * shadow_mix) + 35.0 * shadow_mix
        g = g * (1.0 - 0.55 * shadow_mix) + 18.0 * shadow_mix
        b = b + 85.0 * (shadow_mix ** 1.5)

    return (int(max(0, min(255, r))), int(max(0, min(255, g))), int(max(0, min(255, b))))


def setup(state):
    state["temps"] = []
    state["heat_idx"] = 0


def render(ctx):
    n = ctx["n"]
    dt = max(0.001, min(0.1, float(ctx["dt"])))
    pos = ctx.get("world_positions") or ctx["positions"]
    env = ctx["env"]
    beat = ctx["beat"]
    p = ctx["params"]
    st = ctx["state"]
    u = ctx["utils"]

    k_min = float(p.get("base_kelvin", 1850))
    k_max = max(k_min + 400.0, float(p.get("peak_kelvin", 3800)))
    scale = float(p.get("brightness", 1.0))
    diff_rate = float(p.get("diffusion", 0.60))
    cool_rate = float(p.get("cooling", 1.3))
    indigo = bool(p.get("indigo_shadow", True))

    pulse = float(env.get("pulse", 0.0))
    kick = float(env.get("kick", 0.0))
    snare = float(env.get("snare", 0.0))
    hat = float(env.get("hat", 0.0))
    energy = float(env.get("energy", 0.0))
    bar_phase = float(beat.get("bar_phase", 0.0))

    temps = st.get("temps") or []
    if len(temps) != n:
        temps = [0.15] * n

    coords = u.axis_positions(pos)

    # 1. Heat injection: Kicks heat the center/rotating focal spot; bass warms the whole bed
    focal_pos = 0.5 + 0.35 * math.sin(2.0 * math.pi * bar_phase)
    if ctx["features"].get("kick") and kick > 0.15:
        for i in range(n):
            x = coords[i] if i < len(coords) else i / max(1, n - 1)
            dist = abs(x - focal_pos)
            inject = math.exp(-((dist / 0.24) ** 2)) * (0.45 + 0.55 * kick)
            temps[i] = min(1.0, max(temps[i], inject))

    # Snare heats the outer architectural edges
    if snare > 0.25:
        for i in range(n):
            x = coords[i] if i < len(coords) else i / max(1, n - 1)
            edge = abs(x - 0.5) * 2.0
            temps[i] = min(1.0, max(temps[i], edge * snare * 0.78))

    # 2. 1D Heat Equation (conduction between adjacent spots) + radiative cooling
    new_temps = []
    ambient_temp = 0.08 + 0.26 * pulse + 0.10 * energy
    k_cond = diff_rate * 4.5

    for i in range(n):
        t_cur = temps[i]
        t_left = temps[i - 1] if i > 0 else t_cur
        t_right = temps[i + 1] if i < n - 1 else t_cur

        # Laplacian heat diffusion between neighbor spots
        laplacian = (t_left + t_right - 2.0 * t_cur)
        t_next = t_cur + k_cond * laplacian * dt

        # Non-linear Stefan-Boltzmann-like cooling: hotter spots cool faster initially, embers linger!
        excess = max(0.0, t_next - ambient_temp)
        cooling_loss = cool_rate * (0.65 * excess + 1.45 * (excess ** 2.2)) * dt
        t_next = max(ambient_temp, t_next - cooling_loss)

        # Subtle hi-hat ember crackle
        if hat > 0.3 and ((i + int(beat.get("count", 0))) % 3 == 0):
            t_next = min(1.0, t_next + hat * 0.16)

        new_temps.append(min(1.0, max(0.0, t_next)))

    st["temps"] = new_temps

    # 3. Map temperature to Planckian color and radiant brightness
    px = []
    for i in range(n):
        t = new_temps[i]
        col = _thermal_rgb(t, k_min, k_max, indigo)
        # Brightness scales smoothly from a warm 6% idle glow up to 100%
        v = 0.06 + 0.94 * (t ** 1.35)
        px.append(u.scale(col, min(1.0, v) * scale))

    return px
