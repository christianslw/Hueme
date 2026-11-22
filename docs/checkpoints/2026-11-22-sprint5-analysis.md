# Checkpoint — 2026-11-22 — review fixes, push, EffectCraft analysis

## State

- Sprint 5 UI phase 1 is complete and pushed.
- Seven code-review bugs are fixed and verified.
- `reference/effectcraft-main` was reviewed read-only; findings are in
  `docs/effectcraft-analysis.md`.
- `docs/TODO.md` is now the live task board. `AGENTS.md` makes it a read-first
  contract for every agent.
- Phase 2 has **not** been implemented yet.

## Git

- `main` commit: `0c0af10` — `Sprint 5 UI phase 1 + review bug fixes`
- tag: `phase1-ui-2026-11-22`
- remote: `https://github.com/christianslw/Hueme.git`
- `reference/` is git-ignored.

## Bug fixes verified

1. Library double-click guard (`libraryActionOnce`, 350 ms) prevents duplicate
   preset/filter commits.
2. Expression lane preview uses real blueprint/deterministic envelope values.
3. Removed `SYNC_OFFSET_KEY` writes from studio and live clients.
4. `extract_warm_channel()` blends neutral/1800 K subtraction; pure amber and
   neutral white have no RGB residue, warm whites stay warm.
5. Removed the `shockwave_3d` orphan line; `strobe_blinder` accepts scalar or
   array gates.
6. `/api/system/restart` uses `BackgroundTasks` so the response flushes before
   `execv`.
7. MCP `_blueprint_extract` comment corrected; `_create_show` uses
   `default_params("solid")`.

## Docs added

- `AGENTS.md` — read-first contract and non-negotiables.
- `docs/TODO.md` — live task board, including the Timeline Analysis view.
- `docs/effectcraft-analysis.md` — borrow list, do-not-copy list, Phase 2 map.

## Verification

```text
$ python3 -m pytest -q -W error
162 passed, 6 subtests passed in 16.34s

$ python3 -m compileall -q hueme_studio
$ node --check hueme_studio/static/studio.js
$ node --check hueme_studio/static/live.js
$ node --check hueme_studio/static/icons.js
syntax ok
```

Server restarted on `0.0.0.0:8090`.

## Next

Start Phase 2 at the top of `docs/TODO.md`:

1. Expression functions/variables + generator dialog.
2. New basic/particle presets.
3. Filter keyframes.
4. Blueprint FX + Effect Builder.
5. Timeline Analysis view.

Keep `docs/PROGRESS.md` and `docs/TODO.md` current after every slice.
