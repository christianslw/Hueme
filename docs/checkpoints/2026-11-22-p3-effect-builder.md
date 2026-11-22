# Checkpoint — 2026-11-22 — P3 slice 1: Effect Builder module rack

## State

The old Effect Builder was a 820 px two-column form dump with a small blurred
preview; the user called it out as the reason P3 exists. It is replaced by a
1180×840 module rack:

- left **Pipeline** rail: Generator, Mask, filter modules, add-filter, saved
  looks, look name + Save Look;
- centre preview with clip-local transport and a pipeline summary;
- right **module inspector** that only shows the selected module;
- footer: Close, Insert as Clip, Apply to Clip.

The builder keeps a detached recipe with local keyframes/expressions. `◇`/`ƒ`
write into the recipe and travel with Apply/Insert; saved looks intentionally
store no automation. The target clip is fixed when the dialog opens; a draft
opened without a selection can still be inserted.

## Preview performance and correctness

`mirror_symmetry` previously rebuilt an O(n²) nearest-voxel map for the preview
front grid on every render. A 192×108 filtered clip took **7.3 s per frame**.
The regular grid now uses an exact mirrored-index map (`_grid_mirror_sources`),
turning it into O(n); the same clip takes **9 ms** at 320×180. The preview cap
was raised to 320×180 and the builder queues refreshes instead of dropping them.

`_render` now takes an explicit `local_t`. The builder transport is clip-local,
but the render uses `previewStart + local_time`, so blueprint envelopes,
sections and beat values match the clip's real show position while presets,
params and filters evaluate against the clip-relative time as the compositor
does.

## Changed

- `hueme_studio/fxpreview.py` — `MAX_WIDTH/HEIGHT = 320/180`, exact grid mirror
  sources, `local_t` plumbing through `PresetContext` and `FilterContext`.
- `hueme_studio/static/studio.js` — new `openEffectBuilder()` with module rail,
  contextual inspector, look list, clip-local transport and builder-local
  automation; `expressionContext`/`openExpressionDialog` accept a time,
  transient context and commit override; `MASK_DEFAULTS`/`MASK_LABELS`.
- `hueme_studio/static/ui.css` — three-pane builder layout, module cards,
  pipeline chips, look list; removed the old two-column builder styles.
- `tests/test_studio_fxpreview.py` — brute-force mirror check and full-resolution
  preview check.
- `docs/TODO.md` — re-audited; legacy builder no longer counted, P3 slice 1
  checked, multi-generator pipeline kept open.
- `docs/PROGRESS.md`, `docs/ui-system.md` — this slice and the builder contract.

## Verification

```text
$ python3 -m pytest -q -W error
179 passed, 6 subtests passed

$ python3 -m compileall -q hueme_studio
$ node --check hueme_studio/static/{client,studio,live,icons,snap,autoshow}.js
$ node --test tests/test_snap.mjs tests/test_autoshow.mjs
# tests 9, pass 9
```

Headless Chromium against a `--no-persist` server on `127.0.0.1:8094`:

- dialog 1180×840, preview natural size 320×180, blob URL loaded;
- generator/mask/filter modules select correctly; Bloom added from the rail;
- colour keyframe set in the recipe; local expression
  `0.2 + 0.1 * sin(beatPhase * TAU)` committed and shown with live value;
- mask switched to Plane Sweep; look saved and loaded; Apply closed the dialog;
- Insert produced a new clip with the mask and expression;
- no console or page errors.

## Contracts

- Builder edits are detached until Apply/Insert. Save Look excludes automation.
- Filter automation paths are `filter:<uid>:<param>`; removing a filter drops
  its keyframes/expressions from the recipe.
- Preview transport is local; render time is the clip's show time. `t` and
  `local_t` are separate.
- Editor previews cap at 320×180 and never use the O(n²) mirror search.
- P3's multi-generator/colour/transform pipeline remains engine work; the
  current pipeline is Generator → Mask → Filter chain.
