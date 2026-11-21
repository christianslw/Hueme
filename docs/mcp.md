# Hueme Studio — MCP transport

`hueme_studio/mcp.py` exposes the studio as an MCP tool server. It implements
JSON-RPC 2.0 directly instead of depending on the MCP SDK: `initialize`,
`notifications/initialized`, `ping`, `tools/list`, `tools/call`, plus empty
`resources/list` / `prompts/list`.

## Transports

- **Streamable HTTP**: `POST /mcp` on the studio server (default
  `http://127.0.0.1:8090/mcp`). Responses are `application/json`; the
  `Mcp-Session-Id` header is issued on `initialize`. `GET /mcp` returns 405
  because the server has no SSE stream yet. `DELETE /mcp` returns 204.
- **stdio**: `python3 -m hueme_studio.mcp --stdio` proxies newline-delimited
  JSON-RPC to the running HTTP endpoint. It is a proxy by design: stdio clients
  must edit the live session, not a second in-process runtime.

```bash
python3 -m hueme_studio --host 127.0.0.1 --port 8090
python3 -m hueme_studio.mcp --stdio
```

## Tools

| Tool | Arguments | Effect |
|---|---|---|
| `studio_state` | — | full snapshot: rig, show, playback, catalogs |
| `rig_get` | — | working-copy rig and dirty flag |
| `show_get` | `show_id?` | one ShowDoc |
| `show_patch` | `patch`, `show_id?`, `label?` | deep-merge partial doc, one commit |
| `show_commit` | `document`, `show_id?`, `label?` | replace doc, one commit |
| `show_undo` / `show_redo` | `show_id?` | shared ShowStore history |
| `show_snapshot` | `show_id?`, `label?` | manual undo snapshot |
| `show_lint` | `show_id?` | rig/blueprint pre-flight report |
| `blueprint_get` | `show_id?` | attached AudioBlueprint |
| `blueprint_extract` | `path`, `show_id?` | analyse local WAV and attach |
| `transport_set` | playback fields | play/pause/seek/loop/gain/blackout |

`show_patch` merges objects recursively and replaces arrays wholesale. It is
the intended batch-edit tool: send one patch for N edits, get one undo entry
and one invalidation event. Invalid edits return `isError: true` with the
Pydantic message and never mutate the document.

## Telemetry

`GET /api/mcp/telemetry` returns counters (calls, errors, p50/p95) and the last
100 tool log entries. Every request also publishes a `{"type":"mcp"}` event on
`/ws/preview`; the studio MCP drawer renders both without polling the server
for frame data.

## Limits

- No server-initiated SSE / progress notifications / `listChanged`.
- No authentication: the same LAN trust model as REST.
- Tool schemas are flat; ShowDoc validation is authoritative.
