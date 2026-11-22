# Checkpoint — 2026-11-22 — Phase 2.4 blueprint FX + Effect Builder

## State

FX previews now follow the attached audio blueprint, saved user looks have a
global store and API, and the Studio has a pop-up Effect Builder for building,
previewing, saving and inserting looks.

## Changed

- `hueme_studio/fxpreview.py` — optional envelope provider for preset and clip
  renders; clip filter automation uses effective filters.
- `hueme_studio/server.py` — envelope provider passed to effect/preview
  thumbnails; user preset store, REST endpoints and snapshot field.
- `hueme_studio/userpresets.py` — validated `UserPresetStore` with atomic
  `config/user-presets.json` persistence.
- `hueme_studio/static/studio.html` / `studio.js` / `ui.css` — `Effect
  Builder…` button and dialog with live preview, transport, recipe stack,
  parameter editing, ◇/ƒ actions, save/apply/insert.
- `tests/test_studio_userpresets.py` — CRUD, persistence, validation and
  blueprint-envelope preview coverage.
- `docs/studio-server.md` — REST rows and user-look/preview contract.

## Verification

```text
$ python3 -m pytest -q -W error
172 passed, 6 subtests passed in 72.78s

$ python3 -m compileall -q hueme_studio
$ node --check hueme_studio/static/studio.js
syntax ok
```

Headless Chromium: opened Effect Builder from the timeline, saw a lit preview,
played and paused, added 3D Bloom, saved `Color Wash Look`, and inserted it as a
new clip (111 → 112 clips). Console clean.

## Contracts

- `config/user-presets.json` is global runtime user data, not show data.
- User looks store preset + params + spatial mask + filter stack only; they do
  not carry show-specific timing.
- Preview rendering is an editor aid; it never becomes a hardware output path.
- The builder is a native `<dialog>`; Esc and the close button dismiss it, and
  closing clears its preview object URL.
