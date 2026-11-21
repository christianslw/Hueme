import math

META = {
    "id": "dance_timecode",
    "name": "DANCE - Cinematic Timecode Show (7+3)",
    "description": "Überarbeitete 105-BPM-Lichtchoreografie für 7 obere Galerie-Spots und 3 untere Spots. Zeitcodiert, mit weicheren Bewegungen und Audio-Akzenten.",
}

PARAMS = [
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 0.85},
    {"key": "offset_s", "label": "Timecode Offset (sec)", "type": "range", "min": -2.0, "max": 2.0, "step": 0.05, "default": 0.0},
    {"key": "restart_show", "label": "Force Restart Timecode (Toggle)", "type": "bool", "default": False},
]


# Song structure retained from the original choreography.
BPM = 105.0
BEAT_S = 60.0 / BPM
BAR_S = BEAT_S * 4.0
DROP_TIME = 55.4
END_TIME = 285.8

C_AMBER = [255, 112, 28]
C_GOLD = [255, 174, 58]
C_RED = [255, 24, 58]
C_MAGENTA = [220, 20, 170]
C_PURPLE = [72, 18, 125]
C_BLUE = [18, 72, 210]
C_CYAN = [0, 205, 255]
C_ICE = [190, 235, 255]
C_WHITE = [255, 250, 238]
C_BLACK = [0, 0, 0]


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def _smooth(a, b, x):
    """Smoothstep interpolation from a to b."""
    if b <= a:
        return 1.0 if x >= b else 0.0
    q = _clamp((x - a) / (b - a))
    return q * q * (3.0 - 2.0 * q)


def _pulse(phase, sharpness=1.6):
    """Beat transient: strongest at the beat, then fades smoothly."""
    p = phase % 1.0
    return max(0.0, 1.0 - p) ** sharpness


def _gauss(x, center, width):
    width = max(0.001, width)
    return math.exp(-(((x - center) / width) ** 2))


def _mix(u, a, b, amount):
    return u.lerp(a, b, _clamp(amount))


def _color(u, color, level, master):
    return u.scale(color, _clamp(level) * master)


def _black(n):
    return [C_BLACK] * n


def setup(state):
    state["t0"] = -1.0
    state["last_alive"] = 0.0
    state["last_restart_flag"] = False


def render(ctx):
    n = max(1, int(ctx["n"]))
    now = float(ctx["now"])
    af = ctx.get("features") or {}
    params = ctx.get("params") or {}
    state = ctx.get("state") or {}
    env = ctx.get("env") or {}
    u = ctx["utils"]

    master = _clamp(float(params.get("brightness", 0.85)))
    offset_s = float(params.get("offset_s", 0.0))
    restart_flag = bool(params.get("restart_show", False))

    # A change of the toggle is an explicit manual restart.
    if restart_flag != state.get("last_restart_flag", False):
        state["last_restart_flag"] = restart_flag
        state["t0"] = now
        state["last_alive"] = now

    rms = max(0.0, float(af.get("rms", 0.0) or 0.0))
    energy = max(0.0, float(env.get("energy", 0.0) or 0.0))
    kick = _clamp(float(af.get("kick", 0.0) or 0.0))
    alive = bool(af.get("alive"))
    is_playing = alive and (rms > 0.003 or kick > 0.01 or energy > 0.015)

    show_track = af.get("show_track") or {}
    progress = show_track.get("progress_s")
    has_timecode = progress is not None and float(progress or 0.0) > 0.1

    if is_playing:
        if state.get("t0", -1.0) < 0.0 or now - float(state.get("last_alive", 0.0)) > 5.0:
            state["t0"] = now
        state["last_alive"] = now
    elif not has_timecode and now - float(state.get("last_alive", 0.0)) > 5.0:
        state["t0"] = -1.0
        return _black(n)

    if has_timecode:
        t = float(progress) + offset_s
    else:
        if state.get("t0", -1.0) < 0.0:
            return _black(n)
        t = now - float(state["t0"]) + offset_s

    t = max(0.0, t)
    beat_float = (t - DROP_TIME) / BEAT_S
    beat_index = math.floor(beat_float)
    beat_phase = beat_float - beat_index
    bar_pos = beat_index % 4
    bar_phase = (beat_float / 4.0) % 1.0
    beat = _pulse(beat_phase, 1.45)

    # The effect engine invokes the script once per fixture group:
    # n=7 is the upper gallery, n=3 is the lower depth layer.
    upper = n >= 5
    center = (n - 1) / 2.0
    max_dist = max(1.0, center)
    xs = [i / max(1, n - 1) if n > 1 else 0.5 for i in range(n)]
    ds = [abs(i - center) / max_dist for i in range(n)]

    # Audio features are deliberately accents, not the timing master.
    # The show remains coherent even if the analyser misses a transient.
    audio_hit = _clamp(max(kick, (rms - 0.025) * 5.0, (energy - 0.04) * 2.5))
    audio_glow = _clamp(energy * 1.8 + rms * 1.2)
    impact = max(beat * 0.72, audio_hit * 0.48)
    slow_breath = 0.5 + 0.5 * math.sin(t * 0.72)

    out = []

    # 1. INTRO — restrained amber emergence.
    if t < 4.5:
        p = _smooth(0.0, 4.5, t)
        for i, d in enumerate(ds):
            if upper:
                level = (0.025 + 0.20 * p) * _gauss(xs[i], 0.5, 0.16)
                out.append(_color(u, C_GOLD, level, master))
            else:
                level = 0.07 + 0.19 * p + 0.04 * slow_breath
                out.append(_color(u, _mix(u, C_PURPLE, C_AMBER, p * 0.65), level, master))
        return out

    # 2. RISER — a continuous, widening/narrowing light sweep.
    if t < 21.7:
        p = _smooth(4.5, 21.7, t)
        sweep = 0.5 + 0.43 * math.sin((t - 4.5) * (1.0 + p * 1.7))
        width = 0.25 - 0.09 * p
        base_col = _mix(u, C_AMBER, C_CYAN, p * 0.78)
        for i, x in enumerate(xs):
            if upper:
                beam = _gauss(x, sweep, width)
                secondary = _gauss(x, 1.0 - sweep, width * 1.4)
                level = 0.025 + 0.62 * beam + 0.12 * secondary
                out.append(_color(u, base_col, level, master))
            else:
                pulse = 0.5 + 0.5 * math.sin((t - 4.5) * 2.0 + i * 0.8)
                out.append(_color(u, C_PURPLE, 0.08 + 0.22 * p * pulse, master))
        return out

    # 3. GROOVE — alternating center kick and outer clap, softened tails.
    if t < 38.8:
        clap = bar_pos in (1, 3)
        for i, d in enumerate(ds):
            if upper:
                target = d > 0.58 if clap else d < 0.48
                color = C_CYAN if clap else C_RED
                level = (0.035 + (0.72 * beat if target else 0.025))
                # A little spill keeps the ceiling from looking mechanically binary.
                level += 0.08 * _gauss(d, 0.5 if clap else 0.0, 0.28) * (1.0 - beat_phase)
                out.append(_color(u, color if target else C_PURPLE, level, master))
            else:
                color = C_MAGENTA if clap else C_RED
                level = 0.12 + 0.46 * beat + 0.10 * audio_glow
                out.append(_color(u, color, level, master))
        return out

    # 4. BUILD — aperture closes toward the center; final blackout is exact.
    if t < 55.4:
        if t >= 54.82:
            return _black(n)
        p = _clamp((t - 38.8) / (54.82 - 38.8))
        aperture = 1.0 - 0.94 * _smooth(0.0, 1.0, p)
        gate = 0.18 + 0.82 * _pulse((beat_float * (1.0 + 3.0 * p)), 1.1)
        col = _mix(u, C_RED, C_ICE, p ** 1.25)
        for i, d in enumerate(ds):
            if upper:
                level = (0.10 + 0.78 * gate) if d <= aperture else 0.0
            else:
                level = (0.32 * (1.0 - p) * gate) if p < 0.78 else 0.0
            out.append(_color(u, col, level, master))
        return out

    # 5. DROP 1 — full-room impact with a moving color split.
    if t < 73.7:
        chop = t >= 64.5 and 0.43 <= beat_phase <= 0.72
        for i, d in enumerate(ds):
            if upper:
                wing = d >= 0.48
                active = (bar_pos % 2 == 0 and not wing) or (bar_pos % 2 == 1 and wing)
                if chop and i % 2:
                    col, level = C_MAGENTA, 0.78 + 0.12 * beat
                elif active:
                    col = C_CYAN if wing else C_RED
                    level = 0.56 + 0.40 * beat + 0.10 * audio_hit
                else:
                    col, level = C_BLUE, 0.055
                out.append(_color(u, col, level, master))
            else:
                col = C_MAGENTA if bar_pos % 2 else C_RED
                level = 0.38 + 0.42 * beat + 0.16 * audio_hit
                out.append(_color(u, col, level, master))
        return out

    # 6. VERSE 1 — slower pendulum, with a gentle moving halo.
    if t < 104.0:
        part_b = t >= 92.0
        pendulum = 0.5 - 0.40 * math.cos(2.0 * math.pi * bar_phase)
        width = 0.24 if part_b else 0.19
        for i, x in enumerate(xs):
            if upper:
                beam = _gauss(x, pendulum, width)
                halo = _gauss(x, 0.5 + 0.38 * math.sin(2.0 * math.pi * bar_phase), width * 1.8)
                col = _mix(u, C_PURPLE, C_MAGENTA if part_b else C_GOLD, beam)
                level = 0.035 + 0.56 * beam + 0.12 * halo
                if bar_pos in (1, 3) and beat_phase < 0.16 and i in (0, n - 1):
                    col, level = C_CYAN, 0.68
                out.append(_color(u, col, level, master))
            else:
                level = 0.13 + (0.17 if part_b else 0.08) * slow_breath
                out.append(_color(u, C_AMBER, level, master))
        return out

    # 7. GLITCH BREAK 1 — short alternating stabs and a clean vacuum.
    if t < 106.8:
        if t >= 106.25:
            return _black(n)
        eighth = int(math.floor(beat_float * 2.0))
        for i in range(n):
            on = (i + eighth) % 2 == 0
            out.append(_color(u, C_ICE, 0.82 if on else 0.0, master))
        return out

    # 8. CHORUS 1 — ring chase; vocal sustains bloom from center outward.
    if t < 124.8:
        scream = (110.8 <= t <= 114.2) or (119.5 <= t <= 123.2)
        for i, d in enumerate(ds):
            if scream:
                radius = _clamp((t - (110.8 if t < 115 else 119.5)) / 1.5)
                bloom = _gauss(d, radius, 0.42)
                col = _mix(u, C_GOLD, C_MAGENTA, d)
                level = 0.36 + 0.55 * bloom + 0.08 * beat
            else:
                ring = bar_pos
                spot_ring = min(3, int(round(d * 3.0)))
                active = spot_ring == ring
                col = C_CYAN if bar_pos in (1, 3) else C_RED
                level = 0.08 + (0.76 * beat if active else 0.025)
            out.append(_color(u, col if scream or active else C_PURPLE, level, master))
        return out

    # 9. POST-CHORUS — expanding wave travels from center to both edges.
    if t < 140.8:
        wave = (beat_float * 0.5) % 1.0
        for i, d in enumerate(ds):
            ring = _gauss(d, wave, 0.20) + _gauss(d, 1.0 - wave, 0.20)
            col = _mix(u, C_MAGENTA, C_CYAN, d)
            level = 0.07 + 0.62 * min(1.0, ring) + 0.15 * beat
            out.append(_color(u, col, min(1.0, level), master))
        return out

    # 10. VERSE 2 — sequential traveling pinspot, mirrored on the lower layer.
    if t < 172.0:
        if 169.0 <= t <= 171.5:
            flash = 1.0 - _smooth(169.0, 171.5, t)
            return [_color(u, C_RED, 0.22 + 0.72 * flash, master) for _ in range(n)]
        step = beat_index % n
        for i in range(n):
            dist = abs(i - step)
            if dist == 0:
                col, level = (C_GOLD if bar_pos % 2 == 0 else C_MAGENTA), 0.34 + 0.62 * beat
            elif dist == 1:
                col, level = C_PURPLE, 0.15 + 0.08 * beat
            else:
                col, level = C_BLUE, 0.025
            out.append(_color(u, col, level, master))
        return out

    # 11. GLITCH BREAK 2 — cyan polarity switch, then blackout.
    if t < 174.8:
        if t >= 174.2:
            return _black(n)
        eighth = int(math.floor(beat_float * 2.0))
        for i in range(n):
            on = (i + eighth) % 2 == 0
            out.append(_color(u, C_ICE if on else C_CYAN, 0.86 if on else 0.0, master))
        return out

    # 12. CHORUS 2 — stronger alternating blocks and two vocal blooms.
    if t < 208.5:
        scream = (178.8 <= t <= 182.2) or (187.5 <= t <= 191.2)
        for i, d in enumerate(ds):
            if scream:
                col = _mix(u, C_GOLD, C_CYAN, d)
                level = 0.62 + 0.30 * (0.5 + 0.5 * math.sin(t * 2.1 - d * 2.0))
            else:
                wing = d >= 0.48
                active = (bar_pos % 2 == 0 and not wing) or (bar_pos % 2 == 1 and wing)
                col = C_MAGENTA if wing else C_RED
                level = 0.07 + (0.78 * beat if active else 0.025)
            out.append(_color(u, col, level, master))
        return out

    # 13. BRIDGE — near-dark ambient section with slow asymmetric breathing.
    if t < 225.7:
        swell = 0.5 + 0.5 * math.sin((t - 208.5) * 0.82)
        for i, d in enumerate(ds):
            if upper:
                if i in (1, n - 2):
                    level = 0.07 + 0.28 * swell
                    out.append(_color(u, C_GOLD, level, master))
                else:
                    out.append(C_BLACK)
            else:
                level = 0.08 + 0.20 * swell
                out.append(_color(u, _mix(u, C_BLUE, C_MAGENTA, 0.35), level, master))
        return out

    # 14. FINAL BUILD — widening stack, then rapid compression to center.
    if t < 242.8:
        if t >= 242.1:
            return _black(n)
        p = _clamp((t - 225.7) / (242.1 - 225.7))
        if p < 0.52:
            aperture = _smooth(0.0, 0.52, p)
        else:
            aperture = 1.0 - _smooth(0.52, 1.0, p) * 0.96
        subdivisions = 2.0 if p < 0.65 else 4.0
        gate = 0.20 + 0.80 * _pulse(beat_phase * subdivisions, 1.1)
        col = _mix(u, C_GOLD, C_ICE, p ** 1.35)
        for d in ds:
            level = (0.16 + 0.82 * gate) if d <= aperture else 0.0
            out.append(_color(u, col, level, master))
        return out

    # 15. FINALE — alternating odd/even grid plus a fast synth beam sweep.
    if t < 277.1:
        solo = t >= 259.8
        solo_pos = 0.5 + 0.47 * math.sin((t - 259.8) * 4.6) if solo else 0.5
        for i, x in enumerate(xs):
            d = ds[i]
            if upper:
                grid_on = (i + beat_index) % 2 == 0
                col = C_RED if bar_pos % 2 == 0 else C_CYAN
                level = 0.55 + 0.40 * beat if grid_on else 0.045
                if solo:
                    beam = _gauss(x, solo_pos, 0.17)
                    if beam > 0.08:
                        col = _mix(u, col, C_WHITE, beam)
                        level = max(level, 0.25 + 0.75 * beam)
                out.append(_color(u, col, level, master))
            else:
                col = C_MAGENTA if bar_pos % 2 == 0 else C_GOLD
                level = 0.40 + 0.48 * beat + 0.08 * audio_glow
                out.append(_color(u, col, level, master))
        return out

    # 16. OUTRO — isolated pinspot hits, no lingering full-room wash.
    if t < END_TIME:
        active = beat_index % n
        stab = _pulse(beat_phase, 2.2)
        for i in range(n):
            if upper and i == active:
                out.append(_color(u, C_GOLD, stab * 0.95, master))
            elif not upper and bar_pos == 0 and i == 1:
                out.append(_color(u, C_RED, stab * 0.55, master))
            else:
                out.append(C_BLACK)
        return out

    return _black(n)
