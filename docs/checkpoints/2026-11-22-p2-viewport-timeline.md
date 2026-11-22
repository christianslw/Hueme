# Checkpoint — 2026-11-22 — P2 slice 2: viewport, timeline, inspector, Auto Show

## State

The viewport now has a clickable view cube with six orthographic faces and an
ORTHO/PERSP pill; the old Persp/Front/Top/Side buttons are gone from the
toolbar, and the status line moved to the bottom-right. The hint is one line
plus a `?` shortcut button.

The timeline ruler is split into a 28 px time row and a 24 px structure band.
Blueprint sections render as colour-coded strips, cue/drop markers as ticks with
labels that skip collisions, in both Layers and Analysis. The band is inside
`view.rulerH`, so clicking it seeks exactly like the old ruler.

Inspector parameter rows now carry a keyframe navigator (`◀ ◆ ▶`) and an inline
expression chip with the live value. Colour parameters can be keyframed and
render as a gradient strip with swatches in their lane; preset lanes show
readable parameter names.

Auto Show turns blueprint sections into one ordinary layer track, one clip per
section, in a single undoable commit. It never edits existing tracks.

## Changed

- `hueme_studio/static/studio.js` — view cube draw/hit-test, camera command
  refactor, viewport help binding, structure band, colour lanes, keyframe
  navigator, inline expression chips, Auto Show action, tooltip/translation
  polish.
- `hueme_studio/static/autoshow.js` — new pure section→track builder.
- `hueme_studio/static/studio.html` — viewport toolbar, hint, `?` button,
  Auto Show transport button and Datei menu command.
- `hueme_studio/static/ui.css` — viewport status position, expression chip and
  key-nav styles, parameter action column.
- `tests/test_autoshow.mjs` — 5 `node:test` cases.
- `docs/ui-system.md` — view cube, structure band, snapping, key nav, Auto Show.
- `docs/TODO.md`, `docs/PROGRESS.md` — board and handoff.

## Verification

```text
$ python3 -m pytest -q -W error
177 passed, 6 subtests passed

$ python3 -m compileall -q hueme_studio
$ node --check hueme_studio/static/{client,studio,live,icons,snap,autoshow}.js
$ node --test tests/test_snap.mjs tests/test_autoshow.mjs
# tests 9, pass 9, fail 0
```

Headless Chromium against `127.0.0.1:8092` (`--no-persist`):

- view cube top face → `ORTHO TOP`; pill → `PERSP TOP`;
- structure-band click → snapped seek `00:20.548`;
- colour keys committed with array values and a gradient lane;
- Auto Show dialog → 7 sections → 7 named clips, beat gate on build/drop;
- `/live` loaded with no console or page errors.

## Contracts

- View cube faces are straight orthographic views; the pill toggles projection.
  Menu/keyboard camera commands share `setCameraNamed()`.
- `view.rulerH = 52` (28 time row + 24 band). All seek/scrub hit tests stay on
  `y <= view.rulerH`; track rows start below the audio lane.
- Colour keyframes stay ShowDoc data. The timeline strip is an sRGB editor
  preview; compositor rendering remains Oklab.
- Auto Show appends one track and is one commit; section mapping lives in
  `static/autoshow.js` and is covered by `node --test`.
