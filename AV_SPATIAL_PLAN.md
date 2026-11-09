# Hueme AV & Spatial Plan — prework spec for implementation

> Status: implemented (phases A–D, 2026-11-08), including a single global
> top-down scene editor and AGC-scaled onset detection for quiet Snapcast
> streams. Sendspin playback-clock lookahead added 2026-11-09 (§11), plus a
> live sync-test calibration UI (§12). Phase E (mood layer, §9) remains
> deferred by design. Companion to `PLAN.md`, which remains the source of truth
> for the overall platform.

## 0. Problem statement (user verdict)

Current audio-reactive effects look terrible to the user: flicker with no
smoothness, no harmony, no musical relation to the song. Audio ingest (Snapcast ←
Music Assistant, PCM 48 kHz/16/2) works. The audio *brain* must be made capable:
real BPM, rhythm structure, beat-locked outputs, and (future) mood.

## 1. Root-cause analysis of the current DSP (`hueme/audio.py`, `AudioProcessor`)

Concrete defects, file/line evidence as of 2026-11-08:

1. **Band levels are self-normalizing against a ~0.26 s EMA** (line ~505:
   `band_ema = 0.96*ema + 0.04*b_rms`; `norm = min(1, b_rms/(ema*1.45)*sens)`).
   Consequence A: constant music sits at norm ≈ 0.85–1.0 → lamps near full.
   Consequence B: moment-to-moment RMS jitter of ±10 % passes straight through.
   With highs decay τ ≈ 105 ms (`9.5`), consecutive blocks alternate bright/dim →
   the perceived strobe.
2. **No slew limiting, no minimum release time** on any envelope. Every numeric
   jitter becomes a frame-level flicker at 30 fps.
3. **Kick = spectral flux over one 21 ms FFT window** (window 1024 @ 48 k) with
   a median threshold over only 43 blocks (~0.46 s). Threshold noise → multiple
   triggers per actual kick; `kick_env = 1.0` with τ = 100 ms = 3-frame flash
   = bursty epileptic pulses, drum fills hit harder than the downbeat.
4. **BPM estimator is interval-difference EMA between *onset candidates***, not
   music tempo (line ~523). No octave handling (halves/doubles constantly),
   no lock, no confidence, loses phase entirely. `beat_count` counts onsets
   (drum fills inflate it) — meaningless as a beat grid.
5. **AGC gain clips to 8×** with τ=10 s pumping through quiet→loud transitions;
   silence gate (`is_silent`) multiplies levels by `exp(-dt*8)` giving a hard
   vertical drop instead of musical release.
6. **Effects amplify all of this**: presets map raw band levels directly to
   brightness/position; several have "strobe" params at high duty. No effect
   uses beat phase/bar structure (they can't — it doesn't exist).

**Conclusion: this is not a tuning problem. The feature layer needs a rhythm
stage, and effects need a contract that makes strobing impossible by default.**

## 2. Target audio pipeline (v2 brain)

All inside `hueme/audio.py` (single file, no new dependencies; numpy only).
Stages:

```
PCM block (512 = 10.7 ms @48k)
  └─ A. resample-free direct path, AGC unchanged but gain clip 0.4–2.5×, tau ≥ 20 s
  └─ B. multi-resolution FFT:
        bass FFT:  window 4096 (85 ms, 11.7 Hz bins)  — sub-bass needs this
        treble FFT: window 1024 (21 ms)               — snare/transient timing
  └─ C. per-band analysis:
        level  = log-compressed RMS, slow attack (≥ 60 ms), release ≥ 400 ms
        flux   = half-wave rectified spectral difference (per band)
  └─ D. onset peak-picking (per band):
        trigger iff  flux > median(flux_hist) * k  AND flux is a local max
        AND now - last_t ≥ refractory (bass 0.18 s, snare 0.09 s)
        strength = (flux / median) normalized 0..1  (∞ clamp)
        flux_hist: ≥ 1.5 s of history (~140 hops)
  └─ E. RHYTHM STAGE (new, the heart):
        tempo + phase + confidence, see §3
  └─ F. musical envelopes (the "big-4"), see §4
  └─ G. features snapshot v2 (§5) → engine → sandbox ctx
```

Silence handling: gate via slow energy only; on gate the envelopes decay at
their musical release rates, never a hard drop.

## 3. Rhythm stage — algorithm spec (must implement, not optional)

### 3.1 Onsetstrength history

Keep `onsets: deque[(t, strength)]` for the last **8 s** (from bass+snare+mids
combined flux; separately keep bass-only list for kick origin/phase).

### 3.2 Tempo hypothesis — comb filtering over onset strength

Every 1.0 s (not every block):

1. Build onset-strength envelope sampled at 200 Hz over the 8 s window
   (each onset contributes a raised cosine kernel, width 60 ms, ×strength).
2. For tempo candidates T in 60..190 BPM (step 0.25): score =
   Σ envelope(T_k) for k=0..L-1 over the window with the best phase alignment —
   implemented as circular cross-correlation of the envelope against a pulse
   train; score = max over phase. Range 60–190, prefer 70–170 by small bias.
3. **Octave folding / disambiguation**: score ratio test. If score(T/2) /
   score(T) > 0.75 → prefer half tempo; if score(T*2) / score(T) > 0.75 →
   double. This eliminates the classic 60↔120↔240 wobble.
4. Smooth winner: `bpm = ema(bpm, winner, alpha ≈ 0.2)` when winner within
   ±5 % of current bpm, else switch with 2 s confirmation.
5. **Confidence**: `bpm_conf = clamp((best_score / median_score − 1) / 2, 0..1)`
   (peak prominence vs running tempo-candidates). Also `music_conf`
   from onset density: 1.5–8 onsets/s ⇒ music plausible; density outside ⇒ 0.

### 3.3 Phase tracking (beat grid)

- Maintain `beat_phase ∈ [0..1)`, advancing `beat_phase += dt*bpm/60`.
- Every onset (weight = strength × bpm_conf) makes a soft phase correction:
  `delta = wrap01(onset_phase − beat_phase)`; correction
  `±min(|delta|, 0.15) * 0.12 * weight` pulling phase toward the onset;
  bass onsets (kick table) count double.
- Wrap of beat_phase → `beat_count += 1` (a true beat counter at last).
- **Bar phase**: `bar_phase = (beat_count % 4 + beat_phase) / 4` (4/4 assumption;
  document it). Do NOT attempt meter detection in v1.
- Grid quality: if onsets consistently disagree > 3 beats in a row, halve
  confidence (the rhythm stage "loses the grid", effects fall back gracefully).

### 3.4 Rhythm filtering of raw triggers

The raw kick/snare onset triggers stay exposed, but each carries an aligned flag:
`aligned = wrap01(|onset_phase − nearest 16th-of-grid|) < 0.07`. Off-grid
onsets (fill noise, vocal plosives) get strength × 0.15 in the envelopes and
do not reset a wave/shock unless `bpm_conf < 0.25`.

## 4. Musical envelope set (the effects' vocabulary)

Ratios chosen so `env` movers breathe with the song at any volume (AGC makes
absolute level irrelevant; these shapes are what kill the epilepsy):

| name | source | release τ | attack | intended job |
|------|--------|-----------|--------|----------------|
| `pulse` | bass band level, log-comp, **slow** | 400 ms | 60–80 ms | whole-scene brightness/size — the "breathing" |
| `kick` | kick onset strength, aligned | 260 ms | 0 ms | wavefronts, thump accents — punch, not strobe (3+ frames loud vs 100 ms) |
| `snare` | snare onset strength | 180 ms | 0 ms | pattern accents, 2-beat |
| `bright` | highs+snare mix, log-compressed | 300 ms | 100 ms | sparkle density, tint |
| `energy` | overall RMS EMA | 3 s | 3 s | passage intensity / "mood energy" (future mood input) |

Rules (implement as a helper, not per-effect re-invention):
- log compression of band power before normalization: `0.5 + log10(1+9x)/...`
  so verses/choruses map to a perceptually useful spread.
- All envelopes slew-limited max Δ/frame ≥ nothing hard cuts; attack instant
  only for kick/snare/bright with strength ≥ 0.3.
- `beat.quantize()` helpers live in the sandbox utils, not in each effect.

## 5. Features snapshot v2 (exact dict contract)

`AudioProcessor.snapshot()` returns (backwards-compat keys kept at top level):

```python
{
  # legacy keys, same semantics as today (levels smooth, slow):
  "alive", "connected", "source", "error",
  "bass", "snare", "mids", "highs",        # band levels 0..1 (slower than today)
  "kick", "snare_hit",                     # raw onset trigger bools (this frame)
  "kick_env", "snare_env",                 # legacy envelopes (keep for old effects)
  "bpm", "rms", "agc_gain", "t_ingest",
  # new, additive:
  "beat": {
    "phase":    0.73,      # 0..1 position inside the beat
    "bar_phase": 0.18,     # 0..1 position inside the 4/4 bar
    "count":    214,       # true beat counter (grid-based)
    "on_beat":  True,      # within ±35 ms of grid edge this frame
    "on_downbeat": False,  # bar-phase wrap this frame
  },
  "env": {
    "pulse": 0.61, "kick": 0.88, "snare": 0.12,
    "bright": 0.35, "energy": 0.42,
  },
  "conf": {
    "bpm": 0.72,   # tempo-hypothesis confidence 0..1
    "music": 0.9,  # music-vs-noise/speech plausibility
    "grid": 0.65,  # phase-tracking quality
  },
  "aligned_kick": False,   # kick trigger aligned to grid (§3.4)
  "aligned_snare": False,
}
```

Sandbox ctx v2 passes this plus spatial data (§7).

## 6. The "harmonic output" layer (perception pipeline)

This is the difference between "objective reactiveness" and "looks expensive":

1. **Gamma 2.2 per channel** applied once per group at dispatch (engine, not per
   effect): `out = (in ** 2.2)`. Effects then need not compensate.
2. **Tone desaturation into the dark, capped white point**: below 30 % brightness
   blend hue toward warm white (`desat = smoothstep(0.30, 0.02, luminance)`),
   cap white at L=0.85 so "full" stays tinted.
3. **Cosine gradient palettes** (Íñigo Quílez form: `a + b·cos(2π(c·t+d))` over
   (position·k + phase·t)), in a shared `palettes` util: 6 curated presets
   (ember, synth teal-magenta, sunset, moonlight, acid green, ice). No naked HSV
   rainbows in any shipped preset.
4. **Crossfade 400 ms RGB lerp** on any effect/param change (engine `_render_loop`
   destination pixels) — removes all hard cuts when switching effects live.
5. **Beat-quantized transitions**: palette sweeps / scene changes snap to
   `beat.bar_phase` edges — "scored to the track" feel comes almost entirely
   from this one trick.

## 7. Effect contract v2 (with spatial + rhythm fields)

`render(ctx)` ctx extensions (sandbox.py line 235 area):

```python
ctx = {
  "n", "now", "dt", "params", "state", "utils",        # as today
  "features": {...},                                    # §5, top-level only
  "positions": [(x, y, z), ...],                        # normalized 0..1, full 3D
  "roles": ["center", "edge", ...],                     # per fixture: center|edge|low|high
  "env":    {"pulse": ..., "kick": ..., ...},           # shortcut of features
  "beat":   {"phase": ..., "bar_phase": ..., ...},
}
```

Legacy effects keep working: `positions` still 2-tuples if no z, `features`
still has legacy keys.

**Anti-flicker guardrail (QA rule)**: any shipped preset must have
`max(Δpixel_rgb per fixture per frame) ≤ 0.35` except within the 2 frames
after a kick trigger. Add this as a unit test harness against a synthetic
click track render (tests can run the sandbox in-process with fake ctx).

## 8. Reference grammar — one flagship effect spec (`scene_pulse`)

Shows the intended style; the implementing LLM should ship this + 2 more in
the same grammar:

```python
def render(ctx):
    state["ph"] = ctx["beat"]["phase"]; state["bar"] = ctx["beat"]["bar_phase"]
    n = ctx["n"]
    pos = ctx["positions"]; cx, cy = centroid(pos)
    # whole-scene breath
    base = 0.18 + 0.55 * ctx["env"]["pulse"]
    # palette drift locked to bars
    ph = state["bar"] + ctx["env"]["energy"] * 0.15
    px = []
    for i, (x, y, z) in enumerate(pos):
        r = math.hypot(x-cx, y-cy, (z-0.5)*0.7)
        # kick shockwave: wavefront travels with room speed, sharp gaussian front
        w = math.exp(-((r - ctx["env"]["kick"]*1.2)**2) / (2*0.15**2))  # wavefront
        col = palette(ctx["utils"], "synth", ph + 0.12*r)
        v = base + 0.5*w + local_sparkle(state, i, ctx)   # §4 grammar
        px.append(scale_c(col, min(1.0, v)))
    return px
```

Sparkle: per fixture `rand = hash(i, beat.count)` compared against
`ctx["env"]["bright"] * density` — musical, deterministic per beat, noise-free.

## 9. Future: mood layer (explicitly deferred, prewired for)

`env.energy` (§4) is the first mood axis. When done properly, mood = aggregate
over rolling 30 s of: energy, `conf.bpm`, brightness tilt (spectral centroid),
rhythmicity (onset regularity). Effects get it as `ctx["mood"]` dict. Do not
build until the base pipeline ships and feels right.

## 10. Phases & acceptance criteria

**Phase A — DSP v2 (this must land first)**
- multi-resolution FFT, onset peak-picking, §3 rhythm stage, §4 envelopes.
- Unit tests (run with `python -m unittest`):
  - synthetic click track 120 BPM s16le → `bpm ∈ [118, 122]` after 8 s,
    `bpm_conf > 0.5`, `beat.phase` locked within ±0.08 of click position;
  - 90 BPM and 140 BPM same criteria (no octave error: 45/70/280 rejected);
  - white-noise burst → `bpm_conf < 0.2`, no grid lock;
  - envelopes: no per-frame Δ>0.35 outside kick frames; silence drop
    follows release τ (no vertical jump).
- Acceptance: `features` dict exact per §5; old keys unchanged; all 15
  existing tests still pass; engine fps unaffected on dev box (> 28 fps).

**Phase B — hidden perception layer + contract v2**
- gamma/desat/crossfade in engine (§6.1, 6.2, 6.4), palettes util + new ctx
  (§7), z in positions + roles.
- Acceptance: no output = flicker above guardrail w/ new reference effects;
  legacy effects pass through unchanged behavior (gamma applies, that's fine).

**Phase C — presets rewrite in the new grammar**
- Ship: `scene_pulse`, `spectrum_sweep` (freq→x axis), `shockwave` (proper wave
  physics w/ beat-origin), plus convert `rainbow`, `band_matrix`, `dim2warm`
  to palettes. Move strobe params behind "advanced" or derate their duty cycle.
- Acceptance: user subjectively approves; flicker test per §7 passes.

**Phase D — spatial editor (Zones canvas)**
- drag/z slider/grid snap v existing autosave; preview on Effects page
  (live dots from `/api/status` group pixels). No new backend besides ctx.

Keep out of scope: meter detection (4/4 fixed), chord/harmony extraction,
true 3D rendering, per-fixture effect layering, WASAPI loopback, FLAC decode.

## 11. Sendspin playback clock & 60 ms lookahead (2026-11-09)

Problem: Sendspin delivers PCM *ahead* of the audible moment, but features were
stamped with TCP arrival time. `_on_audio()` discarded both the server
timestamp and `send_ahead`, so the relationship between analysis and playback
was undefined. A negative sync trim could extrapolate the beat phase, but could
not announce an unknown future kick/snare.

Implemented:

- **Source timing** (`hueme/sendspin_source.py`): every audio chunk stores its
  playback time. `client.compute_play_time(server_timestamp_us)` and
  `client.now_us()` share aiosendspin's clock domain, so their difference is
  mapped onto Hueme's `time.monotonic()` (perf_counter/MONOTONIC_RAW is never
  compared directly). `read()` returns the play time of the block's first
  sample as `last_play_time`, correctly advancing through partially consumed
  server chunks; buffer trimming drops matching timing metadata. First chunk
  per connection logs play lead and `send_ahead`.
  `latency` / `stream_latency_s` expose the newest chunk's lead; saturated
  `send_ahead` values (0 / 2³²−1) only skip the log field, never playback.
- **Playback-keyed features** (`hueme/audio.py`): `AudioEngine` stamps each
  snapshot with `t_play` and keys `_history` by that time instead of arrival
  time. `get_features()` selects:

  ```text
  target = now + LOOKAHEAD_S - audio.latency_ms/1000
  LOOKAHEAD_S = 0.060   # fixed 60 ms anticipation
  ```

  Kick/snare triggers and envelopes now come from audio up to 60 ms before it
  is audible. Positive Sync trim still delays the lights; negative trim still
  adds phase lead. Sources without a playback clock (Snapcast, parec, WAV,
  synthetic) fall back to arrival time, so their behaviour is unchanged.
  Because the base timeline advances by 60 ms, an existing positive trim now
  lands 60 ms early; add ~60 ms to that trim to restore the old alignment.
- **Diagnostics**: snapshots carry `t_play`, `play_lead_ms`, `lookahead_ms`,
  `latency_s` and `stream_latency_s`; the audio settings hint shows stream lead
  plus the active analysis lookahead.
- **Guards**: a play time outside `now ± 4 s` falls back to arrival time; a
  backwards jump > 50 ms clears the history (reconnect/clock resync).

Tests (`python -m unittest`): partial-chunk play-time tracking in
`tests/test_sendspin.py`; future-audio selection/anticipation and no-future-leak
for `get_features()`.

## 12. Sync calibration UI (2026-11-09)

Manual trim alone is hard to judge against a moving effect. The Audio card now
has a **Sync test** toggle and live trim buttons:

1. Play music normally and press **▶ Sync test**. Every enabled group is
   overridden with a plain white flash: bright on each detected kick, with the
   beat-grid pulse as fallback when kick confidence is low. Normal effects and
   UDP overrides are bypassed while the test runs.
2. Nudge **−50 / −10 / +10 / +50 ms** until the white flash lands on the
   audible kick. Each press applies immediately at runtime
   (`POST /api/audio/latency`); the config is marked dirty and **Save** persists
   it. The number field in advanced settings uses the same live path.
3. Press **■ Stop sync test** to return to the selected effects.

Why no separate metronome click: the real stream is the only trustworthy
reference. It already includes Sendspin send-ahead, playback-clock conversion
and output latency. A browser-generated click would add another unsynced clock
domain. The test flash is driven by `env.kick` (sharp attack, 260 ms release)
and `beat.on_beat` only when `conf.bpm ≥ 0.35`, so silence/idle noise cannot
produce misleading flashes.

API: `POST /api/sync/test {"enabled": bool}` and
`POST /api/audio/latency {"ms": number}`. `sync_test` is included in
`/api/status`; `tests/test_sync.py` covers flash levels and latency clamping.
