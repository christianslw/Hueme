# Hueme Studio — Build Progress & Session Checkpoints

This file is the durable handoff. A new session should read this first, then
continue at the first unchecked item. Do **not** re-do completed work.

Last updated: 2026-11-22 (Sprint 5, UI phase 1)

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
- The Studio may live at `/root/Hueme/test` in the current workspace; commands
  below use that project root.

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
cd /root/Hueme/test
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

Sprint 5 UI phase 1 is complete. Read `docs/PROGRESS.md`,
`docs/checkpoints/2026-11-22-sprint5.md`, `docs/ui-system.md` and
`docs/studio-server.md` first.

Phase 2, not started:

- Richer expression vocabulary and audio variables (`bass`, `mid`, `high`,
  `amp`, `rms`, `harmonic`, `pulse`, subdivision helpers, `smoothstep`, `mix`,
  `map`, `pulse`, `saw`, `tri`, `decay`), expression templates and live preview.
- New basic/particle presets with expression-addressable emitter position,
  spawn rate, spread, gravity, turbulence and lifetime.
- Filter parameter keyframes (only static values + presets are animatable now).
- Optional blueprint-backed FX preview (currently synthetic envelope).

Remaining external verification, not code work:

- Test against the real Music Assistant/Sendspin instance at
  `192.168.10.13:8927`; confirm pairing token and track metadata keys.
- Confirm the MQTT broker credentials and that HA discovery creates the four
  entities.
- Wire `Hal` into the Studio transport (still opt-in) and verify DDP/E1.31/Hue
  output with the real rig. Sprint 4 deliberately leaves HAL unwired.
- Confirm the WLED strip's 3- vs 4-channel wiring (still an open question).
