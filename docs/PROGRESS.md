# Hueme Studio — Build Progress & Session Checkpoints

This file is the durable handoff. A new session should read this first, then
`docs/TODO.md`, then continue at the first unchecked item. Do **not** re-do
completed work.

Last updated: 2026-11-22 (P3 slice 1: Effect Builder module rack + preview performance)

## Ground rules

- Product: fixture-agnostic 3D lighting NLE ("After Effects for room lighting").
- **No fixed installation.** The rig is a loadable preset; any room, fixture
  count, coordinate or grouping is runtime data. See `docs/rig-sandbox.md`.
- Preserve `hueme/drivers.py` and `hueme/sendspin_source.py`. Extend protocols.
- Deterministic evaluation: same ShowDoc + timestamp = same RGB voxel buffer.
  Presets are stateless; inertia is a deterministic look-back, not a reactive
  callback.
- Do not ship untested monoliths. Verify every sprint with `pytest`.
- No `alert()` / `confirm()` in the frontend. Use toasts/modals.
- UI follows `docs/ui-system.md`: After Effects-like, no emojis, no generic SaaS
  card styling, no purple "AI" gradients.

## Sprint status

### Sprint 1 — Core Engine — COMPLETE

- [x] Repository audit; drivers and Sendspin preserved.
- [x] `hueme_studio/rig.py` — room, point/line voxels, transforms, logical and
  spatial groups, compiled arrays, rig preset library, transactional fixture/
  group edit helpers (`with_fixture`, `without_fixture`, `with_group`,
  `assign_to_group`).
- [x] `config/rigs/` — `hueme_loft.json` (current setup preset),
  `empty_loft.json`, `index.json` with active preset.
- [x] `hueme_studio/showdoc.py` — Pydantic v2 ShowDoc v1, Oklab, keyframes,
  transactional ShowStore with ≥50 undo levels, atomic persistence.
- [x] `hueme_studio/presets.py` — six stateless presets: solid, turrell_horizon,
  caustic_abyss, celestial_orrery, blackbody_forge, ma3_wings. Legacy ids are
  accepted as aliases.
- [x] `hueme_studio/filters.py` — mirror_symmetry, beat_gate, dim2warm, inertia,
  audio_mod.
- [x] `hueme_studio/compositor.py` — multi-track evaluation, five blend modes,
  fades, keyframes, spatial masks, per-fixture ZCL snap, deterministic envelope.
- [x] `hueme_studio/hal.py` — Rig-driven routing, Hue snap/smooth split, DDP
  RGBW 4-channel amber output, E1.31, HA sink, rate limiting, async wrapper.
- [x] `config/shows/dance_loft_v1.json` — 5 tracks / 22 clips / 6 presets / 5
  filters / all mask kinds. `config/shows/index.json`.
- [x] Tests: 51 Studio tests (all with `-W error`) + 32 existing tests pass.
- [x] Checkpoint: `docs/PROGRESS.md`, `docs/ui-system.md`,
  `docs/rig-sandbox.md`, `docs/showdoc-v1.md`,
  `docs/checkpoints/2026-11-20-sprint1.md`.

### Sprint 2 — Server + Studio UI — COMPLETE

- [x] FastAPI app, REST transport, binary `/ws/preview`, static UI.
- [x] Rig preset REST: list/load/save/clone/create/delete/activate, live push.
- [x] Desktop `/studio` 4-panel After Effects layout per `docs/ui-system.md`.
- [x] Rig Manager: preset selector, add/move/delete fixture, X/Y/Z + transform
  fields, group editor, marquee selection/drag grouping.
- [x] Mobile `/live` deck.
- [x] Live push to all clients after REST edits; the same event bus is ready for
  MCP edits in Sprint 3.
- [x] `hueme_studio/server.py`, `hueme_studio/__main__.py`,
  `hueme_studio/static/` (`studio.html`, `live.html`, `ui.css`, `client.js`,
  `studio.js`, `live.js`).
- [x] Tests: 15 server tests; full suite remains green.
- [x] Docs: `docs/studio-server.md` (REST, frame format, socket protocol).

Sprint 2 deviations, both documented in `docs/studio-server.md`:

- The viewport is a dependency-free Canvas2D 3D projection, not Three.js.
  It renders the room, fixtures, cones, selection and camera presets offline.
- The timeline consumed the ShowDoc beat grid only in Sprint 2; Sprint 3 adds
  the deterministic AudioBlueprint waveform and section/drop overlay.

### Sprint 3 — MCP + Audio Blueprint — COMPLETE

- [x] MCP JSON-RPC 2.0 at `POST /mcp` (initialize, tools/list, tools/call,
  notifications, ping) and `python3 -m hueme_studio.mcp --stdio` proxy.
  No MCP SDK dependency; Streamable HTTP returns JSON, GET stream is 405.
- [x] Twelve transactional/read tools in `hueme_studio/mcp.py`: `studio_state`,
  `rig_get`, `show_get`, `show_patch`, `show_commit`, `show_undo`, `show_redo`,
  `show_snapshot`, `show_lint`, `blueprint_get`, `blueprint_extract`,
  `transport_set`. One call = one `ShowStore.commit`.
- [x] `hueme_studio/lint.py` — missing group/preset/filter errors, empty group
  and duration/tempo mismatches as warnings, missing blueprint as info.
- [x] `hueme_studio/blueprint.py` — content-addressed deterministic extractor
  reusing `hueme.show_analyzer`, `config/blueprints/` cache, waveform + beat
  stem curves, `BlueprintEnvelope` feeding the compositor env.
- [x] REST: `/api/shows/{id}/lint`, `/api/shows/{id}/blueprint` (GET/raw POST),
  `/api/mcp/telemetry`; MCP events ride the existing invalidate bus.
- [x] UI: real MCP drawer log + telemetry strip + tool filter, Audio button for
  WAV upload, waveform/section/drop rendering in the timeline.
- [x] Tests: `tests/test_studio_blueprint.py` (6) and
  `tests/test_studio_mcp.py` (7); full suite remains green.
- [x] Docs: `docs/mcp.md`, `docs/audio-blueprint.md`, `docs/studio-server.md`,
  this checkpoint.

### Sprint 4 — MA Sync + Home Assistant — COMPLETE

- [x] `hueme_studio/sync.py` — conservative `ShowMatcher` over `ShowDoc.match`
  (normalised keys, MA item ids, duration scoring, ambiguity rejection) and a
  `PlayheadPll` with hard snap + ±4 % rate trim. `MaSync.tick()` switches shows,
  pauses on unknown tracks and leaves manual transport alone when disconnected.
- [x] Runtime: `/api/sync`, `/ws/preview` `scope: "sync"` invalidations, Studio
  header `MA ...` status/toggle, `--sync` / `--sendspin-url` /
  `--sync-offset-ms` CLI. Sendspin source lifecycle is owned by the app.
- [x] `hueme_studio/ha.py` — MQTT discovery for playback switch, show select,
  gain number and status sensor; retained state, availability LWT, queued
  commands applied on the runtime tick. `--ha-discovery`, `/api/ha`.
- [x] `ShowStore` index carries the full `match` block for the matcher.
- [x] Dockerfile / `.dockerignore` / Compose example updated for the Studio
  server on 8090, `/app/config` bind mount and optional sync/HA flags.
- [x] Tests: `tests/test_studio_sync.py` (11) and
  `tests/test_studio_ha.py` (3); full suite remains green.
- [x] Docs: `docs/ma-sync.md`, `docs/home-assistant.md`,
  `docs/deployment.md`, `docs/studio-server.md`, this checkpoint.

### Sprint 4 follow-up — UI recovery + browser audio — COMPLETE

- [x] Fixed the frozen Studio UI: optional `MA` controls are null-guarded so a
  stale cached `studio.html` cannot stop `bindTimeline()`, and HTML/static
  assets send `Cache-Control: no-cache`.
- [x] `--no-persist` now means read-only, not empty: existing shows load and
  play, but edits are not written.
- [x] AudioBlueprint uploads now keep the original WAV beside the analysis.
  `GET /api/shows/{id}/audio` serves it with Range support.
- [x] Studio `<audio>` preview follows play/pause, timeline scrubbing and show
  switches; **Vol** slider controls local monitor volume. This is separate from
  MA playback.
- [x] Tests: 128 passed, 3 skipped; headless Chromium verified play, seek and
  audio playback; stale-page regression verified.

### Sprint 5 — UI phase 1 — COMPLETE

Requested direction: AE/Premiere-like editing, free effect adjustment, user
palettes, fixture-free FX preview, automatic layer names, layer context menus,
and one icon language.  Phase 2 (richer expression vocabulary and new particle
systems) is intentionally separate.

- [x] `hueme_studio/palettes.py` — global palette library. Seven built-in
  cosine palettes plus user palettes in `config/palettes.json`, atomic writes,
  Oklab interpolation, built-ins protected from overwrite/delete.
- [x] Preset palette params changed from a fixed `select` to `ParamSpec(type=
  "palette")`; custom palettes flow through `PresetUtils.gradient`.
- [x] REST: `GET/POST/DELETE /api/palettes`; palettes included in
  `/api/studio/state`. MCP gains `palette_list`, `palette_save` and
  `palette_delete`.
- [x] `hueme_studio/fxpreview.py` — fixture-free front-plane preset/filter
  renderer and dependency-free PNG encoder. One preview render at a time and a
  small cache prevent O(n²) spatial filters from ballooning memory.
- [x] REST: `GET /api/presets/{id}/preview.png`, `GET /api/filters/{id}/
  preview.png`, `POST /api/preview/effect`. Library thumbnails now use real
  effect renders, not isometric rig point clouds.
- [x] Studio viewport **FX** toggle: front view of the selected/active clip with
  no fixture glyphs or cones, refreshed while scrubbing/playing.
- [x] Filter Stack inspector: every filter parameter is editable, filters can be
  enabled, reset, reordered and removed.
- [x] Palette section in the library and palette picker in effect parameters;
  create/edit/copy/delete palettes and apply to the selected clip.
- [x] Auto naming: clip display name follows its preset unless manually named;
  `Track.name_auto` follows the first clip's manual name or preset; manual
  rename freezes it. A toggle exposes both behaviours.
- [x] Layer context menu: copy, cut, paste, duplicate, rename, blend-mode
  submenu with checkmarks, mute/solo, clipping, sub-layer and delete. Clip
  context menu has the same copy/cut/paste/duplicate/rename/delete set.
- [x] Unified Material Design Icons subset in `static/icons.js`; all transport,
  menu, filter, palette and context-menu glyphs use it.
- [x] Tests: `tests/test_studio_palettes.py` adds palette CRUD, auto-name and
  PNG preview coverage. Stale demo-show/rig assertions were made data-driven so
  user-authored `config/shows` and `config/rigs` stay runtime data.
- [x] Headless Chromium verified: clip selection, filter parameter edit, palette
  creation/apply, FX preview changes with playhead, layer context submenu, and
  track auto-name following a preset change.
- [x] Full suite: **161 passed, 6 subtests passed** with `-W error`.

### Sprint 5 follow-up — code-review fixes, push, reference analysis

- [x] Double-click in the preset/filter library can no longer create duplicate
  commits; a 350 ms `libraryActionOnce` guard covers the native click pair.
- [x] Expression lane preview now uses the real `envelopeAt()` values from the
  attached blueprint (or the deterministic fallback), not `kick=0` / `energy=0.5`.
- [x] Removed the unused `SYNC_OFFSET_KEY` localStorage writes from `/studio`
  and `/live`; server sync status is the only source of truth.
- [x] `extract_warm_channel()` now blends between a neutral and 1800 K
  subtraction vector. Pure amber and neutral white both land on W with no RGB
  residue/blue cast; warm whites keep a warm RGB residual.
- [x] Removed the orphaned `base = U.color_mix(...)` line in `shockwave_3d`;
  `strobe_blinder` now handles scalar or array `gate` without `float(array)`.
- [x] `/api/system/restart` schedules the re-exec through `BackgroundTasks`
  after the HTTP response is flushed.
- [x] MCP `_blueprint_extract` comment now reflects `handle_async`'s worker
  thread; `_create_show` uses `default_params("solid")`.
- [x] Pushed `main` to GitHub at `0c0af10`, tag `phase1-ui-2026-11-22`.
- [x] Added `AGENTS.md`, `docs/TODO.md` and `docs/effectcraft-analysis.md`.
- [x] Full suite after fixes: **162 passed, 6 subtests passed**.

### Phase 2 slice 1 — Expressions — COMPLETE

- [x] Safe expression vocabulary now includes `smoothstep`, `lerp`, `step`,
  `wrap`, `pulse`, `env(name, phase)` and `fbm(x, y, z, scale)`. String
  literals are accepted only as the `env()` curve name; attribute access,
  subscripts and all other non-numeric constants stay rejected.
- [x] `EXPRESSION_VARIABLES` and `Compositor._expression_context` carry `bass`,
  `mid`, `high`, `harmonic`, `percussive`, `prominence`, `bar`, `barPhase`,
  `phrase`, `phrasePhase`, `sectionProgress`, `beatsToDrop` and `inDrop`.
- [x] Both envelope providers return the full basis set; `BlueprintEnvelope`
  reads sections, drops and drop windows from the cached blueprint.
- [x] The `ƒ` button opens a non-blocking generator dialog with Beat LFO, Stem
  Follower, Phrase Riser, Emitter Wave and Wiggle presets, token chips, live
  playhead value and inline diagnostics.
- [x] `POST /api/expressions/validate` returns syntax/symbol errors and a
  deterministic value; the dialog checks it before committing.
- [x] Tests: `tests/test_studio_expressions.py`; full suite **167 passed,
  6 subtests passed**.
- [x] Headless Chromium verified the generator dialog, live value, validation
  call and committed expression chip with no console errors.

### Phase 2 slice 2 — New presets — COMPLETE

- [x] `color_wash` (alias `quad_gradient`) — four-corner room wash with
  saturation, centre softness, edge falloff and optional drift.
- [x] `wave_interference` — two to four superposed 3D wave fronts with
  frequency, amplitude, speed and angle controls.
- [x] `shutter_matrix` — musical matrix shutter with attack, decay,
  per-emitter phase spread and a background floor.
- [x] `emitter_swarm` — analytic deterministic particles with emitter position
  and radii, birth rate, lifetime, velocity, gravity, resistance, turbulence,
  size, fade tail, birth/death colour, seed and background.
- [x] Numeric emitter/spawn parameters accept clip expressions; per-emitter
  and per-voxel values are documented as preset-local variables in
  `docs/showdoc-v1.md`.
- [x] Tests: preset determinism, animation and expression-addressable spawn
  parameters; full suite **168 passed, 6 subtests passed**.
- [x] Headless Chromium verified all four presets in the library and a lit
  fixture-free FX render of Emitter Swarm with no console errors.

### Phase 2 slice 3 — Filter keyframes — COMPLETE

- [x] `FilterSpec.uid` is stable and backward compatible; legacy filters get
  `f<index>_<type>` (with a numeric suffix for duplicates) during validation.
- [x] Filter automation uses `filter:<uid>:<param>` in `clip.keyframes` and
  `clip.expressions`; display names are never addresses.
- [x] `Clip.effective_filters()` returns copies with keyframed/expression
  parameter overrides; the compositor and FX preview use them without mutating
  the stored stack.
- [x] Every numeric/colour filter parameter row has ◇ and ƒ controls with an
  active-key highlight. Timeline lanes show filter paths with readable names
  and parameter ranges, and reorder/enable still target the same instance.
- [x] Tests: reorder, disable, expression override and Oklab colour keyframe
  interpolation; full suite **169 passed, 6 subtests passed**.
- [x] Headless Chromium verified setting a filter keyframe, opening the ƒ
  dialog from a filter row, committing a filter expression, and revealing the
  parameter lane with no console errors.

### Phase 2 slice 4 — Blueprint FX + Effect Builder — COMPLETE

- [x] FX previews and preset thumbnails accept an envelope provider. The
  runtime passes the attached `BlueprintEnvelope` when the active show has
  audio, so previews use the same sections/drops/curves as playback.
- [x] `hueme_studio/userpresets.py` + `config/user-presets.json` store global
  looks (preset, params, spatial mask, filter stack) with atomic writes.
  REST: `GET/POST /api/user-presets`, `DELETE /api/user-presets/{id}`; the
  Studio snapshot includes `user_presets`.
- [x] `Effekt-Baukasten…` non-blocking `<dialog>`: live 320×180 preview,
  play/pause/scrub, preset and parameter editing with ◇/ƒ, mask controls,
  filter add/reorder/enable/disable/remove, save look, apply to clip and insert
  as a new clip. Esc closes; no alert/confirm.
- [x] Tests: user preset CRUD/persistence/extra-field rejection and
  blueprint-envelope preview rendering; full suite **172 passed, 6 subtests
  passed**.
- [x] Headless Chromium verified opening the builder, a lit live preview,
  play/pause, adding a bloom filter, saving a look and inserting it as a new
  clip with no console errors.

### Phase 2 slice 5 — Timeline Analysis view — COMPLETE

- [x] Timeline toolbar switch **Layers / Analysis**. Analysis replaces track
  rows with a full-height waveform, beat grid, section markers and drop
  windows.
- [x] Every basis is toggleable and colour-coded: `pulse`, `kick`, `snare`,
  `hat`, `energy`, `harmonic`, `percussive`, `prominence`, `bass`, `mid`,
  `high`, `sectionProgress`, `beatsToDrop`, `inDrop`. All/None buttons and the
  visible-curve selection persist in `localStorage`.
- [x] Legend shows values at the playhead; hover/click shows every visible
  value at the beat-snapped hover time and seeks the playhead.
- [x] Spectrum strip (bass/mid/high/energy) and dominance readout are drawn at
  the playhead. Audio-less shows render an explicit empty state.
- [x] Curves use the same `envelopeAt()` values as expression lanes; no second
  envelope implementation.
- [x] Tests: deterministic sampling, blueprint clipping/structure, waveform
  unit normalisation; full suite **175 passed, 6 subtests passed**.
- [x] Headless Chromium verified the mode switch, 14 curve toggles, All/None
  persistence, audio-less empty state, hover/scrub readout and a 20.8s seek
  with no console errors.

### P1 — usability polish — COMPLETE

- [x] Library rows are keyboard-operable divs with a star button. **Favorites**
  and **Recently Used** groups persist in `localStorage`
  (`hueme.studio.libraryFavorites.v1`, `hueme.studio.libraryRecents.v1`), also
  available from the row context menu.
- [x] Search matches are highlighted with `<mark>`; the new **Cat / A–Z**
  button switches category grouping for an alphabetical list and persists the
  choice (`hueme.studio.librarySort.v1`).
- [x] Filter cards reorder by dragging the card title; the up/down buttons
  remain for keyboard users. Drop target and dragged card have explicit states.
- [x] Double-clicking any parameter label opens its track lanes, focuses that
  parameter first and switches the timeline to Layers when needed.
- [x] `U` reveals animated parameter lanes for the selected clip/track;
  `Shift+U` collapses all lanes.
- [x] Filter header gains a colour-label button (cycles the five track identity
  colours), an info popover with the filter description and parameter count,
  and keeps enable/reset/reorder/delete.
- [x] `Alt+click` a parameter label opens the expression generator;
  `Alt+click` an active `ƒ` button removes the expression in one transaction.
- [x] `FilterSpec.color` is optional editor metadata only; it does not affect
  rendering and defaults to `""`.
- [x] Tests: filter colour label round-trip; full suite **176 passed, 6
  subtests passed**.
- [x] Headless Chromium verified favourites/recents, search highlighting,
  Cat/A–Z toggle, preset/filter recents, colour label commit, about popover,
  drag reorder, lane reveal, `U` / `Shift+U` and Alt+click expression removal
  with no console errors.

### P2 slice 1 — memory bound + magnetic timeline snap — COMPLETE

- [x] `StudioRuntime._preview_cache` is an LRU `OrderedDict` capped at
  `MAX_PREVIEW_CACHE = 64`; hits refresh recency and old entries are evicted.
  This bounds the only unbounded server cache; scrubbing with unique times can
  no longer grow it. The reported 16 GB spike was not reproduced locally.
- [x] Regression test requests 84 distinct preview times and asserts the cache
  stays at the cap and a repeated time is a hit
  (`tests/test_studio_palettes.py`).
- [x] Timeline snapping now magnetises to markers, clip edges and the playhead
  within 8 px, with the beat grid as the fallback. `Alt` still releases the
  snap while moving or trimming clips; the dragged clip's own edges are
  excluded (`snapTime(value, clip.id)`).
- [x] Snap selection is a pure module, `hueme_studio/static/snap.js`, covered by
  `tests/test_snap.mjs` (`node --test tests/test_snap.mjs`, 4 cases).
- [x] `config/sendspin_pairings.json` and `config/sendspin_identity.json` are no
  longer tracked. They contain live pairing PSKs and were already listed in
  `.gitignore`; the PSKs appeared in earlier history, so re-pair/rotate them.
- [x] Full suite: **177 passed, 6 subtests passed**. Headless Chromium verified
  `/studio` seek snapping and `/live` with no console or server errors.

### P2 slice 2 — viewport, timeline and inspector polish + Auto Show — COMPLETE

- [x] The viewport axis triad is now a **view cube** at the top-right: six
  clickable faces (Front/Back/Left/Right/Top/Bottom), an ORTHO/PERSP pill and
  hover states. Face clicks are straight orthographic views; Shift+click shows
  the opposite side. The old Persp/Front/Top/Side buttons were removed from the
  viewport toolbar; menu/keyboard camera commands use `setCameraNamed()`.
- [x] Viewport chrome decluttered: the status moved to the bottom-right, the
  hint is one short line, and a `?` button opens the shortcut list.
- [x] Parameter rows gained a keyframe navigator (`◀ ◆ ▶`) that jumps the
  playhead to the previous/next key of that parameter, and an inline `ƒ` chip
  with the current expression and live value. Filter parameter rows use the
  same expression chip.
- [x] Colour parameters are keyframeable (◇ only, no scalar ƒ). Their timeline
  lane is a gradient strip with colour-swatch diamonds; preset parameter lanes
  now use readable labels instead of raw keys.
- [x] The timeline ruler grew a **structure band** (ruler 28 px time row + 24 px
  band). Blueprint sections are colour-coded strips, cue/drop markers are ticks
  with collision-free labels, and both Layers and Analysis show it. Clicking
  the band still seeks with snapping because the band stays inside `rulerH`.
- [x] **Auto Show** (`static/autoshow.js`): blueprint sections become one
  ordinary layer track with one clip per section, numbered names (`Verse 1/2`),
  a preset per section label, `beat_gate` on build/drop, fades and clamped,
  non-overlapping ranges. It is one `commitShow` transaction and never touches
  existing tracks. Button in the transport plus Datei menu.
- [x] `tests/test_autoshow.mjs` covers preset mapping, numbering, clamping,
  overlap trimming and unique ids (5 cases).
- [x] Long inspector help moved into tooltips (Sync-Offset); the Show tab hint
  is translated and shortened.
- [x] The `ƒ` generator list gained **Sidechain Duck**
  (`val * (1 - 0.75 * env('kick', beatPhase))`), matching the board's ducking
  request.
- [x] TODO board audited against this file: the builder's live preview / save /
  apply / insert actions and the P1 library features are checked, partial audio
  and analyser items carry explicit notes, and PSK rotation is a separate open
  item because it is external work.
- [x] Full suite: **177 passed, 6 subtests passed**; `node --test` 9/9.
  Headless Chromium verified view-cube clicks and the ORTHO/PERSP toggle, band
  seeking, colour keyframe commits with a gradient lane, Auto Show creation
  (7 sections → 7 clips) on `/studio`, and `/live` with no console errors.

### P3 slice 1 — Effect Builder module rack — COMPLETE

- [x] Replaced the old two-column form dialog with a 1180×840 module rack:
  pipeline rail (Generator, Maske, filter modules, add-filter, saved looks),
  large preview, clip-local transport and a contextual inspector. Selecting a
  module shows only its parameters instead of dumping every form at once.
- [x] The builder keeps a **detached recipe** and now owns local keyframes and
  expressions for generator and filter parameters. `◇`/`ƒ` write into the
  recipe; Apply and Insert carry the automation into the clip. The expression
  dialog accepts a transient context/commit, so it never touches the show
  until the user applies.
- [x] Colour parameters keyframe as swatches; filter parameters use
  `filter:<uid>:<param>`; removing a filter drops its automation.
- [x] Saved looks are listed in the rail and load with one click. Save Look
  stores preset + params + mask + filters (looks intentionally exclude
  automation).
- [x] Preview is clip-local (transport shows 0..clip duration) but renders at
  the show time, so blueprint envelopes, sections and beat values match the
  clip's real position. `_render` now takes an explicit `local_t` for params
  and filters.
- [x] Preview cap raised to 320×180. `mirror_symmetry` uses an exact O(n)
  mirror mapping on the regular front grid instead of an O(n²) nearest search;
  a filtered clip dropped from **7.3 s to 9 ms** per frame. Preview requests
  are queued instead of dropped while one is in flight.
- Tests: `tests/test_studio_fxpreview.py` covers the mirror mapping against
  brute force and the full editor resolution. Full suite: **179 passed, 6
  subtests**; the 9 JS cases stay green.
- [x] Headless Chromium: opened the builder on a clip, added Bloom, switched to
  the mask module, set a colour keyframe, committed a local expression, saved
  and loaded a look, applied to the clip and inserted a new clip with mask +
  expression. No console or page errors.

## Decisions already made

- Coordinates metres, Z up. Room defaults to `[6.5, 8.0, 5.1]` only in the
  empty template; every preset carries its own room.
- Voxels are the universal address space. Point = 1 voxel; line = N voxels.
  Fixture voxels occupy contiguous slices in rig order.
- Rig storage: `config/rigs/<id>.json` + `index.json.active`. There is no
  fixed `config/rig.json`.
- Transform order: scale → rotate around pivot → translate. See rig.py.
- Internal compositor colour: float32 sRGB 0..255; uint8 at preview/HAL edge.
- Keyframe colour interpolation uses Oklab. Keyframes are clip-local.
- Spatial mask coordinates are normalised room coordinates.
- `opacity` is a reserved keyframe parameter.
- ZCL snap is per fixture. HAL splits a Hue tick into snap (0x0080) and smooth
  groups because ZCL smoothing is frame-wide.
- Undo history per show, transactional, 50 snapshots by default.
- Hardware output opt-in; preview never requires physical output.
- `StudioRuntime` owns one working-copy rig; fixture/group edits are in memory
  until Save. Show edits commit and persist immediately.
- Preview frames are `HUE1` + 16-byte little-endian header + RGB bytes; see
  `docs/studio-server.md`.
- The UI is vanilla ES modules + Canvas2D, no build step or frontend framework.
- REST and MCP share `ShowStore.commit`; both push through the same
  invalidation event bus.
- MCP is a small JSON-RPC implementation (no SDK dependency). stdio mode is a
  proxy to a running studio server, not a second runtime.
- AudioBlueprint cache is content-addressed (`sha1` of WAV bytes) under
  `config/blueprints/`; `index.json` maps show id to key. Only 16-bit PCM WAV
  is accepted; conversion belongs upstream.
- Blueprint envelopes replace the synthetic beat-shaped envelope only while a
  blueprint is attached. ShowDoc timing remains authoritative for snapping and
  the beat grid.
- MA matching is conservative: exact normalised key or MA item id wins; a
  title-only match must be unambiguous; ties match nothing. The matcher never
  overrides a conflicting known artist.
- The PLL never seeks every frame: offsets below 750 ms are corrected by
  trimming playback rate at most ±4 %. Positive `offset_s` delays the lights.
- Sendspin disconnection leaves manual transport untouched. Reconnection
  resynchronises with a snap on the next valid track.
- HA commands are queued by the MQTT callback and applied on the runtime tick;
  paho's thread never mutates transport. HA is opt-in and never required for
  preview.
- `--no-persist` makes the show store read-only, not empty: existing shows are
  loaded and playable, but edits are not written back.
- Studio HTML/CSS/JS use `Cache-Control: no-cache` so a reload cannot mix an old
  page with new modules. Optional UI controls are null-guarded so a stale cached
  page cannot break timeline/event binding.
- The Studio container serves port 8090 and mounts config at `/app/config`;
  credentials, pairing state, caches and logs are excluded from the image.

- Custom palettes are global user data in `config/palettes.json`, not shown
  data. Built-in palettes are cosine gradients; custom palettes are discrete
  stops interpolated in Oklab. Built-ins can be copied but not overwritten.
- FX preview renders a flat front plane at the room's mid-depth through the
  preset and filter stack. It is an editor preview; it is not the hardware
  output path and has no fixture glyphs.
- Preview PNGs are serialized through one lock and cached by
  preset/filter/time/size. Spatial filters are O(n²), so the filter preview
  defaults to 48×27 and FX-with-filters to 48×27; the UI upscales the image.
- Clip names are automatic while `clip.name` is null. A manual clip name wins
  and also feeds an auto-named track. `Track.name_auto` is explicit; legacy
  tracks default to `false` so loading an old show does not rename rows.
- Auto naming runs inside `ShowStore` commits, so REST, MCP and UI edits share
  the same result. A manual track rename sets `name_auto=false`.
- Filter automation addresses `filter:<uid>:<param>`. Legacy filters get a
  deterministic uid during validation; reorder never retargets automation.

- Preview thumbnails are an LRU cache of 64 PNGs keyed by preset/filter +
  time + size. Time is part of the key because thumbnails are editor aids, not
  show data; the cap keeps scrubbing clients bounded.
- Timeline snapping prefers the nearest magnet (marker, clip edge, playhead)
  within 8 px of the pointer, otherwise it quantises to the beat grid. `Alt`
  bypasses both while moving or trimming.
- The view cube is the only viewport orientation control. Its faces are
  orthographic; the pill toggles projection and the Datei/Ansicht menu commands
  share `setCameraNamed()`. The status line lives bottom-right.
- The timeline ruler height includes the 24 px structure band so existing
  seek/scrub hit tests stay a single path (`y <= view.rulerH`).
- Auto Show lives in `static/autoshow.js` and maps section labels to presets.
  It always appends a new layer track in one `ShowStore.commit`, never edits or
  removes existing tracks, and leaves every generated clip editable.
- Colour keyframe lanes interpolate in sRGB for the editor strip; the
  compositor continues to interpolate colour keyframes in Oklab.
- The Effect Builder edits a detached recipe. Its keyframes/expressions are
  builder-local and only reach the show through Apply or Insert; Save Look
  stores the visual recipe without automation.
- Builder preview transport is clip-local, but the render uses the clip's show
  time so envelopes and sections match. `render_clip_rgb` passes `t` (show) and
  `local_t` (clip) separately.
- Editor previews render at up to 320×180. The front grid uses an exact mirror
  index map for `mirror_symmetry`; do not reintroduce an O(n²) nearest search
  in the preview path.
- P3's multi-generator/colour/transform pipeline is engine work; the current
  pipeline is Generator → Mask → Filter chain, with filters reorderable.
- User looks are global runtime data in `config/user-presets.json`, not show
  data. They store preset + params + mask + filter stack, not timing.
- Analysis mode is a scope over the same `envelopeAt()` values as expressions;
  it does not add a compositor effect or write to the show.
- Library favourites, recents and the category/alphabetical choice are local
  UI preferences, not show data. Filter colour labels are show data
  (`FilterSpec.color`) because they describe a specific effect instance.
- Animated-parameter lanes are editor state (`openTracks`), persisted with the
  layout in `localStorage`; they are never written to the ShowDoc.
- The Studio project root in this workspace is `/root/Hueme`; commands below
  use that project root.

## Open questions / external values to confirm

- WLED/SK6812 controller confirmed at `192.168.10.246:4048` (WLED 16.0.1).
  DDP works natively; `21324` is WLED's UDP sync port, not DDP.
- Strip confirmed 4-channel RGBW (`/json` reports `rgbw: true`), so
  `rgb_amber_1800k` and 4-channel DDP are correct. WLED's first bus is exactly
  290 SK6812 RGBW pixels; the rig matches. Two extra single LEDs are separate
  buses, so do not raise the rig to 292.
- DDP colour type must be `0x1B` for RGBW (`0x0B` for RGB). WLED only switches
  its 4-bytes-per-pixel parser when `(dtype & 0x38) >> 3 == 0b011`; `0x03`
  silently falls back to RGB24 and shifts every amber byte into the next pixel.
- `config/gateway.json` contains live credentials; never commit it.

## Verification commands

```bash
cd /root/Hueme
python3 -m pytest tests/test_studio_*.py -q -W error
python3 -m pytest -q -W error
python3 -m compileall -q hueme_studio
node --check hueme_studio/static/client.js
node --check hueme_studio/static/studio.js
node --check hueme_studio/static/live.js
node --check hueme_studio/static/icons.js
python3 -m hueme_studio --host 127.0.0.1 --port 8090
curl -s localhost:8090/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Next session start point

Phase 2 (slices 2.1–2.5), P1 usability polish, P2 slices 1–2 and P3 slice 1
are complete. Read `docs/PROGRESS.md`, `docs/TODO.md`,
`docs/effectcraft-analysis.md`, `docs/ui-system.md` and
`docs/studio-server.md` first.

The next board in `docs/TODO.md`: section 1 visual hierarchy; section 2 layer
hierarchy, palette/gradient keyframes and easing; section 3 the multi-generator
FX pipeline (P3 slice 1 replaced the dialog, the engine still renders one
generator); section 4 audio/emitter expressions and event triggers; section 5
targets, group HW output, start hub and `/live`. The renderer memory spike still
needs a long-run soak against the production host; the server's preview cache is
capped.

Completed in P3 slice 1:

- New Effect Builder module rack with pipeline rail, contextual inspector,
  saved looks, clip-local transport and 320×180 preview.
- Builder-local keyframes/expressions that travel with Apply/Insert.
- `mirror_symmetry` preview mapping fixed (7.3 s → 9 ms) and `local_t` plumbing
  for musically correct previews.

Completed in P2 slice 2:

- Clickable view cube, decluttered viewport chrome and `?` shortcuts.
- Keyframe navigator and inline expression chips in the inspector.
- Colour keyframes in the inspector and gradient colour lanes.
- Structure band with sections and collision-free cue labels.
- Auto Show scaffold from blueprint sections, one undo step.
- Sidechain Duck `ƒ` generator and a TODO board audit.

Completed in P2 slice 1:

- Bounded LRU preview thumbnail cache with regression test.
- Magnetic timeline snapping to markers, clip edges and playhead, `Alt` bypass.

Completed in Phase 2:

- Safe expression vocabulary, audio/structure variables, generator dialog and
  diagnostics.
- `color_wash`, `wave_interference`, `shutter_matrix`, `emitter_swarm`.
- Stable filter uids, filter parameter keyframes/expressions and timeline lanes.
- Blueprint-backed FX preview, global user looks and the Effect Builder.
- Timeline Analysis view with all envelope bases, spectrum and dominance.

Completed in P1:

- Library favourites, recents, search highlighting and Cat/A–Z grouping.
- Filter drag reorder plus header colour label and info popover.
- Double-click parameter lane reveal, `U` / `Shift+U` lane shortcuts and
  `Alt+click` expression add/remove.

Remaining external verification, not code work:

- Rotate/re-pair the Sendspin PSK that was exposed in git history; the files are
  untracked now but history still contains it.
- Test against the real Music Assistant/Sendspin instance at
  `192.168.10.13:8927`; confirm pairing token and track metadata keys.
- Confirm the MQTT broker credentials and that HA discovery creates the four
  entities.
- Wire `Hal` into the Studio transport (still opt-in) and verify DDP/E1.31/Hue
  output with the real rig. Sprint 4 deliberately leaves HAL unwired.
- Confirm the WLED strip's 3- vs 4-channel wiring (still an open question).
