import math

META = {
    "id": "slayyyter_dance",
    "name": "SLAYYYTER - DANCE... (Supernova Live Show)",
    "description": (
        "Beat-exact 112.5 BPM show for 7 ceiling spots + 3 low spots, built on the real "
        "8-bar phrase grid of Slayyyter's DANCE... (4:48). Cold open, mirror rehearsal, "
        "paparazzi chorus, mirrorball, runway, void breakdown and a supernova encore."
    ),
}

PARAMS = [
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 0.9},
    {"key": "offset_s", "label": "Timecode Offset (sec)", "type": "range", "min": -2.0, "max": 2.0, "step": 0.05, "default": 0.0},
    {"key": "restart_show", "label": "Force Restart Timecode (Toggle)", "type": "bool", "default": False},
    {"key": "primary", "label": "Pop Red / Hot Pink", "type": "color", "default": [255, 36, 82]},
    {"key": "secondary", "label": "Club Cyan", "type": "color", "default": [0, 205, 255]},
    {"key": "warm", "label": "Stage Amber", "type": "color", "default": [255, 154, 44]},
    {"key": "flash", "label": "Paparazzi / Flash Amount", "type": "range", "min": 0.0, "max": 1.0, "step": 0.05, "default": 0.8},
]

# The track is a rock-steady 112.5 BPM 4/4 grid.  Phrase boundaries are exact
# multiples of 32 beats (8 bars); the drop lands on beat 104.
BPM = 112.5
BEAT_S = 60.0 / BPM
END_BEAT = 536.0

# (start beat, act). The final entry is the end marker.
SECTIONS = (
    (0.0, "cold"),
    (8.0, "rehearsal"),
    (40.0, "groove"),
    (72.0, "build"),
    (104.0, "drop1"),
    (136.0, "mirrorball"),
    (168.0, "runway"),
    (200.0, "chorus"),
    (232.0, "float"),
    (264.0, "verse"),
    (296.0, "rise"),
    (328.0, "chorus2"),
    (360.0, "house"),
    (392.0, "break"),
    (424.0, "build2"),
    (456.0, "encore"),
    (488.0, "solo"),
    (520.0, "outro"),
    (536.0, "end"),
)

C_BLACK = (0, 0, 0)
C_WHITE = (255, 246, 232)
C_ICE = (188, 232, 255)
C_CYAN = (0, 205, 255)
C_MAGENTA = (235, 20, 150)
C_PURPLE = (84, 26, 150)
C_INDIGO = (18, 18, 72)
C_RED = (255, 28, 55)
C_AMBER = (255, 116, 18)
C_GOLD = (255, 188, 76)


def setup(state):
    state["t0"] = -1.0
    state["last_alive"] = 0.0
    state["last_restart_flag"] = False


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def _smooth(a, b, x):
    if b <= a:
        return 1.0 if x >= b else 0.0
    q = _clamp((x - a) / (b - a))
    return q * q * (3.0 - 2.0 * q)


def _pulse(phase, sharp=1.5):
    p = phase % 1.0
    return max(0.0, 1.0 - p) ** sharp


def _gauss(x, center, width):
    w = max(0.001, float(width))
    return math.exp(-(((x - center) / w) ** 2))


def _mix(u, a, b, t):
    return u.lerp(a, b, _clamp(t))


def _rgb(params, key, default):
    c = params.get(key)
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        return (int(c[0]), int(c[1]), int(c[2]))
    return default


def _hash01(n):
    """Deterministic pseudo-random 0..1 (safe in the effect sandbox)."""
    v = math.sin(float(n) * 12.9898 + 78.233) * 43758.5453
    return v - math.floor(v)


def _section(beat):
    name, start = SECTIONS[0]
    for i in range(len(SECTIONS) - 1):
        if SECTIONS[i][0] <= beat < SECTIONS[i + 1][0]:
            return SECTIONS[i][1], SECTIONS[i][0], SECTIONS[i + 1][0]
    return "end", SECTIONS[-1][0], SECTIONS[-1][0] + 32.0


def _black(n):
    return [C_BLACK] * n


def _comet(x, beat, period, width=0.09):
    """A moving beam with two time-lagged tails (cheap physical motion blur)."""
    def pos(b):
        return 0.5 + 0.46 * math.sin(2.0 * math.pi * b / period)

    p0 = pos(beat)
    p1 = pos(beat - 0.25)
    p2 = pos(beat - 0.50)
    return (
        _gauss(x, p0, width)
        + 0.45 * _gauss(x, p1, width * 1.15)
        + 0.20 * _gauss(x, p2, width * 1.35),
        p0,
    )


def render(ctx):
    n = max(1, int(ctx["n"]))
    now = float(ctx["now"])
    u = ctx["utils"]
    af = ctx.get("features") or {}
    params = ctx.get("params") or {}
    state = ctx.get("state") or {}
    env = ctx.get("env") or af.get("env") or {}

    master = _clamp(float(params.get("brightness", 0.9)), 0.05, 1.0)
    offset_s = float(params.get("offset_s", 0.0))
    restart_flag = bool(params.get("restart_show", False))
    flash_amt = _clamp(float(params.get("flash", 0.8)))
    c_primary = _rgb(params, "primary", C_RED)
    c_secondary = _rgb(params, "secondary", C_CYAN)
    c_warm = _rgb(params, "warm", C_AMBER)

    # Manual reset toggle in the UI.
    if restart_flag != state.get("last_restart_flag", False):
        state["last_restart_flag"] = restart_flag
        state["t0"] = now
        state["last_alive"] = now

    show_track = af.get("show_track") or {}
    progress = show_track.get("progress_s")
    has_timecode = progress is not None and float(progress or 0.0) > 0.1

    rms = max(0.0, float(af.get("rms", 0.0) or 0.0))
    energy = max(0.0, float(env.get("energy", 0.0) or 0.0))
    kick_bool = bool(af.get("kick"))
    alive = bool(af.get("alive"))
    is_playing = alive and (rms > 0.003 or kick_bool or energy > 0.015 or has_timecode)

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

    if t <= 0.0 or t >= END_BEAT * BEAT_S:
        return _black(n)

    beat = t / BEAT_S
    beat_idx = int(math.floor(beat))
    beat_phase = beat - beat_idx
    bar_pos = beat_idx % 4
    act, act_start, _ = _section(beat)
    lb = beat - act_start
    upper = n >= 5

    positions = ctx.get("positions") or []
    if len(positions) == n:
        xs = u.axis_positions(positions)
    else:
        xs = [(i / max(1, n - 1)) if n > 1 else 0.5 for i in range(n)]
    if not xs:
        xs = [0.5] * n
    ds = [abs(x - 0.5) * 2.0 for x in xs]

    kick = _clamp(env.get("kick", af.get("kick_env", 0.0)))
    snare = _clamp(env.get("snare", af.get("snare_env", 0.0)))
    pulse = _clamp(env.get("pulse", af.get("bass", 0.0)))
    bright = _clamp(env.get("bright", af.get("highs", 0.0)))
    beat_dec = _pulse(beat_phase, 1.45)

    def emit(col, lvl):
        return u.scale(col, _clamp(lvl) * master)

    # ------------------------------------------------------------------
    # 1. COLD OPEN - a single work light comes up in the centre.
    # ------------------------------------------------------------------
    if act == "cold":
        p = _smooth(0.0, 8.0, beat)
        breath = 0.5 + 0.5 * math.sin(t * 1.15)
        col = _mix(u, C_INDIGO, C_GOLD, 0.12 + 0.78 * p)
        out = []
        for i in range(n):
            b = _gauss(xs[i], 0.5, 0.115)
            halo = _gauss(xs[i], 0.5, 0.34)
            lvl = 0.006 + p * (0.40 * b + 0.030 * halo) + 0.020 * p * breath * b
            out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 2. MIRROR REHEARSAL - two warm beams drift in opposition.
    # ------------------------------------------------------------------
    if act == "rehearsal":
        pos = 0.5 + 0.36 * math.cos(lb * math.pi / 16.0)
        slow = 0.5 + 0.5 * math.sin(t * 0.85)
        ca = _mix(u, c_warm, C_WHITE, 0.10 + 0.16 * slow)
        cb = _mix(u, C_MAGENTA, C_PURPLE, 0.42)
        out = []
        for i in range(n):
            if upper:
                b1 = _gauss(xs[i], pos, 0.16)
                b2 = _gauss(xs[i], 1.0 - pos, 0.16)
                base = 0.012 + 0.035 * slow * _gauss(xs[i], 0.5, 0.55)
                lvl = base + 0.52 * max(b1, b2)
                col = _mix(u, ca, cb, _clamp(b2 / max(0.01, b1 + b2)))
                if _hash01(i * 31 + beat_idx * 17 + 5) > 0.78 and beat_phase < 0.45:
                    glint = max(0.0, 1.0 - beat_phase * 2.2)
                    col = _mix(u, col, C_ICE, 0.75)
                    lvl = max(lvl, 0.40 * glint)
                out.append(emit(col, lvl))
            else:
                swell = 0.5 + 0.5 * math.sin(lb * math.pi / 8.0 + i * 0.9)
                lvl = 0.055 + 0.10 * swell + 0.045 * pulse + 0.05 * (1.0 - ds[i])
                col = _mix(u, C_PURPLE, c_warm, 0.22 + 0.30 * swell)
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 3. FIRST GROOVE - a slow rotating cross-fan with a central pulse.
    # ------------------------------------------------------------------
    if act == "groove":
        sweep = 0.5 + 0.42 * math.sin(lb * math.pi / 4.0)
        col_base = _mix(u, c_warm, C_MAGENTA, 0.40 + 0.32 * math.sin(lb * 0.35))
        out = []
        for i in range(n):
            if upper:
                b1 = _gauss(xs[i], sweep, 0.12)
                b2 = _gauss(xs[i], 1.0 - sweep, 0.12)
                centre = _gauss(xs[i], 0.5, 0.22)
                lvl = 0.018 + 0.50 * max(b1, b2) + 0.10 * centre * beat_dec + 0.035 * pulse
                col = _mix(u, col_base, C_GOLD, 0.16 + 0.26 * centre)
                if bar_pos == 3 and (i == 0 or i == n - 1):
                    lvl += 0.10 * beat_dec
                out.append(emit(col, lvl))
            else:
                wave = 0.5 + 0.5 * math.sin(lb * math.pi / 2.0 + i * 0.8)
                lvl = 0.075 + 0.22 * pulse + 0.16 * kick + 0.05 * energy
                col = _mix(u, C_PURPLE, C_MAGENTA, 0.32 + 0.42 * wave)
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 4. RED-CARPET BUILD - beams converge from the ends; vacuum before impact.
    # ------------------------------------------------------------------
    if act == "build":
        p = _clamp(lb / 32.0)
        if lb >= 30.0:
            return _black(n)
        pos_l = 0.02 + 0.48 * p
        pos_r = 0.98 - 0.48 * p
        width = 0.17 - 0.085 * p
        sub = 1 + int(3.0 * p)
        gate = _pulse(beat * sub, 1.35)
        col = _mix(u, c_primary, C_ICE, p ** 1.25)
        if p > 0.72:
            col = _mix(u, col, C_WHITE, (p - 0.72) * 2.2)
        out = []
        for i in range(n):
            if upper:
                b = max(_gauss(xs[i], pos_l, width), _gauss(xs[i], pos_r, width))
                centre = _gauss(xs[i], 0.5, 0.20)
                lvl = 0.018 + 0.70 * b * (0.24 + 0.76 * gate) + 0.10 * centre * p * gate
                out.append(emit(col, lvl))
            else:
                b = max(_gauss(xs[i], pos_l, 0.24), _gauss(xs[i], pos_r, 0.24))
                lvl = 0.035 + 0.40 * b * (0.30 + 0.70 * gate)
                if p > 0.82:
                    lvl *= max(0.0, (1.0 - p) / 0.18)
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 5. DROP 1 - white-hot impact, then a centre-born shockwave and moving head.
    # ------------------------------------------------------------------
    if act == "drop1":
        flash = max(0.0, 1.0 - lb * 2.8)
        ring_r = (lb * 0.5) % 1.0
        head = 0.5 + 0.42 * math.sin(lb * math.pi / 8.0)
        out = []
        for i in range(n):
            if upper:
                wave = max(_gauss(ds[i], ring_r, 0.22), 0.55 * _gauss(ds[i], 1.0 - ring_r, 0.22))
                hot = _gauss(xs[i], head, 0.16)
                col = _mix(u, C_MAGENTA, c_secondary, 0.5 + 0.5 * math.sin(lb * 0.55 + ds[i] * 2.0))
                if bar_pos % 2 == 1:
                    col = _mix(u, col, c_primary, 0.42)
                lvl = 0.035 + 0.56 * wave + 0.20 * hot + 0.16 * kick + 0.08 * snare
                if flash > 0.0:
                    col = _mix(u, col, C_WHITE, 0.88)
                    lvl = max(lvl, 0.88 * flash)
                out.append(emit(col, lvl))
            else:
                wave = 0.5 + 0.5 * math.sin(lb * math.pi / 2.0 + i * 1.3)
                lvl = 0.18 + 0.44 * kick + 0.12 * wave + 0.08 * snare
                col = _mix(u, c_warm, C_MAGENTA, 0.32 + 0.50 * wave)
                if flash > 0.0:
                    col = _mix(u, col, C_WHITE, 0.85)
                    lvl = max(lvl, 0.82 * flash)
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 6. MIRRORBALL - counter-rotating facets and deterministic glints.
    # ------------------------------------------------------------------
    if act == "mirrorball":
        rot = lb * 0.10
        out = []
        for i in range(n):
            if upper:
                a = 0.5 + 0.5 * math.cos(2.0 * math.pi * (xs[i] * 1.8 + rot))
                b = 0.5 + 0.5 * math.cos(2.0 * math.pi * (xs[i] * 1.8 - rot + 0.33))
                facet = _mix(u, C_CYAN, C_MAGENTA, 0.5 + 0.5 * math.sin(lb * 0.22))
                col = _mix(u, C_PURPLE, facet, 0.32 + 0.46 * a)
                lvl = 0.025 + 0.38 * a + 0.22 * b + 0.075 * pulse
                if _hash01(i * 53 + beat_idx * 7 + 2) > 0.70:
                    glint = max(0.0, 1.0 - beat_phase * 2.5)
                    col = _mix(u, col, C_ICE, 0.80)
                    lvl += 0.20 * glint
                out.append(emit(col, lvl))
            else:
                swirl = 0.5 + 0.5 * math.sin(lb * math.pi / 6.0 + i * 1.7)
                lvl = 0.060 + 0.13 * swirl + 0.11 * pulse + 0.08 * kick
                col = _mix(u, C_INDIGO, _mix(u, C_MAGENTA, C_CYAN, 0.42), 0.28 + 0.42 * swirl)
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 7. RUNWAY - chevron waves with snare-fired wings.
    # ------------------------------------------------------------------
    if act == "runway":
        d_phase = (lb * 0.5) % 1.0
        col_base = _mix(u, c_warm, _mix(u, C_MAGENTA, c_secondary, 0.42), 0.38 + 0.28 * math.sin(lb * 0.35))
        out = []
        for i in range(n):
            if upper:
                b = max(_gauss(ds[i], d_phase, 0.16), _gauss(ds[i], 1.0 - d_phase, 0.16))
                active = (bar_pos % 2 == 0 and ds[i] <= 0.52) or (bar_pos % 2 == 1 and ds[i] >= 0.48)
                lvl = 0.018 + 0.42 * b
                if active:
                    lvl += 0.23 * beat_dec
                else:
                    lvl *= 0.68
                col = col_base
                if snare > 0.30 and ds[i] >= 0.58:
                    lvl = max(lvl, 0.68 * snare)
                    col = _mix(u, col, C_WHITE, 0.45)
                out.append(emit(col, lvl))
            else:
                step = int(lb * 0.5) % n
                dist = abs(i - step)
                if dist == 0:
                    col = c_warm
                    lvl = 0.28 + 0.36 * beat_dec
                elif dist == 1:
                    col = C_MAGENTA
                    lvl = 0.10 + 0.12 * beat_dec
                else:
                    col = C_PURPLE
                    lvl = 0.025
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 8. PAPARAZZI CHORUS - full ceiling wave, camera flashes, vocal blooms.
    # ------------------------------------------------------------------
    if act == "chorus":
        scream = (110.7 <= t <= 114.2) or (119.4 <= t <= 123.2)
        out = []
        if scream:
            start = 110.7 if t < 115.0 else 119.4
            radius = _clamp((t - start) / 1.4)
            for i in range(n):
                if upper:
                    bloom = _gauss(ds[i], radius, 0.34) + 0.45 * _gauss(ds[i], 1.0 - radius, 0.34)
                    col = _mix(u, C_GOLD, C_MAGENTA, ds[i])
                    lvl = 0.20 + 0.64 * bloom + 0.08 * kick
                else:
                    bloom = 0.5 + 0.5 * math.sin(lb * math.pi / 2.0 + i * 0.7)
                    col = _mix(u, c_warm, C_MAGENTA, 0.35 + 0.35 * bloom)
                    lvl = 0.22 + 0.38 * kick + 0.16 * bloom
                out.append(emit(col, lvl))
            return out

        pos = 0.5 + 0.42 * math.sin(lb * math.pi / 6.0)
        for i in range(n):
            if upper:
                beam = _gauss(xs[i], pos, 0.13)
                col = _mix(u, c_primary, c_secondary, 0.5 + 0.5 * math.sin(lb * 0.30 + xs[i] * 2.0))
                lvl = 0.035 + 0.50 * beam + 0.17 * beat_dec
                r = _hash01(i * 17 + beat_idx * 19 + 3)
                if r > (0.82 - 0.12 * flash_amt) and beat_phase < 0.32:
                    fl = max(0.0, 1.0 - beat_phase * 3.0)
                    col = _mix(u, col, C_WHITE, 0.90)
                    lvl = max(lvl, 0.70 * fl * flash_amt)
                out.append(emit(col, lvl))
            else:
                wave = 0.5 + 0.5 * math.sin(lb * math.pi / 2.0 + i * 1.1)
                lvl = 0.18 + 0.36 * kick + 0.14 * wave + 0.06 * bright
                col = _mix(u, c_warm, C_MAGENTA, 0.30 + 0.48 * wave)
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 9. FLOATING POST-CHORUS - slow expanding rings, ice and violet.
    # ------------------------------------------------------------------
    if act == "float":
        r = (lb / 16.0) % 1.0
        out = []
        for i in range(n):
            if upper:
                ring = max(_gauss(ds[i], r, 0.24), 0.60 * _gauss(ds[i], 1.0 - r, 0.24))
                sway = 0.5 + 0.5 * math.sin(lb * math.pi / 8.0 + xs[i] * 3.0)
                col = _mix(u, C_ICE, _mix(u, C_PURPLE, C_CYAN, 0.35), 0.32 + 0.34 * sway)
                lvl = 0.085 + 0.43 * ring + 0.13 * pulse + 0.04 * sway
                out.append(emit(col, lvl))
            else:
                swing = 0.5 + 0.5 * math.sin(lb * 0.5 + i * 1.2)
                col = _mix(u, C_INDIGO, C_CYAN, 0.30 + 0.25 * swing)
                lvl = 0.075 + 0.14 * swing + 0.06 * pulse
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 10. VERSE - one travelling spotlight; then a quantized ping-pong chase.
    # ------------------------------------------------------------------
    if act == "verse":
        col_base = _mix(u, c_warm, C_MAGENTA, 0.38 + 0.28 * math.sin(lb * 0.25))
        chase = lb >= 16.0
        out = []
        for i in range(n):
            if upper:
                if not chase:
                    pos = 0.5 + 0.44 * math.sin(lb * math.pi / 12.0)
                    b = _gauss(xs[i], pos, 0.10)
                    halo = _gauss(xs[i], pos, 0.22)
                    col = col_base
                    lvl = 0.012 + 0.55 * b + 0.10 * halo + 0.045 * beat_dec
                else:
                    step = int(lb) % n
                    dist = abs(i - step)
                    if dist == 0:
                        lvl = 0.30 + 0.60 * beat_dec
                    elif dist == 1:
                        lvl = 0.060 + 0.10 * beat_dec
                    else:
                        lvl = 0.012
                    col = col_base if bar_pos % 2 == 0 else _mix(u, C_GOLD, c_secondary, 0.32)
                if bar_pos in (1, 3) and _hash01(i * 11 + beat_idx * 5) > 0.88 and beat_phase < 0.28:
                    fl = max(0.0, 1.0 - beat_phase * 3.2)
                    col = _mix(u, col, C_WHITE, 0.80)
                    lvl = max(lvl, 0.52 * fl)
                out.append(emit(col, lvl))
            else:
                swing = 0.5 + 0.5 * math.sin(lb * 0.4 + i * 0.8)
                col = _mix(u, c_warm, C_MAGENTA, 0.24 + 0.20 * swing)
                lvl = 0.055 + 0.09 * swing + 0.05 * kick
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 11. RISING VERSE - beams widen, then a six-beat glitch fill.
    # ------------------------------------------------------------------
    if act == "rise":
        if lb >= 26.0:
            if lb >= 31.2:
                return _black(n)
            eighth = int(beat * 2.0) % 2
            out = []
            for i in range(n):
                on = (i + eighth) % 2 == 0
                col = C_ICE if on else c_secondary
                lvl = 0.90 if on else 0.020
                if not upper:
                    lvl *= 0.72
                out.append(emit(col, lvl))
            return out

        p = _clamp(lb / 32.0)
        pos = 0.5 + (0.28 + 0.16 * p) * math.sin(lb * math.pi / 6.0)
        col_base = _mix(u, c_warm, C_MAGENTA, 0.34 + 0.38 * p)
        out = []
        for i in range(n):
            if upper:
                b1 = _gauss(xs[i], pos, 0.11)
                b2 = _gauss(xs[i], 1.0 - pos, 0.18)
                lvl = 0.018 + 0.44 * b1 + 0.16 * b2 + 0.07 * beat_dec
                col = _mix(u, col_base, C_GOLD, 0.18 * b1)
                out.append(emit(col, lvl))
            else:
                step = int(lb * 0.5) % n
                dist = abs(i - step)
                lvl = (0.30 + 0.35 * beat_dec) if dist == 0 else (0.08 if dist == 1 else 0.025)
                col = _mix(u, C_PURPLE, C_MAGENTA, 0.35 + 0.30 * p)
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 12. LASER-FAN CHORUS - crossfire beams, big vocal blooms.
    # ------------------------------------------------------------------
    if act == "chorus2":
        scream = (178.7 <= t <= 182.2) or (187.4 <= t <= 191.2)
        out = []
        if scream:
            start = 178.7 if t < 185.0 else 187.4
            radius = _clamp((t - start) / 1.5)
            for i in range(n):
                if upper:
                    bloom = _gauss(ds[i], radius, 0.30) + 0.55 * _gauss(ds[i], 1.0 - radius, 0.34)
                    col = _mix(u, C_ICE, C_MAGENTA, ds[i])
                    lvl = 0.22 + 0.64 * bloom + 0.07 * kick
                else:
                    wave = 0.5 + 0.5 * math.sin(lb * math.pi / 2.0 + i * 0.9)
                    col = _mix(u, c_warm, C_MAGENTA, 0.30 + 0.42 * wave)
                    lvl = 0.24 + 0.34 * kick + 0.14 * wave
                out.append(emit(col, lvl))
            return out

        pos1 = 0.5 + 0.42 * math.sin(lb * math.pi / 8.0)
        pos2 = 0.5 - 0.42 * math.sin(lb * math.pi / 8.0)
        for i in range(n):
            if upper:
                b1 = _gauss(xs[i], pos1, 0.10)
                b2 = _gauss(xs[i], pos2, 0.10)
                hot = max(b1, b2)
                cross = min(b1, b2)
                col = _mix(u, c_primary, C_ICE, 0.5 + 0.5 * math.sin(lb * 0.40 + xs[i] * 2.5))
                lvl = 0.030 + 0.47 * hot + 0.20 * cross + 0.17 * kick + 0.07 * snare
                if bar_pos == 3 and ds[i] >= 0.65:
                    lvl += 0.12 * beat_dec
                out.append(emit(col, lvl))
            else:
                wave = 0.5 + 0.5 * math.sin(lb * math.pi / 2.0 + i * 1.4)
                col = _mix(u, c_warm, C_MAGENTA, 0.35 + 0.45 * wave)
                lvl = 0.20 + 0.40 * kick + 0.12 * wave
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 13. HOUSE LIGHTS - warm white crowd wave, then a last-bar lift.
    # ------------------------------------------------------------------
    if act == "house":
        if lb >= 31.6:
            return _black(n)
        out = []
        for i in range(n):
            wave = 0.5 + 0.5 * math.sin((xs[i] * 3.5 - lb * 0.65) * math.pi)
            if upper:
                col = _mix(u, c_warm, C_WHITE, 0.22 + 0.34 * wave)
                if bar_pos in (1, 3):
                    col = _mix(u, col, C_GOLD, 0.22)
                lvl = 0.11 + 0.27 * energy + 0.22 * wave + 0.09 * pulse
                if lb >= 28.0:
                    p = _clamp((lb - 28.0) / 4.0)
                    col = _mix(u, col, C_WHITE, 0.70 * p)
                    lvl += 0.16 * p * beat_dec
                out.append(emit(col, lvl))
            else:
                col = _mix(u, c_warm, C_AMBER, 0.32 + 0.25 * wave)
                lvl = 0.13 + 0.20 * wave + 0.12 * pulse + 0.06 * energy
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 14. EMPTY STAGE - negative space, two pendants and a shooting star.
    # ------------------------------------------------------------------
    if act == "break":
        breath = 0.5 + 0.5 * math.sin(lb * 0.35)
        centre_i = int(round((n - 1) / 2.0))
        star_phase = (lb % 16.0) / 4.0
        star_on = 0.0
        star_pos = 0.0
        if star_phase <= 1.0:
            star_on = max(0.0, 1.0 - star_phase) ** 2
            star_pos = star_phase
        out = []
        for i in range(n):
            if upper:
                if i == centre_i:
                    col = _mix(u, C_GOLD, C_WHITE, 0.15 + 0.20 * breath)
                    lvl = 0.10 + 0.23 * breath
                elif ds[i] >= 0.72:
                    col = _mix(u, C_AMBER, C_GOLD, breath * 0.5)
                    lvl = 0.045 + 0.13 * breath
                else:
                    col = C_BLACK
                    lvl = 0.0
                if star_on > 0.01:
                    sb = _gauss(xs[i], star_pos, 0.075) * star_on
                    if sb > 0.01:
                        col = _mix(u, col, C_ICE, 0.85)
                        lvl = max(lvl, 0.62 * sb)
                out.append(emit(col, lvl))
            else:
                col = _mix(u, C_INDIGO, C_MAGENTA, 0.25 + 0.18 * breath)
                lvl = 0.035 + 0.075 * breath + 0.015 * pulse
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 15. FINAL BUILD - iris opens, then crushes to a point; runway countdown.
    # ------------------------------------------------------------------
    if act == "build2":
        if lb >= 30.0:
            return _black(n)
        p = _clamp(lb / 32.0)
        if p < 0.5:
            aperture = _smooth(0.0, 0.5, p)
        else:
            aperture = 1.0 - _smooth(0.5, 1.0, p) * 0.96
        sub = 2 if p < 0.55 else 4
        gate = _pulse(beat * sub, 1.30)
        col = _mix(u, c_warm, C_ICE, p ** 1.15)
        if p > 0.82:
            col = _mix(u, col, C_WHITE, (p - 0.82) * 3.0)
        out = []
        for i in range(n):
            if upper:
                if ds[i] <= aperture:
                    lvl = 0.09 + 0.73 * gate + 0.08 * p
                    if p < 0.5:
                        lvl *= 0.45 + 1.1 * p
                    out.append(emit(col, lvl))
                else:
                    out.append(C_BLACK)
            else:
                lit_at = 0.12 + 0.22 * (i + 1)
                if p > lit_at and p < 0.92:
                    lvl = 0.18 + 0.55 * gate
                    out.append(emit(col, lvl))
                else:
                    out.append(C_BLACK)
        return out

    # ------------------------------------------------------------------
    # 16. SUPERNOVA ENCORE - white impact, sunrise, then full pop colours.
    # ------------------------------------------------------------------
    if act == "encore":
        if lb >= 31.5:
            return _black(n)
        if lb < 4.0:
            fl = max(0.0, 1.0 - lb / 4.0)
            col = _mix(u, C_ICE, C_WHITE, 0.25 + 0.55 * fl)
            lvl = 0.36 + 0.58 * fl
            return [emit(col, lvl) for _ in range(n)]

        pos = 0.5 + 0.42 * math.sin(lb * math.pi / 8.0)
        ring_r = (lb * 0.5) % 1.0
        out = []
        for i in range(n):
            if upper:
                hot = _gauss(xs[i], pos, 0.14)
                wave = max(_gauss(ds[i], ring_r, 0.21), 0.55 * _gauss(ds[i], 1.0 - ring_r, 0.21))
                col = _mix(u, c_warm, _mix(u, C_MAGENTA, C_CYAN, xs[i]), 0.20 + 0.75 * xs[i])
                if bar_pos % 2 == 1:
                    col = _mix(u, col, c_primary, 0.35)
                lvl = 0.10 + 0.46 * hot + 0.24 * wave + 0.16 * kick + 0.07 * snare
                if lb >= 28.0:
                    q = _clamp((lb - 28.0) / 3.5)
                    col = _mix(u, col, C_WHITE, 0.55 * q)
                    lvl += 0.10 * q * beat_dec
                out.append(emit(col, lvl))
            else:
                wave = 0.5 + 0.5 * math.sin(lb * math.pi / 2.0 + i * 1.2)
                col = _mix(u, c_warm, c_primary, 0.30 + 0.48 * wave)
                lvl = 0.20 + 0.42 * kick + 0.12 * wave + 0.05 * bright
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 17. SYNTH SOLO - comet beam with real trailing tails; low counter-chase.
    # ------------------------------------------------------------------
    if act == "solo":
        if lb >= 31.4:
            return _black(n)
        _, pos = _comet(0.5, lb, 8.0, 0.085)
        out = []
        for i in range(n):
            if upper:
                c, _ = _comet(xs[i], lb, 8.0, 0.085)
                col = _mix(u, C_ICE, C_WHITE, 0.30 + 0.45 * _gauss(xs[i], pos, 0.10))
                if bar_pos % 2 == 1:
                    col = _mix(u, col, C_MAGENTA, 0.38)
                lvl = 0.020 + 0.62 * c + 0.09 * kick + 0.05 * beat_dec
                if _hash01(i * 23 + beat_idx * 7 + 9) > 0.86:
                    sp = max(0.0, 1.0 - beat_phase * 2.8)
                    col = _mix(u, col, C_WHITE, 0.75)
                    lvl += 0.16 * sp
                if lb >= 29.0:
                    conv = _clamp((lb - 29.0) / 2.0)
                    centre = _gauss(xs[i], 0.5, 0.22)
                    lvl = lvl * (1.0 - conv) + 0.65 * centre * conv
                out.append(emit(col, lvl))
            else:
                step = int(lb * 1.0) % n
                dist = abs(i - step)
                if dist == 0:
                    col = C_MAGENTA
                    lvl = 0.34 + 0.38 * beat_dec
                elif dist == 1:
                    col = c_secondary
                    lvl = 0.12 + 0.15 * beat_dec
                else:
                    col = C_PURPLE
                    lvl = 0.025
                out.append(emit(col, lvl))
        return out

    # ------------------------------------------------------------------
    # 18. OUTRO - dry percussion, isolated pinspots, warm fade.
    # ------------------------------------------------------------------
    if act == "outro":
        fade = _clamp((16.0 - lb) / 6.0)
        step = int(lb) % n
        centre_i = int(round((n - 1) / 2.0))
        out = []
        for i in range(n):
            if upper:
                if lb >= 12.0:
                    if i == centre_i:
                        col = _mix(u, c_warm, C_GOLD, 0.35 * fade)
                        lvl = 0.38 * fade
                    else:
                        col = C_BLACK
                        lvl = 0.0
                elif i == step:
                    col = _mix(u, c_warm, C_GOLD, 0.25)
                    lvl = (0.18 + 0.78 * beat_dec) * fade
                elif abs(i - step) == 1:
                    col = c_warm
                    lvl = 0.10 * beat_dec * fade
                else:
                    col = C_BLACK
                    lvl = 0.0
                out.append(emit(col, lvl))
            else:
                if bar_pos == 0 and i == centre_i:
                    col = C_MAGENTA
                    lvl = 0.34 * beat_dec * fade
                else:
                    col = _mix(u, C_INDIGO, C_PURPLE, 0.25)
                    lvl = 0.018 * fade
                out.append(emit(col, lvl))
        return out

    return _black(n)
