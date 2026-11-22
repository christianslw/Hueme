# Hueme Studio — TODO / task board

> **Read this after `docs/PROGRESS.md`.** Update this file whenever a task starts,
> lands or is deliberately deferred. One checkbox = one shippable change.

Last updated: 2026-11-22 (after Sprint 5 UI phase 1, code-review fixes and push)

## Current state

- Phase 1 UI is complete: editable filter stack, global palettes, fixture-free FX
  preview, auto names, layer context menus, MDI icons.
- Code-review bugs 1–7 are fixed and verified. Full suite: 162 passed.
- GitHub: `main` at `0c0af10`, tag `phase1-ui-2026-11-22`.
- Reference analysis: `docs/effectcraft-analysis.md` (read-only).
- Server: `python3 -m hueme_studio --host 0.0.0.0 --port 8090`.

## P0 — Phase 2 core

### 2.1 Expressions

- [ ] Extend `hueme_studio/expressions.py` with `smoothstep`, `lerp`, `step`,
      `wrap`, `pulse`, `env(name, phase)`, `fbm(x, y, z, scale)`.
- [ ] Allow string literals only where a function needs a named curve; keep
      attribute access and subscripts rejected.
- [ ] Extend `Compositor._expression_context` with `bass`, `mid`, `high`,
      `harmonic`, `percussive`, `prominence`, `bar`, `barPhase`, `phrase`,
      `phrasePhase`, `sectionProgress`, `beatsToDrop`, `inDrop`.
- [ ] Add `sectionProgress`, `beatsToDrop`, `inDrop` to both envelope providers
      (`deterministic_envelope` and `BlueprintEnvelope`).
- [ ] Update `EXPRESSION_VARIABLES` and all validation/tests.
- [ ] Mirror the same functions in the UI preview (`evalUiExpression`).
- [ ] Upgrade the `ƒ` dialog: generator presets (Beat LFO, Stem Follower,
      Phrase Riser, Emitter Wave, Wiggle), live value, error message, token
      chips, readable expression output.
- [ ] Add server-side expression diagnostics endpoint or reuse ShowDoc
      validation; show inline errors without blocking edits.

### 2.2 New presets

- [ ] `color_wash` / `quad_gradient` — four-corner room wash with separate
      saturation/brightness and soft centre/edge falloff.
- [ ] `wave_interference` — 2–4 superposed 3D wave fronts (frequency, amplitude,
      speed, angle, source count).
- [ ] `shutter_matrix` — musical gate with attack/decay, emitter phase spread
      (`emitterNorm`), background floor.
- [ ] `emitter_swarm` — deterministic analytic particles: emitter position and
      radii, birth rate, lifetime, velocity, gravity, resistance, turbulence,
      size, fade tail, birth/death colour, seed. No persistent simulation state.
- [ ] Make numeric emitter/spawn params expression-addressable.
- [ ] Document that `emitterIdx`, `emitterCount`, `emitterNorm`, `nx`, `ny`,
      `nz`, `distCenter` are per-emitter values available inside presets, not
      clip-level scalar expressions.

### 2.3 Filter keyframes

- [ ] Add a stable filter instance id to `FilterSpec` (backward compatible).
- [ ] Define `filter:<uid>:<param>` keyframe paths; never use display names.
- [ ] Evaluate filter keyframes and expressions in the compositor without
      mutating the stored `FilterSpec`.
- [ ] Add ◇ / ƒ buttons to each filter parameter row and active-key highlight.
- [ ] Show filter parameter lanes in the timeline.
- [ ] Tests for reorder, enable/disable, easing and colour interpolation.

### 2.4 Blueprint FX + Effect Builder

- [ ] Pass `BlueprintEnvelope` and blueprint sections/drops into
      `/api/preview/effect` and preset thumbnails when the active show has audio.
- [ ] Add `POST /api/user-presets` + `config/user-presets.json` for saved looks
      (preset + params + mask + filter stack).
- [ ] Add “Effekt-Baukasten…” dialog: live preview, play/pause/scrub, recipe
      stack (preset/mask/filters), add/reorder/enable/disable, parameter editing
      with ◇/ƒ, save as user preset, apply to clip, insert as new clip.
- [ ] Keep it a non-blocking `<dialog>`; Esc closes; no alert/confirm.

### 2.5 Timeline Analysis view (new)

Goal: a waveform/scope view that shows **only audio** and lets every analysis
basis be overlaid.

- [ ] Add a Timeline mode switch: **Layers / Analysis** (optional split later).
- [ ] In Analysis mode hide track rows; show a full-height waveform, beat grid,
      section markers and drop windows.
- [ ] Overlay every available basis, each toggleable and colour-coded:
      `pulse`, `kick`, `snare`, `hat`, `energy`, `harmonic`, `percussive`,
      `prominence`, `bass`, `mid`, `high`, `sectionProgress`, `beatsToDrop`,
      `inDrop`.
- [ ] Legend with checkboxes, “all/none”, and values at the playhead.
- [ ] Show a compact spectrum strip (bass/mid/high/energy bars) and a
      “dominance” readout: which basis is strongest at the playhead or over the
      selected range.
- [ ] Hover/scrub readout of every visible value; snap to beat grid.
- [ ] Use the same values as the expression scope; no duplicate envelope math.
- [ ] Persist the visible-curve selection in `localStorage`.
- [ ] Tests: deterministic curve sampling, toggle rendering, no audio = empty
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

- [ ] `python3 -m pytest -q -W error`
- [ ] `python3 -m compileall -q hueme_studio`
- [ ] `node --check hueme_studio/static/*.js`
- [ ] Headless Chromium: no console/server errors; test the new UI path.
- [ ] Update `docs/PROGRESS.md` and add a checkpoint under `docs/checkpoints/`.
