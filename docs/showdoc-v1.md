# ShowDoc v1 — implemented schema notes

Sprint 1 implements this in `hueme_studio/showdoc.py`. The JSON files in
`config/shows/` are validated by Pydantic v2 with `extra="forbid"`.

## Top level

```jsonc
{
  "schema_version": 1,
  "id": "dance_loft_v1",              // safe file/id slug
  "name": "Dance Loft V1",
  "duration_s": 236.25,
  "match": {
    "artist": "...",
    "title": "...",
    "normalized_keys": ["artist|title"],
    "ma_item_ids": [],
    "duration_s": 236.25
  },
  "timing": {
    "bpm": 128.0,
    "beat_offset_s": 0.0,
    "beat_grid_s": 0.46875,           // 60/bpm if omitted
    "markers": [{"t_s": 33.75, "name": "Drop 1", "kind": "drop"}]
  },
  "tracks": [ /* bottom-to-top layer order */ ],
  "notes": "",
  "created_at": null
}
```

Validation rules:

- track ids unique; clip ids unique across the whole show.
- clip `end_s > start_s`; fades fit inside the clip.
- clip keyframes are clip-local and must fit the clip.
- clips and markers must not exceed `duration_s`.
- marker kinds: `section`, `cue`, `drop`, `custom`.

## Track

```jsonc
{
  "id": "gallery_beams",
  "name": "Gallery Beams",
  "target_group": "galerie_oben",     // any group id in the ready rig
  "blend_mode": "add",                // normal | add | max | multiply | override
  "opacity": 0.92,
  "muted": false,
  "solo": false,
  "clips": []
}
```

Tracks evaluate in array order (bottom first). Any solo track suppresses all
non-solo tracks except muted ones. `all` always exists.

## Clip

```jsonc
{
  "id": "gal_drop1",
  "start_s": 33.75,
  "end_s": 67.5,
  "fade_in_s": 0.15,
  "fade_out_s": 2.0,
  "preset": "ma3_wings",
  "opacity": 1.0,
  "params": {},
  "keyframes": [],
  "spatial_mask": {"kind": "none"},
  "filters": [],
  "snap_zcl": true,
  "name": "optional UI label",
  "color": "optional UI swatch"
}
```

Fades are smoothstep. Clip alpha = fade × effective opacity × spatial mask ×
target-group coverage.

## Keyframes

```jsonc
{"t_s": 8.0, "param": "brightness", "value": 1.0, "easing": "ease_in_out"}
```

- `t_s` is relative to clip start.
- `param` targets a preset parameter. `opacity` is reserved for clip opacity.
- Colour values are 3- or 4-component lists and interpolate in Oklab.
- Easing: `linear`, `hold`, `ease_in`, `ease_out`, `ease_in_out`, `exp_decay`.
- A keyframe's easing controls the segment starting at that keyframe. `hold`
  keeps the value until the next keyframe and is one of the ZCL snap triggers.

## Spatial masks

Coordinates are normalised room coordinates 0..1, independent of metres.

| kind | fields | semantics |
|---|---|---|
| `none` | — | full coverage |
| `plane_sweep` | axis, position, width, softness | soft slab perpendicular to axis |
| `sphere_narrow` | center, radius, softness | inside sphere, soft edge |
| `box` | box_min, box_max, softness | inside axis-aligned box |
| `parity` | modulo, remainder | voxel index % modulo == remainder |

`invert` flips any mask.

## Filters

`filters: [{"type": "...", "params": {...}, "enabled": true}]`

- `mirror_symmetry` — axis, mix. Mirror maps are cached per compositor.
- `beat_gate` — division, duty, floor, softness.
- `dim2warm` — amount, curve, warm_color.
- `inertia` — mix, tau_s. Deterministic look-back sampling, no frame state.
- `audio_mod` — source, depth, mode, warm_color. Uses the deterministic
  envelope; Sprint 3's AudioBlueprint replaces it when one is attached.

Filters run in declared order on the clip buffer. The compositor then applies
target group + spatial mask.

## Blend modes

Given source track colour `S`, source alpha `a`, destination `D`:

- `normal`: `D = S*a + D*(1-a)`
- `add`: `D = D + S*a`
- `max`: `D = max(D, S*a)`
- `multiply`: `D = D * (1 - a + a*S/255)`
- `override`: `D = S*a` inside the target group. Lower layers are discarded;
  fade-in rises from black. Use for blackouts and hard cue replacement.

## ZCL snap

`FrameResult.snap_zcl` is a boolean array indexed by fixture. A clip with
`snap_zcl: true` marks its target fixtures on:

- clip start or end within the snap window,
- a `hold` keyframe within the snap window,
- a quantized beat onset while the clip is active.

Only fixtures with `capabilities.zcl_snap` are marked. HAL splits Hue output into
snap and smooth groups so `HueZ2MDriver` can use `0x0080` (~5 ms) for the snap
group and the normal trailing ramp for everything else.

## Store API

`ShowStore` maintains one shared transaction history for UI and MCP edits:

```python
store = ShowStore("config/shows")
store.create(doc)
store.commit(show_id, new_doc, label="MCP: batch 12 edits")
store.apply(show_id, "rename", lambda current: mutate(current))
store.undo(show_id); store.redo(show_id)
store.snapshot(show_id, label="before risky edit")
store.history(show_id)
```

- history limit is configurable, minimum 50, default 50.
- one commit = one snapshot. `batch_apply_edits` will use one commit.
- new commits clear the redo branch.
- persistence is atomic (`tempfile` + `os.replace`), index written with docs.
- `on_change(show_id, doc, action, label)` is the future WebSocket/MCP hook.
