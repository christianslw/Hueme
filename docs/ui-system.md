# Hueme Studio — UI System (locked direction)

This is the design contract for Sprint 2+. The goal is a professional,
purpose-driven lighting NLE that reads like a desktop post-production tool, not
a generic web dashboard. Reference points: After Effects panel density,
DaVinci Resolve color discipline, Ableton device restraint.

## Non-negotiables

- No emojis anywhere in chrome, buttons, labels, toasts, or empty states.
- No marketing hero sections, no rounded SaaS cards, no glassmorphism, no
  purple/blue AI gradients, no floating pill navigation.
- Dense, quiet, legible. Panels do the work; decoration does not.
- Every control has a clear function. No ornamental icons that do nothing.
- No `alert()` / `confirm()`. Use inline validation, non-blocking toasts, and a
  modal for destructive confirmation.
- Motion is functional only: 90–160 ms panel/resize transitions, no bounces.

## Desktop shell (/studio)

Four-panel After Effects geometry, edge-to-edge, 1 px separators:

```
┌────────────────────────────────────────────────────────────────────────┐
│ menubar: Hueme Studio · Show name · transport · preview toggle · MCP    │ 32px
├───────────────┬──────────────────────────────────┬─────────────────────┤
│ LEFT          │ CENTER                           │ RIGHT               │
│ 260px         │ fluid                            │ 300px               │
│ Presets       │ 3D Loft viewport                 │ Context Inspector   │
│ Rig Manager   │ (Three.js canvas)                │ Clip / Fixture      │
│ Filter Library│ camera presets, gizmo overlay    │ tabs, numeric fields│
├───────────────┴──────────────────────────────────┴─────────────────────┤
│ TIMELINE: Canvas2D, ruler, tracks, waveform, beat/bar grid, markers    │ 300px
└────────────────────────────────────────────────────────────────────────┘
```

- Left panel uses collapsible sections with 22 px headers, not cards.
- Right inspector is one continuous column; sections separated by 1 px rules.
- Timeline is the visual anchor: bars alternate only by a barely perceptible
  value shift; clips are flat rectangles with 2 px radius and a colored top
  edge for track identity.
- MCP drawer slides over the right panel, 360 px, JSON-RPC log with monospace
  timestamps and a tool filter. It must not resize the timeline.

### Rig Manager (sandbox, no fixed setup)

The rig is user data. The panel must make that obvious:

- Preset row: active rig preset dropdown, `New`, `Save`, `Save As`, `Clone`,
  `Delete`. Empty rig is a valid starting point.
- Fixture list: 22 px rows with visibility, protocol badge (`HUE`, `DDP`,
  `E131`, `HA`), fixture name and voxel count. No fixed fixture count anywhere.
- Add fixture: menu for Point / Line, then numeric X/Y/Z placement. A line
  exposes start/end/pixels.
- Inspector for a selected fixture: X/Y/Z, aim vector, beam angle, transform
  (translate, rotate, pivot, scale), protocol, address/host, color space,
  max FPS, ZCL snap.
- Multi-select with shift-click or marquee; dragging the selection onto a group
  row adds the fixture ids to that list group. Drag a fixture in the viewport to
  update its XYZ; axis handles constrain movement.
- Groups: create list / spatial box / spatial sphere; edit bounds numerically;
  rename and delete. `all` is immutable.
- A small “unsaved rig changes” dot appears in the preset row; saving is atomic.

## Live deck (/live)

- Tablet/phone first. One now-playing block, transport row, two sliders, one
  large momentary pre-drop blackout pad.
- Large tap targets (min 44 px), no decorative charts.
- Auto-show toggle is a square industrial switch, not an iOS pill.

## Type

- UI: Inter, `-apple-system`, `Segoe UI`, sans-serif. 11–12 px labels, 13 px
  body, 15 px section titles. Never below 10 px.
- Numeric fields and timecode: JetBrains Mono or `SFMono-Regular`, tabular
  numerals.
- Timecode format: `mm:ss.mmm` or `bar.beat.tick` in the ruler.

## Colour tokens

Use a cool neutral graphite base with one warm production accent. Amber is the
brand/highlight; avoid rainbow chrome.

```css
--bg-0: #111214;   /* app background */
--bg-1: #17181b;   /* panel background */
--bg-2: #1d1f23;   /* raised rows, clip body */
--bg-3: #25282d;   /* inputs, hover */
--line:  #2b2e34;  /* 1px separators */
--line-strong: #3a3e46;
--text-0: #e8e9ec; /* primary */
--text-1: #b3b6bd; /* secondary */
--text-2: #7c8089; /* muted labels */
--accent: #e8862e; /* amber: playhead, active tool, focus */
--accent-dim: #7a4618;
--ok: #58a66b;
--warn: #c9a34a;
--danger: #c45a52;
--viewport: #0c0d0f;
```

Track identity colours (only for the top edge + clip tint, never large fills):
`#d06b4a`, `#5e9aa8`, `#9b7fb5`, `#8fa65a`, `#b3925a`.

## Spacing & density

- 4 px base unit; 8/12/16 px panel padding.
- Row height 24 px, input height 24 px, toolbar 28 px.
- 1 px borders only. No drop shadows except the MCP drawer (0 8px 24px #0008).
- Focus ring: 1 px `--accent` outline with 1 px offset. Never remove focus.

## Components

- **Button:** flat 24 px, `--bg-3`, 1 px `--line-strong`, white text; primary
  action uses amber text/border, not a filled gradient. Active/pressed darkens.
- **Number field:** drag-to-scrub on the label, click to type, arrow keys step.
- **Select:** native-feeling menu, 24 px rows, checkmark for current value.
- **Toggle:** 28×14 px square-ish track, amber when on. No labels inside.
- **Slider:** 2 px rail, 10 px square handle, value shown in mono at right.
- **Toast:** bottom-left above timeline, 1 px border, no rounded pill, auto
  dismiss 4 s, max 3 stacked.
- **Modal:** centered 420 px, graphite, 1 px border, title in 13 px caps.
- **Keyframe:** 5 px diamond, amber selected, gray unselected; hold keys are
  square, not diamond.
- **Clip:** flat body, 1 px outline, top 2 px track colour, name in 11 px;
  fades drawn as diagonal edge overlays; selected outline is amber.
- **Playhead:** 1 px amber line, 7 px triangular head, no glow.

## Viewport

- Near-black background with a subtle floor grid (2 m major, 0.5 m minor) and
  room corner lines. No fog gradient unless it communicates depth.
- Fixtures: small emissive discs (spots) and a thin line with pixel ticks
  (strips). Selected fixture gets an amber wire box and axis handles.
- Volumetric cones use additive transparency; floor pools are soft elliptical
  gradients. Keep cone opacity low enough to read fixture positions.
- Camera presets: Persp, Front, Top, Side. Orbit with LMB, pan with MMB/Shift,
  zoom with wheel. Show a compact axis triad bottom-right.

## Interaction rules

- Single click selects; shift-click adds to selection; drag on empty canvas
  marquee-selects.
- Clip drag/trim is magnetic to the beat grid by default; hold Alt to bypass.
- Space = play/pause, S = split at playhead, M = mute selected track,
  Cmd/Ctrl+Z / Shift+Cmd/Ctrl+Z undo/redo.
- MCP edits appear as a single timeline transaction with a toast label
  (“MCP: 12 edits applied”); no modal interruption.
- Loading states use thin indeterminate bars, not spinners in the middle of the
  viewport.

## Accessibility

- Text contrast ≥ 4.5:1 for body, ≥ 3:1 for large labels.
- Never encode state by colour alone; pair with text or an icon-like glyph made
  from CSS strokes (not emoji).
- All controls keyboard reachable. Timeline clips focusable with arrow keys.
