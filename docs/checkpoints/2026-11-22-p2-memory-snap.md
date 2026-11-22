# Checkpoint — 2026-11-22 — P2 slice 1: memory bound + magnetic snap

## State

The only unbounded server cache is now bounded. Preview thumbnails live in an
LRU `OrderedDict` capped at `MAX_PREVIEW_CACHE = 64`; scrubbing with unique
times can no longer grow it, and a cache hit refreshes recency. The reported
16 GB renderer/server spike was not reproduced locally, so the cap is the
concrete mitigation and the long-run soak remains an external check.

Timeline snapping now magnetises to markers, clip edges and the playhead within
8 px, with the beat grid as fallback. `Alt` releases the snap while moving or
trimming, and the dragged clip never snaps to its own edges.

The Sendspin pairing/identity files were untracked. They are runtime secrets,
already listed in `.gitignore`; the PSKs appeared in earlier history and must be
rotated/re-paired.

## Changed

- `hueme_studio/server.py` — `MAX_PREVIEW_CACHE`, `OrderedDict` cache,
  `_remember_preview()` LRU eviction, recency refresh on hits.
- `hueme_studio/static/snap.js` — new pure `nearestSnap()` selector.
- `hueme_studio/static/studio.js` — `snapTime()` collects grid, markers, clip
  edges and playhead; excludes the dragged clip; imports `nearestSnap`.
- `tests/test_studio_palettes.py` — cache bound and hit-recency regression.
- `tests/test_snap.mjs` — four `node:test` cases for magnet/grid/threshold/clamp.
- `config/sendspin_pairings.json`, `config/sendspin_identity.json` — untracked
  (`git rm --cached`), files stay on disk.
- `docs/TODO.md` — restructured board; sections 0 and 2 snapping checked.
- `docs/PROGRESS.md` — this slice, decisions and next start point.

## Verification

```text
$ python3 -m pytest -q -W error
177 passed, 6 subtests passed in 16.44s

$ python3 -m compileall -q hueme_studio
$ node --check hueme_studio/static/{client,studio,live,icons,snap}.js
$ node --test tests/test_snap.mjs
# tests 4
# pass 4
# fail 0
```

Headless Chromium (system Chromium, `--no-sandbox`) against a `--no-persist`
server on `127.0.0.1:8091`: `/studio` loaded, four ruler seeks snapped to
`00:07.397`, `00:20.548`, `00:33.699`, `00:46.849`, clip drag and `/live`
produced no console or page errors. Test show was not modified (`--no-persist`).

## Contracts

- Preview cache key remains `preset|filter:<id>:<t>:<w>:<h>`; entries are PNG
  bytes only. The cache is an editor aid, never show data.
- `nearestSnap()` is pure and DOM-free so it can be tested with `node --test`.
- Snapping operates on pointer time only; committed clip times are the snapped
  values, so REST/MCP see the same result as the UI.
- `config/sendspin_*` are per-install secrets and never tracked again.
