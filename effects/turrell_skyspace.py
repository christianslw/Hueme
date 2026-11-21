import math

META = {
    "id": "turrell_skyspace",
    "name": "Turrell Skyspace (3D Horizon Drift)",
    "description": "Ultra-smooth architectural color-field installation across all zones: a luminous horizon seam drifts through 3D room space at any angle.",
}

PARAMS = [
    {"key": "sky_color", "label": "Upper Field (Sky)", "type": "color", "default": [22, 12, 65]},
    {"key": "seam_color", "label": "Horizon Seam (Glow)", "type": "color", "default": [255, 115, 25]},
    {"key": "earth_color", "label": "Lower Field (Earth)", "type": "color", "default": [140, 20, 45]},
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 0.90},
    {"key": "drift_speed", "label": "Horizon Drift Speed", "type": "range", "min": 0.02, "max": 0.8, "step": 0.02, "default": 0.14},
    {"key": "angle", "label": "Spatial Axis Angle (deg)", "type": "range", "min": 0, "max": 360, "step": 15, "default": 60},
    {"key": "seam_width", "label": "Horizon Seam Width", "type": "range", "min": 0.08, "max": 0.60, "step": 0.02, "default": 0.24},
    {"key": "contrast", "label": "Depth / Shadow Contrast", "type": "range", "min": 0.0, "max": 0.9, "step": 0.05, "default": 0.55},
    {"key": "audio_mod", "label": "Subtle Audio Breath", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.20},
]


def _p3(item):
    if item is None:
        return (0.5, 0.5, 0.5)
    vals = list(item) + [0.5, 0.5, 0.5]
    return (float(vals[0]), float(vals[1]), float(vals[2]))


def render(ctx):
    n = max(1, int(ctx["n"]))
    now = float(ctx["now"])
    pos = ctx.get("world_positions") or ctx["positions"]
    scene = ctx.get("scene") or {}
    lo = scene.get("min") or [0.0, 0.0, 0.0]
    hi = scene.get("max") or [1.0, 1.0, 1.0]
    env = ctx.get("env") or {}
    p = ctx["params"]
    u = ctx["utils"]

    master = float(p.get("brightness", 0.90))
    speed = float(p.get("drift_speed", 0.14))
    angle_rad = math.radians(float(p.get("angle", 60)))
    seam_w = max(0.05, float(p.get("seam_width", 0.24)))
    contrast = float(p.get("contrast", 0.55))
    audio_mod = float(p.get("audio_mod", 0.20))

    c_sky = p.get("sky_color", [22, 12, 65])
    c_seam = p.get("seam_color", [255, 115, 25])
    c_earth = p.get("earth_color", [140, 20, 45])

    pulse = float(env.get("pulse", 0.0)) * audio_mod
    harmonic = float(env.get("harmonic", pulse)) * audio_mod

    # Global 3D room bounding normalization so Main Zone and Zone 2 form one canvas
    sx = max(0.5, float(hi[0]) - float(lo[0]))
    sy = max(0.5, float(hi[1]) - float(lo[1]))
    sz = max(0.5, float(hi[2]) - float(lo[2]))

    dx = math.cos(angle_rad)
    dy = math.sin(angle_rad)

    # Slowly breathing horizon position (-0.65 .. +0.65 in projected room space)
    phase = now * speed
    horizon_pos = 0.52 * math.sin(phase) + 0.15 * math.sin(phase * 0.37) + 0.12 * harmonic
    seam_w_eff = seam_w * (1.0 + 0.35 * pulse)

    px = []
    for i in range(n):
        wx, wy, wz = _p3(pos[i] if i < len(pos) else None)
        nx = (wx - float(lo[0])) / sx - 0.5
        ny = (wy - float(lo[1])) / sy - 0.5
        nz = (wz - float(lo[2])) / sz - 0.5

        # Project 3D spot coordinate onto the rotatable horizon vector + Z elevation tilt
        proj = nx * dx + ny * dy + nz * 0.25
        dist = proj - horizon_pos

        # Smoothstep transition between Earth field (-1) and Sky field (+1)
        t_field = u.smoothstep(-seam_w_eff * 1.6, seam_w_eff * 1.6, dist)
        base_col = u.lerp(c_earth, c_sky, t_field)

        # Gaussian luminous horizon seam right at the boundary (dist == 0)
        seam_glow = math.exp(-((dist / seam_w_eff) ** 2))
        col = u.lerp(base_col, c_seam, seam_glow ** 0.85)

        # Brightness sculpts deep architectural shadows away from the horizon seam
        base_bri = (1.0 - contrast * 0.78) + (contrast * 0.78) * seam_glow
        bri = min(1.0, base_bri + 0.15 * pulse * seam_glow)

        px.append(u.scale(col, bri * master))

    return px
