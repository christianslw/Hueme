# Hueme Studio Server — transport

FastAPI app in `hueme_studio/server.py`. It owns one live studio session and
serves the desktop and mobile UI from `hueme_studio/static/`.

## Run

```bash
cd /root/Hueme
python3 -m hueme_studio --host 127.0.0.1 --port 8090
```

The default port is 8090 so the Studio can run beside the legacy engine, which
still defaults to 8080.

Routes:

- `/studio` — desktop four-panel editor
- `/live` — tablet/phone deck
- `/ws/preview` — binary preview frames + JSON control/events
- `/api/...` — REST

`python3 -m hueme_studio --help` lists `--rigs`, `--shows`, `--fps`,
`--no-persist`, `--gateway`, `--sync`, `--sendspin-url`, `--sync-offset-ms`,
`--ha-discovery` and the MQTT override flags. `--no-persist` is a read-only
session: existing shows still load and play, but edits are not written to disk.

## Session model

`StudioRuntime` keeps:

- a **working-copy rig** loaded from the active `RigLibrary` preset. Fixture
  and group edits mutate this copy and set `rig_dirty`; `POST /api/rig/save`
  writes it atomically. Activating another preset discards the draft only after
  an explicit `force=true` (the UI asks first).
- the active `ShowDoc` from `ShowStore`. Every show mutation is one
  transaction, persisted immediately, with the shared 50-level undo history.
- playhead, playing/loop/blackout flags, preview gain, FPS and subscribers.

`ShowStore.on_change` and every rig/edit handler enqueue invalidations. A
single asyncio ticker advances the playhead, drains events, evaluates the
compositor and broadcasts frames. Evaluation is deterministic; the ticker only
decides *when* to sample.

## REST

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | status, clients, voxel count |
| GET | `/api/studio/state` | rig + compiled voxels + show + catalogs + playback |
| GET | `/api/presets` | stateless preset catalog |
| GET | `/api/filters` | filter catalog |
| GET | `/api/rigs` | preset list + active id |
| GET | `/api/rigs/{id}` | preset document from disk |
| POST | `/api/rigs` | create preset (`name`, optional `preset_id`) |
| POST | `/api/rigs/{id}/clone` | clone preset |
| POST | `/api/rigs/{id}/activate?force=` | load preset into the working copy |
| DELETE | `/api/rigs/{id}?force=` | delete preset |
| GET | `/api/rig` | working-copy rig + dirty flag |
| PUT | `/api/rig` | replace the working copy |
| POST | `/api/rig/save` | save working copy as the active preset |
| POST | `/api/rig/save-as?force=` | change id/name and save |
| POST | `/api/rig/fixtures` | add fixture |
| PATCH | `/api/rig/fixtures/{id}` | deep-merge fixture patch |
| PUT | `/api/rig/fixtures/{id}` | replace fixture |
| DELETE | `/api/rig/fixtures/{id}` | remove fixture |
| PUT | `/api/rig/groups/{id}` | upsert group |
| DELETE | `/api/rig/groups/{id}` | remove group |
| POST | `/api/rig/groups/{id}/assign` | add fixture ids to a list group |
| GET | `/api/shows` | show list |
| GET | `/api/shows/{id}` | full ShowDoc |
| POST | `/api/shows` | create show |
| PUT | `/api/shows/{id}?label=` | commit one transaction |
| DELETE | `/api/shows/{id}` | delete show |
| POST | `/api/shows/{id}/undo` | undo |
| POST | `/api/shows/{id}/redo` | redo |
| GET | `/api/shows/{id}/history` | undo/redo depths and labels |
| POST | `/api/shows/{id}/snapshot` | push a manual snapshot |
| GET | `/api/shows/{id}/lint` | pre-flight lint against rig + blueprint |
| GET | `/api/shows/{id}/blueprint` | attached AudioBlueprint or 404 |
| POST | `/api/shows/{id}/blueprint?source=` | analyse raw 16-bit WAV body and attach |
| GET | `/api/shows/{id}/audio` | attached WAV for browser preview (Range) |
| POST | `/mcp` | MCP JSON-RPC 2.0 (Streamable HTTP) |
| GET | `/mcp` | 405; no server-initiated SSE stream |
| DELETE | `/mcp` | close the transport session |
| GET | `/api/mcp/telemetry` | call/error/latency counters + recent tool log |
| GET | `/api/transport` | playback state |
| POST | `/api/transport` | patch playing/time/loop/show/fps/gain/blackout |
| GET | `/api/sync` | Sendspin/MA match + PLL status |
| POST | `/api/sync` | toggle sync or set `offset_s` |
| GET | `/api/ha` | MQTT discovery connection/entity status |

Validation errors return `400` with readable detail; missing fixtures/groups/
shows return `404`. There is no authentication: bind to localhost or put the
server behind a trusted reverse proxy.

## MCP

`hueme_studio/mcp.py` implements the small stable MCP surface without the SDK:

```bash
# Streamable HTTP
curl -s localhost:8090/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# stdio (proxies the running server; one edit path, live invalidations)
python3 -m hueme_studio.mcp --stdio --url http://127.0.0.1:8090/mcp
```

Tools: `studio_state`, `rig_get`, `show_get`, `show_patch`, `show_commit`,
`show_undo`, `show_redo`, `show_snapshot`, `show_lint`, `blueprint_get`,
`blueprint_extract`, `transport_set`. `show_patch` deep-merges a partial
ShowDoc (arrays replace, objects merge); every write tool becomes exactly one
undoable `ShowStore.commit` and publishes the same invalidation event as REST.
`GET /mcp` is 405 by design; there is no SSE stream or server-initiated
notification yet.

The MCP drawer in `/studio` polls `/api/mcp/telemetry` and appends live
`{"type":"mcp"}` events from `/ws/preview`: calls, errors, p50/p95 latency and
a filterable tool log.

## AudioBlueprint

`hueme_studio/blueprint.py` wraps the offline `hueme.show_analyzer` DSP and
caches a compact blueprint by `sha1` of the WAV bytes:

- beats, downbeats, sections, drops, pre-drop vacuum windows
- per-beat harmonic, percussive and prominence curves
- a 1024-bucket waveform for the timeline
- `BlueprintEnvelope`, which supplies the same env keys as the synthetic
  compositor envelope (`pulse`, `kick`, `snare`, `hat`, `energy`, ...)

Only 16-bit PCM WAV is accepted. Upload from the timeline **Audio** button or
`POST /api/shows/{id}/blueprint` with the WAV as the raw request body. The
cache index maps show id to key, so the blueprint survives restarts. Without a
blueprint, playback uses `deterministic_envelope` exactly as before.

The upload also stores the original WAV beside the cached analysis. The browser
plays it in sync with play/pause and timeline scrubbing through
`GET /api/shows/{id}/audio`; the **Vol** slider controls local monitor volume.
This is separate from Music Assistant playback (which stays on the MA zone).
See `docs/audio-blueprint.md`.

## Lint

`GET /api/shows/{id}/lint` (also the `show_lint` MCP tool) returns
`{ok, errors, warnings, issues}`. Errors: missing target group, unknown preset
or filter. Warnings: empty target group, show/track duration drift, blueprint
duration/tempo drift. A missing blueprint is info, not a failure.

## MA sync + Home Assistant

Sprint 4 adds two optional integrations, both off unless configured:

- `hueme_studio/sync.py` matches the current Sendspin item to a ShowDoc's
  `match` block and locks the playhead with a small PLL. `GET/POST /api/sync`;
  the Studio header `MA ...` chip toggles it. See `docs/ma-sync.md`.
- `hueme_studio/ha.py` publishes four MQTT discovery entities (playback switch,
  show select, gain number, status sensor) and queues commands for the runtime
  tick. `GET /api/ha`; enable with `--ha-discovery`. See
  `docs/home-assistant.md`.
- `create_app(..., sync_source=..., ha_bridge=...)` keeps both injectable for
  tests; no broker or Sendspin server is required for the default preview.

## Preview socket

Connect to `/ws/preview`. The server immediately sends:

1. a text `{"type":"hello","state":<snapshot>}` message, then
2. one binary frame.

### Binary frame

Little-endian header, 16 bytes, followed by `count * 3` RGB bytes in rig voxel
order:

```
magic   4s   "HUE1"
count   u32  voxel count
time    f32  show time in seconds
flags   u32  bit0 playing, bit1 blackout, bit2 loop
```

The UI already has compiled voxel positions from `hello`/`/api/studio/state`,
so a frame only needs colours. `count == 0` is valid for an empty rig.

### Client messages

```jsonc
{"type":"play"}
{"type":"pause"}
{"type":"toggle"}
{"type":"seek","time_s":33.75}
{"type":"transport","playing":true,"loop":true}
{"type":"render","time_s":12.0}   // force one frame to this socket
{"type":"snapshot"}               // resend hello
{"type":"ping"}
```

### Server events

Text messages between frames:

```jsonc
{"type":"transport","transport":{...}}
{"type":"invalidate","scope":"rig","active":"hueme_loft"}
{"type":"invalidate","scope":"show","action":"commit","label":"trim clip","show_id":"dance_loft_v1"}
{"type":"error","message":"..."}
```

Clients refetch `/api/studio/state` on invalidations. The event bus is the same
path MCP edits will use in Sprint 3; no extra plumbing is needed.

## Static UI

- Vanilla ES modules, no build step, no frontend framework.
- `client.js` owns REST, WebSocket, frame decoding and toasts.
- `studio.js` owns the viewport, inspector, rig manager and timeline.
- `live.js` is the mobile deck.
- `ui.css` is the locked design system from `docs/ui-system.md`.

Deviations from the Sprint 2 design note, both deliberate:

- The timeline draws the AudioBlueprint waveform, section boundaries and drop
  windows when one is attached. Without a blueprint it shows the ShowDoc beat
  grid only; it never draws a fake waveform.
- The viewport uses a small Canvas2D perspective projection instead of
  Three.js. It renders the room grid, fixture discs/strips, low-opacity cones,
  selection boxes and axis handles without an external JS dependency, so the
  studio works offline. Swap in Three.js if free-camera volumetrics need real
  depth.
- Viewport dragging writes `transform.translate`, so it stays correct under
  rotation/scale; hold Shift for Z. Axis handles are drawn but not draggable
  yet. Marquee selection and drag-to-group are implemented.

## Verification

```bash
cd /root/Hueme
python3 -m pytest tests/test_studio_*.py -q -W error
python3 -m pytest -q -W error
node --check hueme_studio/static/client.js
node --check hueme_studio/static/studio.js
node --check hueme_studio/static/live.js
```
