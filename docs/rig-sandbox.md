# Rig Sandbox — any installation, no code changes

Hueme Studio does **not** ship a fixed lamp installation in code. The physical
layout is runtime data: a complete `Rig` document saved as a named preset.
The shipped "Hueme Loft" rig is just the active preset; the empty loft and any
user-created rig follow the same path.

## Storage

```
config/rigs/
├── index.json          # preset list + active preset id
├── hueme_loft.json     # the current installation, editable
└── empty_loft.json     # start-from-scratch template
```

The active preset is selected by `index.json.active`. Nothing in
`hueme_studio/rig.py`, `compositor.py`, `hal.py` or the presets assumes a
fixture count, fixture id, room size or protocol mix.

## Library API (Sprint 1)

`hueme_studio.rig.RigLibrary`

- `list_presets()` — id, name, file, fixture/voxel counts, room bounds.
- `load(preset_id=None)` / `load_active()` — validated `Rig`.
- `to_compiled(preset_id=None)` — numeric voxel arrays.
- `create(name, fixtures=[...], groups=[...], room=Room(...), set_active=...)`
- `clone(source_id, new_id, name)`
- `save(rig, set_active=...)`
- `delete(preset_id)`
- `set_active(preset_id)`
- `refresh()`

Sprint 2 exposes the same operations through REST and the Rig Manager panel.

## Fixture placement and transforms

Fixtures have two layers:

1. **Local geometry** — a `point` (`pos`, `aim`, `beam_deg`) or a `line`
   (`start`, `end`, `pixels`, `aim`, `beam_deg`). This is authored data and is
   what the compositor expands into voxels.
2. **Runtime transform** — `translate`, `rotate_deg` (XYZ Euler), `pivot`,
   `scale`. Applied per point as:

   `p' = R * ((p - pivot) * scale) + pivot + translate`

   The aim vector is rotated by the same rotation. Identity transform means the
   geometry is already in world coordinates.

The default rig uses geometry directly. The UI can either drag a point (update
`geometry.pos`) or use the transform fields for a strip/scaled fixture; both
are persisted in the rig preset.

## Grouping

Groups are data, not enums in code:

- `all` — every voxel in the rig.
- `list` — explicit fixture ids, editable from the Rig Manager.
- `spatial_box` — min/max world bounds, recalculated at compile time.
- `spatial_sphere` — centre/radius, recalculated at compile time.

Spatial groups automatically pick up newly added fixtures if they fall inside
the volume. List groups are edited by dragging a selection into a group or by
using the group editor. Shows reference group ids, so moving a fixture inside a
group never requires editing the show.

## Protocol routing

Each fixture carries an `output` block (`hue_z2m`, `wled_ddp`, `e131`,
`ha_light` or `none`). HAL iterates the compiled rig and routes each fixture
independently. Adding a new WLED strip or Hue spot is a rig edit, not a code
change.

## Show portability rules

- A ShowDoc targets group ids. `all` is always available.
- A rig preset is expected to provide the groups a show references. The Sprint 3
  linter will report missing groups before playback.
- Spatial masks use normalised room coordinates (0..1), so a preset can be moved
  between different room sizes without rewriting mask parameters.
- Presets render the whole room; the clip's target group is applied by the
  compositor. New fixtures therefore appear immediately when the show targets
  `all` or a spatial group.
