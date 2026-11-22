# Checkpoint — 2026-11-22 — Phase 2.1 expressions

## State

The safe expression evaluator now has a generator-first editing workflow, the
full audio/structure vocabulary used by the Phase 2 presets and the Analysis
view, and an inline diagnostics endpoint. No scripting runtime was added.

## Changed

- `hueme_studio/expressions.py` — `smoothstep`, `lerp`, `step`, `wrap`,
  `pulse`, `env(name, phase)`, `fbm`; string literals restricted to the
  `env()` name argument.
- `hueme_studio/showdoc.py` — extended `EXPRESSION_VARIABLES`.
- `hueme_studio/compositor.py` — full expression context and deterministic
  fallback basis.
- `hueme_studio/blueprint.py` — `BlueprintEnvelope` structure variables from
  sections, drops and drop windows.
- `hueme_studio/server.py` — `POST /api/expressions/validate`.
- `hueme_studio/static/studio.js` — generator `<dialog>`, live value, token
  chips, inline errors, mirrored evaluator functions.
- `hueme_studio/static/ui.css` — diagnostic styling.
- `tests/test_studio_expressions.py` — function vocabulary, literal safety,
  context parity, blueprint structure, REST diagnostics.

## Verification

```text
$ python3 -m pytest -q -W error
167 passed, 6 subtests passed in 15.65s

$ python3 -m compileall -q hueme_studio
$ node --check hueme_studio/static/studio.js
syntax ok
```

Headless Chromium: opened the ƒ dialog on an opacity parameter, inserted Beat
LFO, saw the live playhead value, validated a `smoothstep`/`env` expression via
the server endpoint, committed it, and confirmed the expression chip. Console
was clean.

## Contracts

- Expressions remain a safe scalar AST; no `eval`/`exec` in Python.
- Audio/structure values are deterministic and bounded 0..1, including
  `beatsToDrop` (normalised proximity to the next drop).
- The UI mirror is for preview only; Python remains authoritative.
- Invalid expressions are rejected by `ShowDoc` validation, while the dialog
  still allows typing and shows errors inline.
