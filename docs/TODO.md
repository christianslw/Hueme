# Hueme Studio — TODO / task board

> **Read this after `docs/PROGRESS.md`.** Update this file whenever a task starts,
> lands or is deliberately deferred. One checkbox = one shippable change.

Last updated: 2026-11-22 (Phase 2 complete; P1 next)

## Current state

- Phase 2 is complete: expressions and diagnostics, four new presets, filter
  keyframes/lanes, blueprint FX + Effect Builder, and Timeline Analysis.
- Phase 1 UI and code-review bugs remain in place. Full suite: 175 passed.
- GitHub: `main` includes the Phase 2 slices, tag `phase1-ui-2026-11-22` is the
  earlier checkpoint.
- Reference analysis: `docs/effectcraft-analysis.md` (read-only).
- Server: `python3 -m hueme_studio --host 0.0.0.0 --port 8090`.

## P0 — Phase 2 core — COMPLETE

### 2.1 Expressions

- [x] Extend `hueme_studio/expressions.py` with `smoothstep`, `lerp`, `step`,
      `wrap`, `pulse`, `env(name, phase)`, `fbm(x, y, z, scale)`.
- [x] Allow string literals only where a function needs a named curve; keep
      attribute access and subscripts rejected.
- [x] Extend `Compositor._expression_context` with `bass`, `mid`, `high`,
      `harmonic`, `percussive`, `prominence`, `bar`, `barPhase`, `phrase`,
      `phrasePhase`, `sectionProgress`, `beatsToDrop`, `inDrop`.
- [x] Add `sectionProgress`, `beatsToDrop`, `inDrop` to both envelope providers
      (`deterministic_envelope` and `BlueprintEnvelope`).
- [x] Update `EXPRESSION_VARIABLES` and all validation/tests.
- [x] Mirror the same functions in the UI preview (`evalUiExpression`).
- [x] Upgrade the `ƒ` dialog: generator presets (Beat LFO, Stem Follower,
      Phrase Riser, Emitter Wave, Wiggle), live value, error message, token
      chips, readable expression output.
- [x] Add server-side expression diagnostics endpoint or reuse ShowDoc
      validation; show inline errors without blocking edits.

### 2.2 New presets

- [x] `color_wash` / `quad_gradient` — four-corner room wash with separate
      saturation/brightness and soft centre/edge falloff.
- [x] `wave_interference` — 2–4 superposed 3D wave fronts (frequency, amplitude,
      speed, angle, source count).
- [x] `shutter_matrix` — musical gate with attack/decay, emitter phase spread
      (`emitterNorm`), background floor.
- [x] `emitter_swarm` — deterministic analytic particles: emitter position and
      radii, birth rate, lifetime, velocity, gravity, resistance, turbulence,
      size, fade tail, birth/death colour, seed. No persistent simulation state.
- [x] Make numeric emitter/spawn params expression-addressable.
- [x] Document that `emitterIdx`, `emitterCount`, `emitterNorm`, `nx`, `ny`,
      `nz`, `distCenter` are per-emitter values available inside presets, not
      clip-level scalar expressions.

### 2.3 Filter keyframes

- [x] Add a stable filter instance id to `FilterSpec` (backward compatible).
- [x] Define `filter:<uid>:<param>` keyframe paths; never use display names.
- [x] Evaluate filter keyframes and expressions in the compositor without
      mutating the stored `FilterSpec`.
- [x] Add ◇ / ƒ buttons to each filter parameter row and active-key highlight.
- [x] Show filter parameter lanes in the timeline.
- [x] Tests for reorder, enable/disable, easing and colour interpolation.

### 2.4 Blueprint FX + Effect Builder

- [x] Pass `BlueprintEnvelope` and blueprint sections/drops into
      `/api/preview/effect` and preset thumbnails when the active show has audio.
- [x] Add `POST /api/user-presets` + `config/user-presets.json` for saved looks
      (preset + params + mask + filter stack).
- [x] Add “Effekt-Baukasten…” dialog: live preview, play/pause/scrub, recipe
      stack (preset/mask/filters), add/reorder/enable/disable, parameter editing
      with ◇/ƒ, save as user preset, apply to clip, insert as new clip.
- [x] Keep it a non-blocking `<dialog>`; Esc closes; no alert/confirm.

### 2.5 Timeline Analysis view (new)

Goal: a waveform/scope view that shows **only audio** and lets every analysis
basis be overlaid.

- [x] Add a Timeline mode switch: **Layers / Analysis** (optional split later).
- [x] In Analysis mode hide track rows; show a full-height waveform, beat grid,
      section markers and drop windows.
- [x] Overlay every available basis, each toggleable and colour-coded:
      `pulse`, `kick`, `snare`, `hat`, `energy`, `harmonic`, `percussive`,
      `prominence`, `bass`, `mid`, `high`, `sectionProgress`, `beatsToDrop`,
      `inDrop`.
- [x] Legend with checkboxes, “all/none”, and values at the playhead.
- [x] Show a compact spectrum strip (bass/mid/high/energy bars) and a
      “dominance” readout: which basis is strongest at the playhead or over the
      selected range.
- [x] Hover/scrub readout of every visible value; snap to beat grid.
- [x] Use the same values as the expression scope; no duplicate envelope math.
- [x] Persist the visible-curve selection in `localStorage`.
- [x] Tests: deterministic curve sampling, toggle rendering, no audio = empty
      state, blueprint min/max normalization.

## P1 — usability polish

- [ ] Library: Favorites and Recently Used sections (persist locally), search
      match highlighting, category/alphabetical toggle.
- [ ] Filter card drag reorder (keep up/down buttons for keyboard users).
- [ ] Double-click a parameter name to reveal its lane in the timeline.
- [ ] Effect header colour labels / reset / about popover.
- [ ] Keyboard: `U` / `Shift+U` reveal animated params, `Alt+click` expression.

## P2 — later

- [ ] Full graph editor for keyframes (value + speed).
- [ ] Syntax-highlighted expression editor with line numbers and autocomplete.
- [ ] User preset import/export (`.huemepreset`).
- [ ] More blend modes only if a real look needs them.
- [ ] FFT spectrum from the live audio element; blueprint curves are the
      deterministic default.

## Verification checklist

- [x] `python3 -m pytest -q -W error`
- [x] `python3 -m compileall -q hueme_studio`
- [x] `node --check hueme_studio/static/*.js`
- [x] Headless Chromium: no console/server errors; test the new UI path.
- [x] Update `docs/PROGRESS.md` and add a checkpoint under `docs/checkpoints/`.
