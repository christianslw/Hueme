# Hueme Studio — read this first

Any agent or LLM starting work in this repository must read, in order:

1. `docs/PROGRESS.md` — what is built, what changed, what is locked.
2. `docs/TODO.md` — the live task board and current phase.
3. `docs/effectcraft-analysis.md` — reference-app findings, only if the task touches
   effects, expressions, presets, the timeline or the library UI.
4. `docs/ui-system.md` — the locked visual/interaction contract.

Then read the files the task actually touches. Do not re-implement what already
exists; search the repository first.

## Non-negotiables

- Deterministic evaluation: same `ShowDoc` + timestamp = same frame. No `eval` /
  `exec` in Python, no blocking of the event loop.
- Pydantic v2 models with `extra="forbid"`; every show edit is one
  `ShowStore.commit` transaction.
- Vanilla ES modules, no frontend build step, no new dependencies unless the task
  truly needs them.
- No emojis in UI chrome, labels or toasts. No `alert()` / `confirm()`.
- Follow `docs/ui-system.md`: dense, calm, after-effects-like panels; one icon
  language; no decorative SaaS cards.
- Keep `config/rigs`, `config/shows`, `config/palettes.json` as runtime user data.
  The `reference/` directory is a read-only third-party clone and is git-ignored.

## Verification before calling work done

```bash
python3 -m pytest -q -W error
python3 -m compileall -q hueme_studio
node --check hueme_studio/static/client.js
node --check hueme_studio/static/studio.js
node --check hueme_studio/static/live.js
node --check hueme_studio/static/icons.js
```

For UI work, also open `/studio` in headless Chromium and check the console.
