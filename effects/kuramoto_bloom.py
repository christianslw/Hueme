import math
import colorsys

META = {
    "id": "kuramoto_bloom",
    "name": "Kuramoto Swarm (Bio-Sync)",
    "description": "Each spot is an autonomous oscillator that drifts organically in quiet passages and phase-locks into a unified wave when the groove locks in.",
}

PARAMS = [
    {
        "key": "palette",
        "label": "Color World",
        "type": "select",
        "default": "synth",
        "options": [
            ["synth", "Velvet Synth (Teal / Magenta)"],
            ["ember", "Amber Forge (1800K / Crimson)"],
            ["moonlight", "Deep Ocean (Indigo / Cyan)"],
            ["sunset", "Horizon (Rose / Gold)"],
            ["ice", "Nordic Aurora (Glacier / Emerald)"],
            ["acid", "Bioluminescent (Lime / Violet)"],
        ],
    },
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.05, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "coupling", "label": "Groove Lock Strength", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 0.70},
    {"key": "spatial_wave", "label": "Spatial Wave Lag", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.45},
    {"key": "contrast", "label": "Negative Space (Contrast)", "type": "range", "min": 1.0, "max": 4.0, "step": 0.2, "default": 2.2},
]


def setup(state):
    state["phases"] = []
    state["sparks"] = []
    state["travel_wave"] = 0.0
    state["pal_pos"] = 0.0


def render(ctx):
    n = ctx["n"]
    dt = max(0.001, min(0.1, float(ctx["dt"])))
    pos = ctx.get("world_positions") or ctx["positions"]
    scene = ctx.get("scene") or {}
    env = ctx["env"]
    beat = ctx["beat"]
    conf = (ctx["features"].get("conf") or {})
    harmony = ctx.get("harmony") or {}
    section = ctx.get("section") or {}
    p = ctx["params"]
    st = ctx["state"]
    u = ctx["utils"]

    scale = float(p.get("brightness", 1.0))
    coupling_gain = float(p.get("coupling", 0.70))
    wave_lag = float(p.get("spatial_wave", 0.45))
    contrast = float(p.get("contrast", 2.2))
    pal = p.get("palette", "synth")

    pulse = float(env.get("pulse", 0.0))
    kick = float(env.get("kick", 0.0))
    snare = float(env.get("snare", 0.0))
    hat = float(env.get("hat", 0.0))
    energy = float(env.get("energy", 0.0))
    bpm = max(60.0, float(ctx["features"].get("bpm") or 120.0))
    bpm_conf = float(conf.get("bpm", 0.0))
    grid_conf = float(conf.get("grid", 0.0))
    beat_phase = float(beat.get("phase", 0.0))
    bar_phase = float(beat.get("bar_phase", 0.0))
    key_shift = (int(harmony.get("key", 0)) % 12) / 12.0

    # Initialize per-fixture oscillators with golden-ratio detuning
    phases = st.get("phases") or []
    sparks = st.get("sparks") or []
    if len(phases) != n or len(sparks) != n:
        phases = [(i * 0.61803398875) % 1.0 for i in range(n)]
        sparks = [0.0] * n

    coords = u.axis_positions(pos)

    # Coupling strength K scales with rhythm confidence and bass energy
    lock_strength = coupling_gain * (0.25 + 0.75 * max(bpm_conf, grid_conf, pulse))
    if section.get("drop"):
        lock_strength = min(1.5, lock_strength * 1.4)

    # Smooth palette drift locked to musical bars + harmonic key
    pal_target = key_shift + bar_phase * 0.25 + energy * 0.15
    pal_pos = float(st.get("pal_pos", pal_target))
    pal_pos += (pal_target - pal_pos) * min(1.0, dt * 3.0)
    st["pal_pos"] = pal_pos

    # Kick launches a physical phase-compression pulse down the room axis
    travel = float(st.get("travel_wave", 1.5))
    if ctx["features"].get("kick") and (ctx["features"].get("aligned_kick", True) or bpm_conf < 0.3):
        travel = 0.0
    else:
        travel += dt * (1.8 + 1.2 * energy)
    st["travel_wave"] = travel

    base_freq = (bpm / 60.0) * 0.5  # Half-beat breathing cycle so it never strobes
    new_phases = []
    new_sparks = []
    px = []

    for i in range(n):
        x = coords[i] if i < len(coords) else i / max(1, n - 1)
        # Natural detuning per spot (-12% to +12%) when uncoupled
        detune = 1.0 + 0.12 * math.sin(i * 2.39996) * (1.0 - 0.8 * lock_strength)
        phi = phases[i] + dt * base_freq * detune

        # Target phase forms a graceful travelling wave along the ceiling spots
        target_phi = (beat_phase * 0.5 - x * wave_lag) % 1.0

        # Kuramoto sine coupling towards the beat-wave + neighbor attraction
        prev_phi = phases[(i - 1) % n]
        next_phi = phases[(i + 1) % n]
        neighbor_pull = 0.5 * (math.sin(2.0 * math.pi * (prev_phi - phi)) + math.sin(2.0 * math.pi * (next_phi - phi)))
        beat_pull = math.sin(2.0 * math.pi * (target_phi - phi))

        phi = (phi + dt * (lock_strength * 2.8 * beat_pull + lock_strength * 0.9 * neighbor_pull)) % 1.0
        new_phases.append(phi)

        # Raised cosine wave with adjustable power exponent -> creates true dark space between spots!
        osc = (0.5 + 0.5 * math.cos(2.0 * math.pi * phi)) ** contrast

        # Travelling kick wave envelope along the room axis
        dist_to_wave = abs(x - travel)
        wave_hit = math.exp(-((dist_to_wave / 0.18) ** 2)) * kick

        # Deterministic hi-hat sparkle on single spots
        sp = sparks[i] * math.exp(-dt / 0.14)
        if hat > 0.25 and ((int(beat.get("count", 0)) * 7 + i * 13) % n) == i:
            sp = max(sp, hat * 0.75)
        new_sparks.append(sp)

        # Compose brightness: deep breathing floor + oscillator + kick wave + snare edge lift
        edge_weight = abs(x - 0.5) * 2.0
        v = (0.04 + (0.28 + 0.45 * pulse) * osc) + 0.55 * wave_hit + 0.30 * snare * edge_weight + sp
        v = min(1.0, max(0.0, v))

        # Color: primary gradient shifted by oscillator phase + warm highlight on kick crest
        col = u.gradient(pal, pal_pos + x * 0.28 + osc * 0.18)
        if wave_hit > 0.05:
            accent_col = u.gradient(pal, pal_pos + 0.42)
            col = u.lerp(col, accent_col, min(1.0, wave_hit * 0.75))
        if sp > 0.1:
            col = u.lerp(col, (255, 235, 200), min(0.65, sp * 0.6))

        px.append(u.scale(col, v * scale))

    st["phases"] = new_phases
    st["sparks"] = new_sparks
    return px
