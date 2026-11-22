# Checkpoint — 2026-11-22 — Phase 2.2 presets

## State

Four Phase 2 presets are available in the Studio library. All render from
`ShowDoc` time and parameters only; there is no per-frame particle state.

- `color_wash` (alias `quad_gradient`) — Basic, four-corner wash.
- `wave_interference` — Spatial FX, 2–4 superposed wave fronts.
- `shutter_matrix` — Spatial FX, beat-locked matrix shutter.
- `emitter_swarm` — Particles, analytic birth/death particles.

## Changed

- `hueme_studio/presets.py` — four renderers, registry entries, alias.
- `tests/test_studio_presets_filters.py` — expected registry count, alias,
  determinism/animation coverage and expression-addressable spawn params.
- `docs/showdoc-v1.md` — expression vocabulary and preset-local emitter values.

## Verification

```text
$ python3 -m pytest -q -W error
168 passed, 6 subtests passed in 15.95s

$ python3 -m compileall -q hueme_studio
$ node --check hueme_studio/static/studio.js
syntax ok
```

Headless Chromium: confirmed all four names in the preset library, applied
Emitter Swarm to a new clip and toggled FX mode; the fixture-free render was lit
and the status line read `FX FRONT · Emitter Swarm · Emitter Swarm · OHNE
FIXTURES`. Console clean.

## Deliberate choices

- `emitter_swarm` uses a periodic analytic age per particle, not cached
  simulation steps. Scrubbing and offline rendering always agree.
- The default particle size is large enough to read on sparse strip/spot rigs;
  size is a normal parameter and can be reduced.
- `quad_gradient` is an alias, not a duplicate catalogue entry.
