import math

META = {
    "id": "dance_timecode",
    "name": "DANCE - Exact 105 BPM Timecode Show (7+3 Loft)",
    "description": "Frame-accurate choreography analyzed directly from DANCE....mp4 (105.0 BPM, 7 upper gallery spots + 3 lower spots).",
}

PARAMS = [
    {"key": "brightness", "label": "Master Brightness", "type": "range", "min": 0.1, "max": 1.0, "step": 0.05, "default": 1.0},
    {"key": "offset_s", "label": "Timecode Offset (sec)", "type": "range", "min": -2.0, "max": 2.0, "step": 0.05, "default": 0.0},
    {"key": "restart_show", "label": "Force Restart Timecode (Toggle)", "type": "bool", "default": False},
]


def setup(state):
    state["t0"] = -1.0
    state["last_alive"] = 0.0
    state["last_restart_flag"] = False


def render(ctx):
    n = max(1, int(ctx["n"]))
    now = float(ctx["now"])
    af = ctx["features"]
    p = ctx["params"]
    st = ctx["state"]
    u = ctx["utils"]

    master = float(p.get("brightness", 1.0))
    offset_s = float(p.get("offset_s", 0.0))
    restart_flag = bool(p.get("restart_show", False))

    # Manual reset toggle in UI
    if restart_flag != st.get("last_restart_flag", False):
        st["last_restart_flag"] = restart_flag
        st["t0"] = now

    # Track time resolution: prefer Sendspin track_progress_s if available, else lock t0 on first audio
    show_track = af.get("show_track") or {}
    rms = float(af.get("rms", 0.0))
    is_playing = bool(af.get("alive")) and (rms > 0.005 or af.get("kick") or float(ctx["env"].get("energy", 0.0)) > 0.02)

    if is_playing:
        if st.get("t0", -1.0) < 0.0 or (now - float(st.get("last_alive", 0.0)) > 5.0):
            st["t0"] = now
        st["last_alive"] = now
    else:
        if now - float(st.get("last_alive", 0.0)) > 5.0:
            st["t0"] = -1.0
            return [(0, 0, 0)] * n

    if show_track.get("progress_s", 0.0) > 0.1:
        t = float(show_track["progress_s"]) + offset_s
    else:
        t = max(0.0, (now - float(st.get("t0", now))) + offset_s)

    # Exact musical grid: 105.0 BPM (1 beat = 0.571428s, 1 bar = 2.285714s)
    BPM = 105.0
    BEAT_S = 60.0 / BPM
    # Anchor beat grid to the first real drop at 55.4s so beats never drift
    beat_float = (t - 4.5) / BEAT_S if t >= 4.5 else t / BEAT_S
    b_idx = int(math.floor(beat_float))
    b_phase = beat_float % 1.0
    bar_pos = b_idx % 4

    # Upper gallery (7 spots) vs Lower depth zone (3 spots)
    is_upper = (n >= 5)
    center = (n - 1) / 2.0
    max_d = max(1.0, center)

    # Palette constants
    C_AMBER = [255, 105, 18]
    C_CRIMSON = [255, 18, 55]
    C_MAGENTA = [215, 15, 160]
    C_CYAN = [0, 215, 255]
    C_INDIGO = [22, 10, 68]
    C_XENON = [245, 248, 255]

    px = []

    # =========================================================================
    # 1. ATMOSPHERE INTRO (0:00.0 - 0:04.5)
    # =========================================================================
    if t < 4.5:
        swell = (t / 4.5) ** 1.4
        for i in range(n):
            if is_upper:
                # Upper gallery stays dark except a faint center ember on Spot 4
                lvl = 0.12 * swell if abs(i - center) < 0.6 else 0.0
                px.append(u.scale(C_AMBER, lvl * master))
            else:
                # Lower 3 spots breathe up from deep indigo/amber
                px.append(u.scale(u.lerp(C_INDIGO, C_AMBER, 0.5), (0.08 + 0.35 * swell) * master))
        return px

    # =========================================================================
    # 2. PITCH-RISER ARPEGGIO (0:04.5 - 0:21.7)
    # =========================================================================
    if t < 21.7:
        prog = (t - 4.5) / (21.7 - 4.5)
        # Accelerating synth arpeggio sweep across the 7 upper spots
        arp_speed = 1.2 + prog * 3.8
        arp_pos = 0.5 + 0.48 * math.sin((t - 4.5) * arp_speed * 2.0)
        col = u.lerp(C_AMBER, C_CYAN, prog * 0.75)
        for i in range(n):
            nx = i / max(1, n - 1) if n > 1 else 0.5
            if is_upper:
                w = math.exp(-(((nx - arp_pos) / max(0.10, 0.24 - prog * 0.10)) ** 2))
                lvl = (0.03 + (0.35 + 0.65 * prog) * w) * master
                px.append(u.scale(col, lvl))
            else:
                # Lower spots pulse with rising tension
                lvl = (0.06 + 0.30 * prog * (0.5 + 0.5 * math.sin(b_phase * 6.283))) * master
                px.append(u.scale(C_INDIGO, lvl))
        return px

    # =========================================================================
    # 3. MIDTEMPO GROOVE INTRO (0:21.7 - 0:38.8) -> 105 BPM Kick + Clap
    # =========================================================================
    if t < 38.8:
        beat_decay = max(0.0, 1.0 - b_phase * 1.65) ** 1.5
        is_clap = (bar_pos in (1, 3))
        for i in range(n):
            d_norm = abs(i - center) / max_d
            if is_upper:
                if not is_clap:
                    # Beat 1 & 3 (Kick): Center core (Spots 3, 4, 5)
                    hit = d_norm <= 0.38
                    col = C_CRIMSON
                else:
                    # Beat 2 & 4 (Clap): Outer wings (Spots 1 & 7)
                    hit = d_norm >= 0.65
                    col = C_CYAN
                lvl = (0.04 + 0.90 * beat_decay) if hit else 0.03
                px.append(u.scale(col if hit else C_INDIGO, lvl * master))
            else:
                # Lower 3 spots lock to the sub-bass kick on every beat
                lvl = 0.08 + 0.55 * beat_decay
                px.append(u.scale(C_CRIMSON, lvl * master))
        return px

    # =========================================================================
    # 4. TENSION FILTER RISER & PRE-DROP BLACKOUT (0:38.8 - 0:55.4)
    # =========================================================================
    if t < 55.4:
        # Final 0.6s before 0:55.4 drop: TOTAL VACUUM BLACKOUT!
        if t >= 54.8:
            return [(0, 0, 0)] * n
        prog = (t - 38.8) / (54.8 - 38.8)
        # Aperture narrows from full room width down to dead center Spot 4
        aperture = max(0.05, 1.0 - prog * 0.92)
        sub_div = 4.0 if prog > 0.65 else (2.0 if prog > 0.3 else 1.0)
        gate = max(0.0, 1.0 - ((b_phase * sub_div) % 1.0) * 1.7)
        col = u.lerp(C_CRIMSON, C_XENON, prog ** 1.3)
        for i in range(n):
            d_norm = abs(i - center) / max_d
            if is_upper:
                if d_norm <= aperture:
                    lvl = (0.25 + 0.75 * gate) * master
                    px.append(u.scale(col, lvl))
                else:
                    px.append((0, 0, 0))
            else:
                # Lower zone shuts off at 75% build to heighten upper focal tension
                lvl = (1.0 - prog) * 0.45 * gate * master if prog < 0.75 else 0.0
                px.append(u.scale(col, lvl))
        return px

    # =========================================================================
    # 5. DROP 1 - HEAVY CYBERPUNK BASS & VOCAL CHOPS (0:55.4 - 1:13.7)
    # =========================================================================
    if t < 73.7:
        punch = max(0.0, 1.0 - b_phase * 1.45) ** 1.3
        # Every 2nd half of the drop (1:04.5+), add vocal-chop syncopation on 8th off-beats
        chop_active = (t >= 64.5) and (0.45 <= b_phase <= 0.75)
        for i in range(n):
            d_norm = abs(i - center) / max_d
            if is_upper:
                is_wing = d_norm >= 0.48
                # Symmetrical MA3 Wing Slam: Beat 1/3 Center, Beat 2/4 Wings
                on = (bar_pos % 2 == 0 and not is_wing) or (bar_pos % 2 == 1 and is_wing)
                if chop_active and (i % 2 == 1):
                    px.append(u.scale(C_MAGENTA, 0.85 * master))
                elif on:
                    col = C_CYAN if is_wing else C_CRIMSON
                    px.append(u.scale(col, (0.55 + 0.45 * punch) * master))
                else:
                    px.append(u.scale(C_INDIGO, 0.04 * master))
            else:
                # Lower 3 spots pump hard with the distorted Reese bassline
                bass_swell = 0.45 + 0.55 * punch
                px.append(u.scale(C_CRIMSON if bar_pos % 2 == 0 else C_MAGENTA, bass_swell * master))
        return px

    # =========================================================================
    # 6. VERSE 1 ("You couldn't wait to see me fail...") (1:13.7 - 1:44.0)
    # =========================================================================
    if t < 104.0:
        is_part_b = (t >= 92.0)  # 1:32.0 "Walked by, can't help but feel..."
        # Slow, silky 1-bar pendulum gliding across the upper gallery
        bar_phase = ((b_idx % 4) + b_phase) / 4.0
        pend_x = 0.5 - 0.42 * math.cos(2.0 * math.pi * bar_phase)
        clap_hit = (bar_pos in (1, 3)) and (b_phase < 0.22)
        for i in range(n):
            nx = i / max(1, n - 1) if n > 1 else 0.5
            if is_upper:
                w = math.exp(-(((nx - pend_x) / (0.26 if is_part_b else 0.18)) ** 2))
                col = u.lerp(C_INDIGO, C_MAGENTA if is_part_b else C_AMBER, w)
                lvl = 0.04 + 0.62 * w
                if clap_hit and (i == 0 or i == n - 1):
                    col = C_CYAN
                    lvl = 0.75
                px.append(u.scale(col, lvl * master))
            else:
                # Warm deep lounge foundation underneath
                lvl = 0.12 + (0.25 if is_part_b else 0.12) * math.sin(bar_phase * 6.283)
                px.append(u.scale(C_AMBER, max(0.05, lvl) * master))
        return px

    # =========================================================================
    # 7. PRE-CHORUS GLITCH BREAK 1 (1:44.0 - 1:46.8)
    # =========================================================================
    if t < 106.8:
        if t >= 106.3:
            return [(0, 0, 0)] * n
        step_8th = int(beat_float * 2.0) % 2
        for i in range(n):
            on = (i % 2) == step_8th
            px.append(u.scale(C_XENON if on else [0, 0, 0], (0.85 if on else 0.0) * master))
        return px

    # =========================================================================
    # 8. CHORUS 1 ("I kinda hate your vibe... let me DANCE!") (1:46.8 - 2:04.8)
    # =========================================================================
    if t < 124.8:
        # "DANCE!" vocal sustain windows: 1:50.8-1:54.2 (110.8-114.2s) and 1:59.5-2:03.2 (119.5-123.2s)
        is_dance_scream = (110.8 <= t <= 114.2) or (119.5 <= t <= 123.2)
        punch = max(0.0, 1.0 - b_phase * 1.4) ** 1.3
        for i in range(n):
            d_norm = abs(i - center) / max_d
            if is_dance_scream:
                # Full ceiling bloom expanding from center on "DANCE!"
                wave = 0.65 + 0.35 * math.cos(d_norm * 3.1415 - b_phase * 6.283)
                col = u.lerp(C_AMBER, C_MAGENTA, d_norm)
                px.append(u.scale(col, wave * master))
            else:
                # Tight rhythmic step on "I kinda hate your vibe..."
                ring = bar_pos % 4
                spot_ring = int(round(d_norm * 3.0))
                on = (spot_ring == ring)
                col = C_CYAN if bar_pos in (1, 3) else C_CRIMSON
                lvl = (0.12 + 0.88 * punch) if on else 0.05
                px.append(u.scale(col if on else C_INDIGO, lvl * master))
        return px

    # =========================================================================
    # 9. POST-CHORUS ("Feel it in my bones / Floating on my own") (2:04.8 - 2:20.8)
    # =========================================================================
    if t < 140.8:
        punch = max(0.0, 1.0 - b_phase * 1.5)
        wave_r = (beat_float * 0.5) % 1.0
        for i in range(n):
            d_norm = abs(i - center) / max_d
            w = math.exp(-(((d_norm - wave_r) / 0.22) ** 2))
            col = u.lerp(C_MAGENTA, C_CYAN, d_norm)
            lvl = 0.08 + 0.72 * w + 0.20 * punch
            px.append(u.scale(col, min(1.0, lvl) * master))
        return px

    # =========================================================================
    # 10. VERSE 2 ("I never wanna stay... Delusional, fuck how you feel") (2:20.8 - 2:52.0)
    # =========================================================================
    if t < 172.0:
        # "fuck how you feel!" accent at 2:49.0 - 2:51.5 (169.0 - 171.5s)
        if 169.0 <= t <= 171.5:
            flash = max(0.0, 1.0 - (t - 169.0) / 2.5)
            return [u.scale(C_CRIMSON, (0.2 + 0.8 * flash) * master) for _ in range(n)]

        step_spot = b_idx % n
        decay = max(0.0, 1.0 - b_phase * 1.35)
        for i in range(n):
            dist = abs(i - step_spot)
            if dist == 0:
                px.append(u.scale(C_AMBER if bar_pos % 2 == 0 else C_MAGENTA, (0.35 + 0.65 * decay) * master))
            elif dist == 1:
                px.append(u.scale(C_INDIGO, 0.22 * decay * master))
            else:
                px.append(u.scale(C_INDIGO, 0.03 * master))
        return px

    # =========================================================================
    # 11. GLITCH BREAK 2 & BLACKOUT (2:52.0 - 2:54.8)
    # =========================================================================
    if t < 174.8:
        if t >= 174.2:
            return [(0, 0, 0)] * n
        step_8th = int(beat_float * 2.0) % 2
        for i in range(n):
            on = (i % 2) == step_8th
            px.append(u.scale(C_CYAN if on else [0, 0, 0], (0.90 if on else 0.0) * master))
        return px

    # =========================================================================
    # 12. CHORUS 2 & POST-CHORUS 2 (2:54.8 - 3:28.5)
    # =========================================================================
    if t < 208.5:
        is_dance_scream = (178.8 <= t <= 182.2) or (187.5 <= t <= 191.2)
        punch = max(0.0, 1.0 - b_phase * 1.5) ** 1.2
        for i in range(n):
            d_norm = abs(i - center) / max_d
            if is_dance_scream:
                wave = 0.70 + 0.30 * math.cos(d_norm * 3.1415 - b_phase * 6.283)
                col = u.lerp(C_AMBER, C_CYAN, d_norm)
                px.append(u.scale(col, wave * master))
            else:
                is_wing = d_norm >= 0.48
                on = (bar_pos % 2 == 0 and not is_wing) or (bar_pos % 2 == 1 and is_wing)
                col = C_MAGENTA if is_wing else C_CRIMSON
                lvl = (0.50 + 0.50 * punch) if on else 0.05
                px.append(u.scale(col if on else C_INDIGO, lvl * master))
        return px

    # =========================================================================
    # 13. THE ETHEREAL BRIDGE (Sudden Beat Cut at 3:28.5 - 3:45.7)
    # =========================================================================
    if t < 225.7:
        # Dreamy ambient chords, zero drums: 75% negative space, slow warm breathing
        chord_swell = 0.5 + 0.5 * math.sin((t - 208.5) * 1.38)
        for i in range(n):
            if is_upper:
                # Only Spot 2 and Spot 6 glow softly like warm 1800K pendants
                if i in (1, n - 2):
                    px.append(u.scale(C_AMBER, (0.08 + 0.32 * chord_swell) * master))
                else:
                    px.append((0, 0, 0))
            else:
                # Lower 3 spots provide a deep twilight indigo horizon
                px.append(u.scale(u.lerp(C_INDIGO, C_MAGENTA, 0.3), (0.10 + 0.25 * chord_swell) * master))
        return px

    # =========================================================================
    # 14. FINAL BUILD-UP ("I came here to party...") (3:45.7 - 4:02.8)
    # =========================================================================
    if t < 242.8:
        # Pre-climax vacuum blackout at 4:02.1 - 4:02.8
        if t >= 242.1:
            return [(0, 0, 0)] * n
        prog = (t - 225.7) / (242.1 - 225.7)
        # Progressive stack + accelerating snare-roll pulse
        sub_div = 4.0 if prog > 0.65 else 2.0
        gate = max(0.0, 1.0 - ((b_phase * sub_div) % 1.0) * 1.6)
        col = u.lerp(C_AMBER, C_XENON, prog ** 1.4)
        for i in range(n):
            d_norm = abs(i - center) / max_d
            # First half fills out, second half compresses tightly into center Spot 4
            aperture = min(1.0, prog * 2.0) if prog < 0.5 else max(0.05, 1.0 - (prog - 0.5) * 1.85)
            if d_norm <= aperture:
                px.append(u.scale(col, (0.25 + 0.75 * gate) * master))
            else:
                px.append((0, 0, 0))
        return px

    # =========================================================================
    # 15. CLIMAX FINALE ("Ooh-ooh" Drop + High Synth Solo) (4:02.8 - 4:37.1)
    # =========================================================================
    if t < 277.1:
        is_synth_solo = (t >= 259.8)  # 4:19.8 high-pitched synth lead solo
        punch = max(0.0, 1.0 - b_phase * 1.6) ** 1.2
        solo_pos = 0.5 + 0.48 * math.sin((t - 259.8) * 5.5) if is_synth_solo else 0.5
        for i in range(n):
            nx = i / max(1, n - 1) if n > 1 else 0.5
            d_norm = abs(i - center) / max_d
            if is_upper:
                # Base symmetrical Odd/Even concert grid
                grid_on = (i % 2) == (b_idx % 2)
                col = C_CRIMSON if (bar_pos % 2 == 0) else C_CYAN
                lvl = (0.55 + 0.45 * punch) if grid_on else 0.05
                # Overlay blazing synth-solo beam sweeping across the 7 spots
                if is_synth_solo:
                    solo_w = math.exp(-(((nx - solo_pos) / 0.16) ** 2))
                    if solo_w > 0.25:
                        col = u.lerp(col, C_XENON, solo_w)
                        lvl = max(lvl, solo_w)
                px.append(u.scale(col, lvl * master))
            else:
                # Lower 3 spots fire full complementary counter-blinders
                col = C_MAGENTA if (bar_pos % 2 == 0) else C_AMBER
                px.append(u.scale(col, (0.45 + 0.55 * punch) * master))
        return px

    # =========================================================================
    # 16. STRIPPED DRUM-LOOP OUTRO (4:37.1 - 4:46.0)
    # =========================================================================
    if t < 285.8:
        # Music strips down to a dry, naked percussion loop -> crisp minimal pin-spots!
        active_spot = b_idx % n
        stab = max(0.0, 1.0 - b_phase * 2.8) ** 2.0
        for i in range(n):
            if is_upper and i == active_spot:
                px.append(u.scale(C_AMBER, stab * master))
            elif not is_upper and bar_pos == 0 and i == 1:
                px.append(u.scale(C_CRIMSON, stab * 0.6 * master))
            else:
                px.append((0, 0, 0))
        return px

    return [(0, 0, 0)] * n