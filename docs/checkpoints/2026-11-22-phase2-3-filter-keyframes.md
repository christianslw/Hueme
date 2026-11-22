# Checkpoint — 2026-11-22 — Phase 2.3 filter keyframes

## State

Filter parameters can now be keyframed and expression-driven through stable
instance ids. The stored `FilterSpec` is never mutated during evaluation.

## Changed

- `hueme_studio/showdoc.py` — `FilterSpec.uid`, legacy uid backfill, and
  `Clip.effective_filters()`.
- `hueme_studio/compositor.py` — inertia and the filter stack use effective
  filter copies.
- `hueme_studio/fxpreview.py` — FX preview uses the same effective filters.
- `hueme_studio/static/studio.js` — ◇/ƒ actions on filter parameter rows,
  filter path labels/ranges in timeline lanes, colour keyframe support.
- `tests/test_studio_compositor.py` — uid stability, keyframe/expression
  overrides, reorder, disable and colour interpolation.
- `docs/showdoc-v1.md` — filter uid and `filter:<uid>:<param>` contract.

## Verification

```text
$ python3 -m pytest -q -W error
169 passed, 6 subtests passed in 15.84s

$ python3 -m compileall -q hueme_studio
$ node --check hueme_studio/static/studio.js
syntax ok
```

Headless Chromium: added Strobe Shutter, set a `filter:f0_strobe_shutter:duty`
keyframe at the playhead, opened the ƒ dialog from the filter row, committed a
Stem Follower expression on the same path, and revealed the parameter lane.
Console clean.

## Contracts

- Keyframe/expression addresses are `filter:<uid>:<param>`, never labels.
- Legacy shows without uids remain loadable; missing ids are assigned
  deterministically at validation and persisted on the next commit.
- Reorder and enable/disable do not retarget automation.
- Filter parameter values can be numbers or 3/4-component colours; colour
  keyframes interpolate in Oklab through the existing keyframe engine.
