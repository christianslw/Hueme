# Hueme Studio — Phase 2 session prompt

Copy the block below into a new session.

```text
Read /root/Hueme/AGENTS.md first, then docs/PROGRESS.md, docs/TODO.md,
docs/effectcraft-analysis.md and docs/ui-system.md. Inspect the code before editing.

Project: /root/Hueme (Hueme Studio, after-effects-like lighting NLE).
Current state: commit 5aca407, tag phase1-ui-2026-11-22, 162 tests passing.
Server is stopped; restart it only after tests.

Implement Phase 2 from docs/TODO.md in this order, one slice at a time:
1) expressions (functions + audio/structure variables + generator dialog + diagnostics)
2) new presets (color_wash/quad_gradient, wave_interference, shutter_matrix, emitter_swarm)
3) filter-parameter keyframes + timeline lanes
4) blueprint-backed FX preview + pop-up Effect Builder
5) Timeline Analysis view: waveform only, overlay every available basis
   (pulse, kick, snare, hat, energy, harmonic, percussive, prominence,
   bass, mid, high, sectionProgress, beatsToDrop, inDrop), spectrum strip,
   dominance readout, per-curve toggles, playhead values.

Hard constraints:
- deterministic evaluation; no eval/exec in Python; no event-loop blocking
- Pydantic v2 extra="forbid"; one ShowStore.commit per edit
- vanilla ES modules, no build step, no new dependencies unless required
- no emojis, no alert()/confirm(), keep the existing amber UI and icon language
- keep config/rigs, config/shows, config/palettes.json as runtime user data
- reference/effectcraft-main is read-only and git-ignored

For each slice: implement, add tests, run
python3 -m pytest -q -W error
python3 -m compileall -q hueme_studio
node --check hueme_studio/static/*.js
then verify the UI path in headless Chromium. Update docs/PROGRESS.md and
docs/TODO.md after each slice, add a checkpoint under docs/checkpoints/,
then commit and push to origin main. Do not skip the docs or verification.
```
