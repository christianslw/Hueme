import math

META = {
    "id": "berghain_shutter",
    "name": "Industrial Shutter (Strict Role Split)",
    "description": "Minimalist club ceiling rig: Center spots = Kick only, Outer end spots = Snare only, Intermediate spots = quantized clockwork ticker. Zero overlap.",
}

PARAMS = [
    {"key": "kick_color", "label": "Center Kick Lamp (Tungsten/Red)", "type": "color", "default": [255, 95, 15]},
    {"key": "snare_color", "label": "Outer Snare Blinder (Xenon/Ice)", "type": "color", "default": [190, 235, 255]},
    {"key": "clock_color", "label": "Grid Ticker Color", "type": "color", "default": [160, 20, 40]},
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "shutter_cut", "label": "Shutter Speed (Tightness)", "type": "range", "min": 0.2, "max": 0.9, "step": 0.05, "default": 0.65},
    {"key": "clock_div", "label": "Ticker Subdivision", "type": "select", "default": "quarter", "options": [["quarter", "1/4 Beat Step"], ["eighth", "1/8 Off-Beat Step"]]},
]


def setup(state):
    state["kick_latch"] = 0.0
    state["snare_latch"] = 0.0
    state["last_step"] = -1
    state["ticker_idx"] = 0


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
    tightness = float(p.get("shutter_cut", 0.65))
    div = str(p.get("clock_div", "quarter"))

    c_kick = p.get("kick_color", [255, 95, 15])
    c_snare = p.get("snare_color", [190, 235, 255])
    c_clock = p.get("clock_color", [160, 20, 40])

    b_count = int(beat.get("count", 0))
    b_phase = float(beat.get("phase", 0.0))
    bar_pos = b_count % 4

    # Partition fixture indices into 3 non-overlapping hardware groups
    center = (n - 1) / 2.0
    kick_indices = set()
    snare_indices = set()
    clock_indices = []

    for i in range(n):
        if n <= 3:
            if i == int(round(center)):
                kick_indices.add(i)
            else:
                snare_indices.add(i)
        else:
            if abs(i - center) <= 0.6:
                kick_indices.add(i)
            elif i == 0 or i == n - 1:
                snare_indices.add(i)
            else:
                clock_indices.append(i)

    if not clock_indices:
        clock_indices = list(range(n))

    # Advance clock ticker on quarter or eighth grid
    step_num = b_count * 2 + int(b_phase * 2.0) if div == "eighth" else b_count
    if step_num != int(st.get("last_step", -1)):
        st["last_step"] = step_num
        st["ticker_idx"] = (int(st.get("ticker_idx", 0)) + 1) % len(clock_indices)

    active_clock_spot = clock_indices[int(st.get("ticker_idx", 0)) % len(clock_indices)]

    # Shutter decay time constant (45ms to 180ms)
    tau_shutter = 0.045 + (1.0 - tightness) * 0.140

    k_latch = float(st.get("kick_latch", 0.0))
    if af.get("kick") or (b_phase < 0.08 and float(env.get("pulse", 0.0)) > 0.35):
        k_latch = 1.0
    else:
        k_latch *= math.exp(-dt / tau_shutter)
    st["kick_latch"] = k_latch

    s_latch = float(st.get("snare_latch", 0.0))
    if af.get("snare_hit") or (bar_pos in (1, 3) and b_phase < 0.08 and float(env.get("snare", 0.0)) > 0.30):
        s_latch = 1.0
    else:
        s_latch *= math.exp(-dt / (tau_shutter * 0.85))
    st["snare_latch"] = s_latch

    px = []
    for i in range(n):
        if i in kick_indices:
            # Hard threshold shutter: below 8% it cuts cleanly to black (halogen filaments cut)
            v = k_latch if k_latch > 0.08 else 0.0
            px.append(u.scale(c_kick, v * master))
        elif i in snare_indices:
            v = s_latch if s_latch > 0.08 else 0.0
            px.append(u.scale(c_snare, v * master))
        else:
            if i == active_clock_spot:
                sub_phase = (b_phase * 2.0) % 1.0 if div == "eighth" else b_phase
                v_clk = max(0.18, 0.65 * (1.0 - sub_phase))
                px.append(u.scale(c_clock, v_clk * master))
            else:
                px.append((0, 0, 0))

    return px
