# Checkpoint — 2026-11-22 — P1 usability polish

## State

The P1 board is complete. The library supports favourites, recents, search
highlighting and category/alphabetical grouping. Filter cards drag-reorder and
carry a colour label plus an info popover. Parameter labels reveal their
timeline lanes on double-click, `U` / `Shift+U` toggle animated lanes, and
`Alt+click` adds or removes an expression.

## Changed

- `hueme_studio/static/studio.html` — library sort button, updated hints.
- `hueme_studio/static/studio.js` — localStorage library preferences, star
  rows, `<mark>` highlighting, Cat/A–Z grouping, filter drag reorder, colour
  label, info popover, lane focus/reveal, `U` / `Shift+U`, Alt+click expression
  handling, filter colour commits.
- `hueme_studio/static/ui.css` — star, highlight, drag/drop and popover note
  styles; grab cursor on filter titles.
- `hueme_studio/static/icons.js` — MDI `star` and `star-outline` paths.
- `hueme_studio/showdoc.py` — optional `FilterSpec.color` editor label.
- `tests/test_studio_showdoc.py` — filter colour round-trip.
- `docs/showdoc-v1.md` — documents `FilterSpec.color`.
- `docs/TODO.md`, `docs/PROGRESS.md` — P1 closed, P2 next.

## Verification

```text
$ python3 -m pytest -q -W error
176 passed, 6 subtests passed in 15.84s

$ python3 -m compileall -q hueme_studio
$ node --check hueme_studio/static/{client,studio,live,icons}.js
syntax ok
```

Headless Chromium against `http://127.0.0.1:8090/studio` on a disposable show:
clip selection, expression chip removal via `Alt+click`, favourite star,
Favorites/Recently Used groups, search `<mark>`, Cat/A–Z persistence, preset
and filter recents, two filter cards, colour label commit, about popover,
drag reorder, double-click lane reveal, lane checkbox, `U` / `Shift+U`
persistence, no console errors. The disposable show was deleted afterwards.

The development server is running on `0.0.0.0:8090` with the new model.

## Contracts

- Library favourites/recents/sort are local UI preferences
  (`hueme.studio.libraryFavorites.v1`, `hueme.studio.libraryRecents.v1`,
  `hueme.studio.librarySort.v1`), not show data.
- `FilterSpec.color` is an editor label only; rendering ignores it.
- Lane visibility stays in `openTracks` / `openLanes` editor state and never
  writes to the ShowDoc.
- Dragging filter titles and the existing up/down buttons both reorder in one
  `ShowStore.commit`.
