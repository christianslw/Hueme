# Checkpoint — 2026-11-22 — Phase 2.5 Timeline Analysis

## State

The timeline has a Layers / Analysis switch. Analysis hides track rows and
shows the audio waveform with beat grid, section/drop context and every
envelope basis overlaid.

## Changed

- `hueme_studio/static/studio.html` — mode switch and legend container.
- `hueme_studio/static/studio.js` — analysis curve registry, legend/toggles,
  localStorage persistence, full-height waveform, spectrum strip, dominance
  readout, beat-snapped hover/scrub readout, Analysis pointer handling.
- `hueme_studio/static/ui.css` — analysis layout, legend and curve controls.
- `tests/test_studio_analysis.py` — deterministic sampling, blueprint curve
  clipping/structure, waveform unit normalization.
- `docs/ui-system.md` — Analysis mode contract.

## Verification

```text
$ python3 -m pytest -q -W error
175 passed, 6 subtests passed in 16.09s

$ python3 -m compileall -q hueme_studio
$ node --check hueme_studio/static/studio.js
syntax ok
```

Headless Chromium: switched to Analysis, confirmed 14 curve toggles, audio-less
show displayed `NO AUDIO`, All/None wrote the localStorage selection, hover and
click produced every visible value, and a click seeked to 20.817 s. Console
clean.

## Contracts

- Analysis uses `envelopeAt()`, the same values as expression lanes and the
  Python envelope providers.
- The view is a scope; it does not add a compositor effect or mutate the show.
- `beatsToDrop` is normalized 0..1 (1 = far from the next drop) to keep every
  plotted basis comparable.
- Visible-curve selection persists under `hueme.studio.analysisCurves.v1`.
