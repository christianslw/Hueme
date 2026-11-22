# EffectCraft reference analysis

Read-only review of `reference/effectcraft-main` (ArtCraft / EffectCraft, MIT OR
Apache-2.0, pure Rust). This document records **patterns worth borrowing**, not
code to copy. Hueme is a fixture-driven lighting NLE; EffectCraft is an
image/video compositor. Treat it as a usability and architecture reference, not
a port target.

Last reviewed: 2026-11-22

## What EffectCraft is

- 308 built-in effects / 2992 parameters; Effect Controls, Effects & Presets,
  Timeline, Graph Editor, viewer, render queue.
- JavaScript expressions (boa) with the After Effects object model, inline
  editor, syntax highlighting, autocomplete, bracket matching and an error strip.
- Deterministic stepped particle simulations (Particle Emitter 2D/3D) at 60
  steps/s, per-particle random seeds, cache keys and CPU/GPU parity.
- Audio Spectrum / Audio Waveform generators and audio waveform rows in the
  Timeline.
- Readable JSON project files (`.ecproj`), user animation presets (`.ecpreset`),
  Favorites / Recently Used in the effects library.
- MCP + CLI + control channel over one command registry.

## Borrow list

### 1. Effect Controls as a device rack

EffectCraft's strongest UX lesson is that an effect stack should read like a
rack, with every parameter visible and operable in place.

Worth adapting to Hueme's clip inspector and filter cards:

| Pattern | Hueme use |
|---|---|
| Effect header: enable switch, twirl, Reset, About, rename, drag reorder | Filter card header. We already have enable/reset/reorder/delete; add drag reorder and a compact info popover. |
| Per-parameter stopwatch | Our ◇ keyframe button already does this; keep it on every numeric filter parameter. |
| Keyframe navigator `◀ ◆ ▶` | Add previous/next key buttons to animated parameter rows, not just add/remove at playhead. |
| `Alt+click` stopwatch adds/removes an expression | Map to `Alt+click` on the `ƒ` button or parameter label. |
| Scrubbable numbers, typed entry, angles as `1x+45°` | We have scrub numbers. Keep angle/degrees formatting for rotational params if added. |
| Point crosshair and colour eyedropper | Not applicable to a room lighting preview, but the idea of “pick in the viewer” can apply to emitter position later. |
| Custom editors for curves/levels/histogram | Add only for parameters that truly need it; our colour palette editor is the first case. |
| Colour labels on effect headers | Optional: use track/accent colour rather than a new label system. |

Takeaway: keep the filter stack dense and flat. No “advanced” drawer; all
parameters are the interface.

### 2. Effects & Presets library

EffectCraft's library is built for repeat use:

- Search with match highlighting.
- Categories or one alphabetical list.
- **Favorites** (star, persisted in settings).
- **Recently Used** (last 8).
- **Animation Presets** (built-in + user `.ecpreset` files).
- Double-click or Enter applies to the selection; drag onto a layer applies there.
- Rows show effect id, category, GPU/32-bit badges and a favourite star.

Hueme already has search + collapsible categories + drag/drop. Add:

- Favourites and Recently Used sections, persisted in `localStorage`.
- Search match highlighting.
- A category/alphabetical switch.
- User looks saved from the Effect Builder (Phase 2.4).
- Keep single-click apply with the existing double-click guard; the guard
  already prevents duplicate commits.

### 3. Expression editor and diagnostics

EffectCraft's expression experience is far beyond a text field:

- Syntax highlighting, line numbers, word wrap, bracket matching.
- Autocomplete (`Tab` accepts) with a large language vocabulary.
- Inline expression editor under the property row in the Timeline.
- Error strip at the bottom of the viewer: warning, `n/m`, previous/next,
  click-to-reveal the failing property.
- Expression menu with common snippets/functions.

Hueme should borrow the **approach**, not the JS object model:

- Keep the safe AST evaluator (no `eval`/`exec`).
- Add a generator-first `ƒ` dialog: readable presets (Beat LFO, Stem Follower,
  Phrase Riser, Emitter Wave, Wiggle) that insert an editable expression.
- Show the live value at the playhead and the last error inline.
- Add token chips for variables/functions; clicking inserts at the cursor.
- Optional later: syntax-highlighted `<textarea>` overlay with line numbers and
  bracket matching. Do not build a full code editor before the generators are
  useful.
- Add a compact diagnostics strip in the timeline/viewport when any expression
  on the active show fails, with click-to-reveal.

### 4. Timeline and Graph Editor

EffectCraft's Timeline rows carry the same parameter controls as Effect Controls:
stopwatch, key navigator, inline expression editor, waveform row, A/V switches.
The Graph Editor adds value/speed graphs, Bezier handles, Easy Ease and
fit/auto-zoom.

For Hueme Phase 2:

- Add filter parameter rows to the timeline lanes with ◇ keys and `ƒ` state.
- Keep keyframe display simple: diamonds, hold squares, curve preview.
- Add previous/next key navigation on parameter rows.
- A full graph editor is P2; the existing keyframe lanes plus easing selector
  are enough for the first pass.

### 5. Audio waveform / spectrum timeline view

EffectCraft draws audio as a property row (Audio > Waveform) with min/max peaks,
follows layer levels and reversed playback, and offers Audio Spectrum/Waveform
generators with parameters such as start/end frequency, frequency bands,
maximum height, audio duration/offset, thickness, softness, inside/outside
colour, display options and side options.

For Hueme this becomes the new **Timeline Analysis view**:

- Full-height waveform only, no fixture tracks.
- Beat grid, section markers and drop windows.
- Overlay every deterministic basis: `pulse`, `kick`, `snare`, `hat`,
  `energy`, `harmonic`, `percussive`, `prominence`, `bass`, `mid`, `high`,
  `sectionProgress`, `beatsToDrop`, `inDrop`.
- Toggle curves, show values at the playhead, show which basis dominates.
- A compact spectrum strip from bass/mid/high/energy.
- Reuse the same `envelopeAt()` math as the expression scope; do not create a
  second interpretation of the audio.

This is a scope/analysis view, not a new compositor effect. It should answer
“what is the music doing here?” before the user programs a look.

### 6. Particle systems

EffectCraft's Particle Emitter 3D parameters are a good shape for Hueme's new
`emitter_swarm`:

- `Birth Rate`, `Longevity`
- Producer position + radius X/Y/Z
- Physics: animation mode, velocity, gravity, resistance, extra, direction axis,
  gravity vector
- Particle: type, birth/death size, size variation, max opacity, birth/death
  colour, transfer mode
- Effect camera + random seed

Its implementation is a deterministic fixed-step simulation: 60 steps/s, a
per-particle random stream, bounded history, cache keys, and a CPU oracle.
Hueme does not need a stepped simulation. Use an **analytic** particle model:

```
birth_time(i) = i / birth_rate  (with fractional carry)
age = t - birth_time
p = emitter + direction * (velocity * age)
  + 0.5 * gravity * age^2
  + turbulence * fbm(seed + i, age)
```

This is stateless, deterministic, and cheap on 302 voxels. Add size/life fade,
birth/death colour, seed and expression-addressable emitter/spawn scalars.
Group parameters as `emitter/`, `physics/`, `particle/`, `colour/` in labels so
the inspector stays readable without adding nested groups.

### 7. Command/MCP surface

EffectCraft exposes one command registry to CLI, control channel and MCP. Hueme
already has one `ShowStore.commit` path for REST and MCP. Keep that. Phase 2
MCP additions worth having:

- `expression_validate` (safe evaluator, returns symbol/function errors).
- `analysis_curves` (waveform/curves at a time range for agents).
- `filter_keyframe_set` / `filter_keyframe_clear`.
- `user_preset_list` / `user_preset_apply`.

Do not add a second runtime or a separate MCP execution path.

## Do not copy

- 308 effects, image/video pipeline, codecs, AEP/Lottie, render queue.
- Full After Effects JavaScript object model; Hueme stays a safe scalar AST.
- Rust/egui/docking architecture.
- 38 blend modes; Hueme's five are enough until a look needs more.
- A full graph editor and syntax-highlighted code editor in the first Phase 2
  pass; generator presets and inline diagnostics deliver more usability per hour.
- EffectCraft's blue accent; Hueme keeps the amber production accent.

## Mapping to Phase 2

| Phase 2 item | EffectCraft lesson | Hueme decision |
|---|---|---|
| 2.1 Expressions | Generator-first, autocomplete, error strip | Extend safe AST functions/variables; generator presets + live value + inline error; no JS runtime. |
| 2.2 New presets | Particle Emitter 3D parameter shape | Analytic deterministic `emitter_swarm`; flat parameter labels with `emitter/` prefixes. |
| 2.3 Filter keyframes | Parameter rows carry stopwatch + key nav | Add stable filter uid, `filter:<uid>:<param>` paths, ◇/ƒ per filter param, lanes. |
| 2.4 Effect Builder | Animation presets + user presets + live preview | `<dialog>` recipe builder, `/api/user-presets`, blueprinted FX preview. |
| 2.5 Analysis view | Audio waveform row + Audio Spectrum params | Full-height waveform + every envelope basis overlay + dominance readout. |
| Library | Favorites / Recently Used / search highlight | Add local favourites/recent and highlight; keep drag/drop. |

## Open questions

- Filter instance id: add an explicit `uid` to `FilterSpec` or derive from
  position? Explicit is safer for reorder; default from index for old shows.
- User preset scope: global library vs show-local. Recommend global
  `config/user-presets.json`, like palettes.
- Analysis view: reuse the existing timeline canvas (recommended, zero new
  layout) or add a separate canvas. Reuse avoids a second time-mapping path.
- `env(name, phase)`: needs string literals in the safe AST. Allow them only as
  the first argument to `env`, reject them everywhere else.
