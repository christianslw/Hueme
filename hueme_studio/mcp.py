"""Minimal MCP server for Hueme Studio (Streamable HTTP + stdio proxy).

The transport is JSON-RPC 2.0 over ``POST /mcp``.  It intentionally implements
the small, stable MCP surface the studio needs -- ``initialize``, ``tools/list``
and ``tools/call`` -- without adding the MCP SDK dependency.  Every write tool
routes through ``StudioRuntime`` and therefore through the same
``ShowStore.commit``/invalidation path as REST and the UI.

``python3 -m hueme_studio.mcp --stdio`` is a newline-delimited JSON proxy to a
running studio server, so stdio clients edit the live session instead of a
second in-process runtime.  ponytail: no SSE stream / server-initiated
notifications; add GET /mcp SSE when a client needs progress or listChanged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
import urllib.error
import urllib.request
from bisect import bisect_right
from collections import deque
from typing import Any, Callable

import numpy as np

from hueme_studio import __version__
from hueme_studio.blueprint import BlueprintStore
from hueme_studio.compositor import deterministic_envelope
from hueme_studio.filters import FILTERS, filter_catalog
from hueme_studio.presets import default_params, get_preset, preset_catalog
from hueme_studio.rig import fixture_incomplete_reasons, hue_proxy_health
from hueme_studio.showdoc import (
    Clip,
    FilterSpec,
    Keyframe,
    Marker,
    ShowDoc,
    Track,
)

MCP_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}
SERVER_NAME = "hueme-studio"

# Tool schemas are deliberately flat: the studio is the source of truth for
# ShowDoc validation, so the schema only names arguments and types.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "studio_context",
        "description": (
            "Start here. Complete situational briefing: active show, playhead/beat, "
            "every track with blend/target/active clips, timeline markers and next "
            "section, attached audio analysis with sections/drops and the current "
            "deterministic envelope, rig/group/fixture/proxy/hardware status. "
            "Compact compared with studio_state, but includes all decision-relevant data."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"show_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "studio_state",
        "description": (
            "Full raw studio snapshot: complete rig document plus compiled voxel "
            "positions/groups, ShowDoc, catalogs, playback, sync, hardware. Heavy; "
            "prefer studio_context for normal reasoning."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "track_detail",
        "description": (
            "Maximum detail for one track: full track document, target group fixture "
            "and voxel coverage, every clip with timing/local time, preset metadata, "
            "raw params, keyframes, filters, masks and expressions, plus which clip "
            "is active at the playhead (or at_s)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "show_id": {"type": "string"},
                "track_id": {"type": "string"},
                "at_s": {"type": "number"},
            },
            "required": ["track_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "audio_detail",
        "description": (
            "Maximum detail for the attached AudioBlueprint: BPM, beat grid, sections, "
            "drops/drop windows/pre-drops, stem curve statistics, envelope at the "
            "playhead, beats around the playhead, waveform stats. Set include_beats/"
            "include_curves/include_waveform for the full raw arrays."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "show_id": {"type": "string"},
                "include_beats": {"type": "boolean"},
                "include_curves": {"type": "boolean"},
                "include_waveform": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "rig_get",
        "description": "Working-copy rig document, active preset id and dirty flag.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "show_get",
        "description": "Read one ShowDoc; omit show_id for the active show.",
        "inputSchema": {
            "type": "object",
            "properties": {"show_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "show_patch",
        "description": (
            "Deep-merge a partial ShowDoc into the current document and commit "
            "one undoable transaction. Arrays replace, objects merge."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "show_id": {"type": "string"},
                "patch": {"type": "object"},
                "label": {"type": "string"},
            },
            "required": ["patch"],
            "additionalProperties": False,
        },
    },
    {
        "name": "show_commit",
        "description": "Replace a ShowDoc wholesale and commit one undoable transaction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "show_id": {"type": "string"},
                "document": {"type": "object"},
                "label": {"type": "string"},
            },
            "required": ["document"],
            "additionalProperties": False,
        },
    },
    {
        "name": "show_undo",
        "description": "Undo the last show transaction.",
        "inputSchema": {
            "type": "object",
            "properties": {"show_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "show_redo",
        "description": "Redo the last undone show transaction.",
        "inputSchema": {
            "type": "object",
            "properties": {"show_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "show_snapshot",
        "description": "Push a labelled snapshot onto a show's undo history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "show_id": {"type": "string"},
                "label": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "show_lint",
        "description": "Validate a show against the rig and attached AudioBlueprint.",
        "inputSchema": {
            "type": "object",
            "properties": {"show_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "create_show",
        "description": (
            "Create an empty ShowDoc (one solid base track) with a generated id "
            "and optional match metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "duration_s": {"type": "number"},
                "bpm": {"type": "number"},
                "match": {"type": "object"},
            },
            "required": ["id", "name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "batch_apply_edits",
        "description": (
            "Apply atomic track/clip/keyframe/filter operations in one undoable "
            "ShowStore commit. Ops: add_track, update_track, delete_track, "
            "add_clip, update_clip, delete_clip, set_keyframe, delete_keyframe, "
            "add_filter, update_filter, set_markers, set_expression."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "show_id": {"type": "string"},
                "operations": {"type": "array", "items": {"type": "object"}},
                "label": {"type": "string"},
            },
            "required": ["operations"],
            "additionalProperties": False,
        },
    },
    {
        "name": "render_telemetry_strip",
        "description": (
            "Render a compact group brightness/colour matrix by sampling the "
            "compositor between start_s and end_s for LLM self-checking."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "show_id": {"type": "string"},
                "start_s": {"type": "number"},
                "end_s": {"type": "number"},
                "samples": {"type": "integer", "minimum": 2, "maximum": 128},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "auto_keyframes",
        "description": (
            "Stamp rhythm keyframes on clips from the AudioBlueprint (ML beats/"
            "stems) or the deterministic beat grid."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "show_id": {"type": "string"},
                "clip_id": {"type": "string"},
                "source": {"type": "string"},
                "param": {"type": "string"},
                "division": {"type": "string"},
                "depth": {"type": "number"},
                "threshold": {"type": "number"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "blueprint_get",
        "description": "Read the AudioBlueprint attached to a show.",
        "inputSchema": {
            "type": "object",
            "properties": {"show_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "blueprint_extract",
        "description": (
            "Analyse a local 16-bit PCM WAV path and attach the deterministic "
            "AudioBlueprint to a show. Repeat calls hit the content cache."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "show_id": {"type": "string"},
                "path": {"type": "string"},
                "sync_show": {"type": "boolean"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "palette_list",
        "description": "List built-in and user palettes with their colour stops.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "palette_save",
        "description": (
            "Create or update a custom palette. Omit id to create a new one; "
            "built-in ids cannot be overwritten."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "colors": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "minItems": 2,
                    "maxItems": 16,
                },
            },
            "required": ["name", "colors"],
            "additionalProperties": False,
        },
    },
    {
        "name": "palette_delete",
        "description": "Delete a custom palette. Built-in palettes are protected.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "transport_set",
        "description": "Patch playback: playing, time_s, loop, fps, gain, blackout, show_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "playing": {"type": "boolean"},
                "time_s": {"type": "number"},
                "loop": {"type": "boolean"},
                "fps": {"type": "number"},
                "gain": {"type": "number"},
                "blackout": {"type": "boolean"},
                "show_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
]

TRANSPORT_KEYS = {
    "playing",
    "time_s",
    "loop",
    "fps",
    "gain",
    "blackout",
    "show_id",
}


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class McpBridge:
    """JSON-RPC dispatcher bound to one :class:`StudioRuntime`."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.calls = 0
        self.errors = 0
        self.started_at = time.monotonic()
        self._latencies: deque[float] = deque(maxlen=200)
        self._log: deque[dict[str, Any]] = deque(maxlen=100)

    # -- protocol ---------------------------------------------------------
    async def handle_async(self, payload: Any) -> Any:
        """Run the synchronous dispatcher off the ASGI event loop.

        Audio analysis (``blueprint_extract``) and other tool work can take
        seconds; the preview ticker must keep rendering meanwhile.
        """
        return await asyncio.to_thread(self.handle, payload)

    def handle(self, payload: Any) -> Any:
        if isinstance(payload, list):
            responses = [self._one(item) for item in payload]
            return [item for item in responses if item is not None]
        return self._one(payload)

    def _one(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return _rpc_error(None, -32600, "invalid request")
        method = message.get("method")
        request_id = message.get("id")
        if not isinstance(method, str):
            return _rpc_error(request_id, -32600, "invalid request")
        is_notification = "id" not in message
        if is_notification and method != "notifications/initialized":
            return None
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _rpc_error(request_id, -32602, "params must be an object")
        start = time.perf_counter()
        try:
            result = self._dispatch(method, params)
            if is_notification:
                return None
            tool = params.get("name") if method == "tools/call" else None
            failed = isinstance(result, dict) and bool(result.get("isError"))
            self._record(
                method,
                start,
                tool,
                "tool returned an error" if failed else None,
            )
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except _RpcError as exc:
            self._record(method, start, None, str(exc))
            if is_notification:
                return None
            return _rpc_error(request_id, exc.code, str(exc))
        except Exception as exc:  # tool errors stay visible to the client
            self._record(method, start, None, str(exc))
            if is_notification:
                return None
            return _rpc_error(request_id, -32603, str(exc))

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = (
                requested if requested in SUPPORTED_VERSIONS else MCP_PROTOCOL_VERSION
            )
            return {
                "protocolVersion": version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False, "subscribe": False},
                },
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": (
                    "Hueme Studio co-director. Start with studio_context: it returns "
                    "the playhead/beat, all tracks with targets/blend/active clips, "
                    "markers/sections, audiodata and hardware state. Use track_detail "
                    "for every clip/keyframe/filter of one track, audio_detail for the "
                    "full audio analysis, and studio_state only when raw rig arrays or "
                    "catalogs are needed. Read with show_get; edit with show_patch/"
                    "show_commit or batch_apply_edits (one undoable transaction per "
                    "call); check show_lint and render_telemetry_strip before/after big "
                    "changes."
                ),
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "resources/list":
            return {"resources": self._resources()}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            uri = params.get("uri")
            if not isinstance(uri, str):
                raise _RpcError(-32602, "uri is required")
            return self._read_resource(uri)
        if method == "prompts/list":
            return {"prompts": []}
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or name not in self._handlers():
                raise _RpcError(-32602, f"unknown tool '{name}'")
            if not isinstance(arguments, dict):
                raise _RpcError(-32602, "tool arguments must be an object")
            try:
                result = self._handlers()[name](arguments)
                text = _json_text(result)
                return {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": result,
                    "isError": False,
                }
            except Exception as exc:
                return {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                }
        raise _RpcError(-32601, f"method '{method}' not found")

    # -- tools ------------------------------------------------------------
    def _handlers(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        return {
            "studio_context": self._studio_context,
            "studio_state": lambda _a: self.runtime.snapshot(),
            "track_detail": self._track_detail,
            "audio_detail": self._audio_detail,
            "rig_get": lambda _a: {
                "active": self.runtime.rigs.active_id,
                "dirty": self.runtime.rig_dirty,
                "document": self.runtime.rig.model_dump(mode="json"),
            },
            "show_get": lambda a: self._show(a).model_dump(mode="json"),
            "show_patch": self._show_patch,
            "show_commit": self._show_commit,
            "show_undo": lambda a: self._undo(a),
            "show_redo": lambda a: self._redo(a),
            "show_snapshot": self._snapshot,
            "show_lint": self._lint,
            "create_show": self._create_show,
            "batch_apply_edits": self._batch_apply,
            "render_telemetry_strip": self._telemetry_strip,
            "auto_keyframes": self._auto_keyframes,
            "blueprint_get": lambda a: self._blueprint(a) or {},
            "blueprint_extract": self._blueprint_extract,
            "palette_list": lambda _a: {"palettes": self.runtime.palettes.list()},
            "palette_save": self._palette_save,
            "palette_delete": self._palette_delete,
            "transport_set": self._transport,
        }

    # -- context / resources ---------------------------------------------
    def _group_fixture_ids(self, group_id: str) -> list[str]:
        try:
            indices = self.runtime.compiled.group_indices(group_id)
        except KeyError:
            return []
        if indices.size == 0:
            return []
        fixture_index = self.runtime.compiled.voxel_fixture_index
        fixture_ids = self.runtime.compiled.fixture_ids
        return sorted(
            {
                fixture_ids[int(fixture_index[int(index)])]
                for index in indices
                if 0 <= int(fixture_index[int(index)]) < len(fixture_ids)
            }
        )

    def _audio_state(
        self, doc: ShowDoc, show_id: str, t: float
    ) -> dict[str, Any]:
        blueprint = self.runtime.blueprints.get(show_id)
        audio_available = self.runtime.blueprints.audio_path(show_id) is not None
        envelope_fn = (
            BlueprintStore.envelope(blueprint)
            if blueprint is not None
            else deterministic_envelope
        )
        env = envelope_fn(doc, t)
        sections: list[dict[str, Any]] = []
        current_section = None
        next_section = None
        for raw in (blueprint or {}).get("sections", []) or []:
            try:
                start = float(raw.get("start", 0.0))
                end = float(raw.get("end", doc.duration_s))
            except (TypeError, ValueError):
                continue
            entry = {
                "label": str(raw.get("label") or "section"),
                "start_s": round(start, 4),
                "end_s": round(end, 4),
                "duration_s": round(max(0.0, end - start), 4),
            }
            sections.append(entry)
            if start <= t < end:
                current_section = entry
            elif start >= t and next_section is None:
                next_section = entry
        curves = (blueprint or {}).get("curves") or {}
        curve_stats: dict[str, dict[str, float]] = {}
        for name in ("harmonic", "percussive", "prominence"):
            values = np.asarray(curves.get(name, []), dtype=np.float64)
            if values.size:
                curve_stats[name] = {
                    "mean": round(float(values.mean()), 4),
                    "min": round(float(values.min()), 4),
                    "max": round(float(values.max()), 4),
                }
        return {
            "attached": blueprint is not None,
            "audio_available": audio_available,
            "key": (blueprint or {}).get("key"),
            "source": (blueprint or {}).get("source"),
            "analyzer": (blueprint or {}).get("analyzer"),
            "duration_s": (blueprint or {}).get("duration_s"),
            "bpm": (blueprint or {}).get("bpm"),
            "beat_grid_s": (blueprint or {}).get("beat_grid_s"),
            "beat_count": len((blueprint or {}).get("beats", [])),
            "downbeat_count": len((blueprint or {}).get("downbeats", [])),
            "sections": sections,
            "section_at_playhead": current_section,
            "next_section": next_section,
            "drops": (blueprint or {}).get("drops", []),
            "drop_windows": (blueprint or {}).get("drop_windows", []),
            "pre_drops": (blueprint or {}).get("pre_drops", []),
            "envelope_at_playhead": {key: round(float(value), 4) for key, value in env.items()},
            "curve_stats": curve_stats,
            "analyzer_error": self.runtime.blueprints.last_analyzer_error,
        }

    def _track_summary(self, track: Track, t: float) -> dict[str, Any]:
        clips = track.clips
        active = [clip for clip in clips if clip.start_s <= t <= clip.end_s]
        return {
            "id": track.id,
            "name": track.name,
            "kind": track.kind,
            "parent_id": track.parent_id,
            "target_group": track.target_group,
            "target_fixture_ids": self._group_fixture_ids(track.target_group),
            "blend_mode": track.blend_mode,
            "opacity": track.opacity,
            "muted": track.muted,
            "solo": track.solo,
            "clipped_to_below": track.clipped_to_below,
            "height_px": track.height_px,
            "clip_count": len(clips),
            "active_clip_ids": [clip.id for clip in active],
            "active_clips": [
                {
                    "id": clip.id,
                    "name": clip.name,
                    "preset": clip.preset,
                    "start_s": clip.start_s,
                    "end_s": clip.end_s,
                    "local_t": round(max(0.0, t - clip.start_s), 4),
                    "opacity": clip.opacity,
                    "keyframes": len(clip.keyframes),
                    "filters": [spec.type for spec in clip.filters if spec.enabled],
                    "expressions": list(clip.expressions),
                }
                for clip in active
            ],
            "keyframe_count": sum(len(clip.keyframes) for clip in clips),
            "filter_count": sum(len(clip.filters) for clip in clips),
        }

    def _studio_context(self, args: dict[str, Any]) -> dict[str, Any]:
        doc = self._show(args)
        show_id = doc.id
        active_show = show_id == self.runtime.show_id
        t = float(self.runtime.playhead) if active_show else 0.0
        t = max(0.0, min(doc.duration_s, t))
        beat = doc.beat_context(t)
        markers = sorted(
            (
                {
                    "t_s": marker.t_s,
                    "name": marker.name,
                    "kind": marker.kind,
                    "color": marker.color,
                }
                for marker in doc.timing.markers
            ),
            key=lambda marker: marker["t_s"],
        )
        previous_marker = None
        next_marker = None
        for marker in markers:
            if marker["t_s"] <= t:
                previous_marker = marker
            elif next_marker is None:
                next_marker = marker
        rig = self.runtime.rig
        proxy_health = hue_proxy_health(rig)
        incomplete = fixture_incomplete_reasons(rig)
        fixtures = [
            {
                "id": fixture.id,
                "name": fixture.name,
                "folder_id": fixture.folder_id,
                "geometry": fixture.geometry.type,
                "protocol": fixture.output.protocol,
                "output_enabled": fixture.output.enabled,
                "incomplete_reasons": incomplete.get(fixture.id, []),
            }
            for fixture in rig.fixtures
        ]
        groups = [
            {
                "id": group.id,
                "name": group.name,
                "type": group.type,
                "fixture_ids": self._group_fixture_ids(group.id),
            }
            for group in rig.groups
            if group.type != "all"
        ]
        smart_groups = [
            {
                "id": group.id,
                "name": group.name,
                "fixture_ids": self._group_fixture_ids(group.id),
            }
            for group in rig.smart_groups
        ]
        return {
            "server": {"name": SERVER_NAME, "version": __version__},
            "show": {
                "id": doc.id,
                "name": doc.name,
                "duration_s": doc.duration_s,
                "playhead_s": round(t, 4),
                "playing": bool(active_show and self.runtime.playing),
                "loop": bool(active_show and self.runtime.loop),
                "blackout": bool(active_show and self.runtime.blackout),
                "gain": float(self.runtime.gain if active_show else 1.0),
                "bpm": doc.timing.bpm,
                "beat_grid_s": doc.timing.beat_grid_s,
                "beat_offset_s": doc.timing.beat_offset_s,
                "beat": {
                    "beat_index": int(beat["beat_index"]),
                    "beat_phase": round(float(beat["beat_phase"]), 4),
                    "bar_index": int(beat["bar_index"]),
                    "bar_pos": int(beat["bar_pos"]),
                    "bar_phase": round(float(beat["bar_phase"]), 4),
                    "on_beat": bool(beat["on_beat"]),
                    "on_downbeat": bool(beat["on_downbeat"]),
                },
                "marker_previous": previous_marker,
                "marker_next": next_marker,
                "markers": markers,
                "tracks": [self._track_summary(track, t) for track in doc.tracks],
                "track_count": len(doc.tracks),
                "clip_count": len(doc.all_clips()),
            },
            "audio": self._audio_state(doc, show_id, t),
            "rig": {
                "active": self.runtime.rigs.active_id,
                "dirty": self.runtime.rig_dirty,
                "fixture_count": len(rig.fixtures),
                "voxel_count": self.runtime.compiled.voxel_count,
                "fixtures": fixtures,
                "groups": groups,
                "smart_groups": smart_groups,
                "proxy_health": proxy_health,
                "hardware": self.runtime.hardware_status(),
            },
            "catalogs": {
                "preset_count": len(preset_catalog()),
                "filter_count": len(filter_catalog()),
            },
        }

    def _track_detail(self, args: dict[str, Any]) -> dict[str, Any]:
        doc = self._show(args)
        track_id = str(args.get("track_id") or "")
        if not track_id:
            raise ValueError("track_id is required")
        track = doc.find_track(track_id)
        if track is None:
            raise KeyError(f"track '{track_id}' not found")
        active_show = doc.id == self.runtime.show_id
        t = float(args.get("at_s", self.runtime.playhead if active_show else 0.0))
        t = max(0.0, min(doc.duration_s, t))
        group_voxels = 0
        try:
            group_voxels = int(self.runtime.compiled.group_indices(track.target_group).size)
        except KeyError:
            pass
        clips = []
        for clip in track.clips:
            local_t = max(0.0, min(clip.end_s - clip.start_s, t - clip.start_s))
            preset = get_preset(clip.preset)
            clips.append(
                {
                    **clip.model_dump(mode="json"),
                    "duration_s": round(clip.end_s - clip.start_s, 4),
                    "active": clip.start_s <= t <= clip.end_s,
                    "local_t_at_query": round(local_t, 4),
                    "preset_meta": {
                        "id": preset.id,
                        "name": preset.name,
                        "category": preset.category,
                        "description": preset.description,
                        "params": [param.as_dict() for param in preset.params],
                    },
                    "filters": [
                        {
                            "type": spec.type,
                            "name": FILTERS[spec.type].name if spec.type in FILTERS else spec.type,
                            "enabled": spec.enabled,
                            "params": spec.params,
                        }
                        for spec in clip.filters
                    ],
                    "keyframe_params": sorted({keyframe.param for keyframe in clip.keyframes}),
                }
            )
        return {
            "show_id": doc.id,
            "query_time_s": round(t, 4),
            "track": {**track.model_dump(mode="json"), "clips": []},
            "target_group": {
                "id": track.target_group,
                "fixture_ids": self._group_fixture_ids(track.target_group),
                "voxel_count": group_voxels,
            },
            "clips": clips,
            "active_clip_ids": [clip.id for clip in track.clips if clip.start_s <= t <= clip.end_s],
        }

    def _audio_detail(self, args: dict[str, Any]) -> dict[str, Any]:
        doc = self._show(args)
        show_id = doc.id
        active_show = show_id == self.runtime.show_id
        t = float(self.runtime.playhead) if active_show else 0.0
        t = max(0.0, min(doc.duration_s, t))
        blueprint = self.runtime.blueprints.get(show_id)
        audio_available = self.runtime.blueprints.audio_path(show_id) is not None
        if blueprint is None:
            return {
                "show_id": show_id,
                "attached": False,
                "audio_available": audio_available,
                "analyzer_error": self.runtime.blueprints.last_analyzer_error,
            }
        envelope_fn = BlueprintStore.envelope(blueprint)
        env = envelope_fn(doc, t)
        audio_state = self._audio_state(doc, show_id, t)
        beats = [float(value) for value in blueprint.get("beats", [])]
        index = max(0, bisect_right(beats, t) - 1) if beats else 0
        around = {
            "index": index,
            "previous": beats[max(0, index - 4) : index],
            "current": beats[index] if beats else None,
            "next": beats[index + 1 : index + 5],
        }
        curves = blueprint.get("curves") or {}
        curve_stats = {}
        for name in ("harmonic", "percussive", "prominence"):
            values = np.asarray(curves.get(name, []), dtype=np.float64)
            if values.size:
                curve_stats[name] = {
                    "count": int(values.size),
                    "mean": round(float(values.mean()), 4),
                    "min": round(float(values.min()), 4),
                    "max": round(float(values.max()), 4),
                }
        waveform = np.asarray(blueprint.get("waveform", []), dtype=np.float64)
        waveform_stats = (
            {
                "buckets": int(waveform.size),
                "mean": round(float(waveform.mean()), 4),
                "peak": round(float(waveform.max()), 4),
            }
            if waveform.size
            else {}
        )
        payload: dict[str, Any] = {
            "show_id": show_id,
            "attached": True,
            "audio_available": audio_available,
            "audio_path": str(self.runtime.blueprints.audio_path(show_id) or ""),
            "key": blueprint.get("key"),
            "source": blueprint.get("source"),
            "analyzer": blueprint.get("analyzer"),
            "duration_s": blueprint.get("duration_s"),
            "sample_rate": blueprint.get("sample_rate"),
            "bpm": blueprint.get("bpm"),
            "beat_grid_s": blueprint.get("beat_grid_s"),
            "sections": blueprint.get("sections", []),
            "section_at_playhead": audio_state["section_at_playhead"],
            "next_section": audio_state["next_section"],
            "drops": blueprint.get("drops", []),
            "drop_windows": blueprint.get("drop_windows", []),
            "pre_drops": blueprint.get("pre_drops", []),
            "beats_around_playhead": around,
            "envelope_at_playhead": {key: round(float(value), 4) for key, value in env.items()},
            "curve_stats": curve_stats,
            "waveform_stats": waveform_stats,
            "analyzer_error": self.runtime.blueprints.last_analyzer_error,
        }
        if args.get("include_beats"):
            payload["beats"] = blueprint.get("beats", [])
            payload["downbeats"] = blueprint.get("downbeats", [])
        if args.get("include_curves"):
            payload["curves"] = curves
        if args.get("include_waveform"):
            payload["waveform"] = blueprint.get("waveform", [])
        return payload

    def _resources(self) -> list[dict[str, Any]]:
        return [
            {
                "uri": "hueme://studio/context",
                "name": "Studio Context",
                "description": "Playhead, active show, tracks, audio and rig briefing.",
                "mimeType": "application/json",
            },
            {
                "uri": "hueme://show/current",
                "name": "Active ShowDoc",
                "description": "Complete active ShowDoc including tracks, clips, keyframes and markers.",
                "mimeType": "application/json",
            },
            {
                "uri": "hueme://audio/current",
                "name": "Audio Detail",
                "description": "Sections, drops, envelope and stem statistics for the active show.",
                "mimeType": "application/json",
            },
            {
                "uri": "hueme://rig/current",
                "name": "Active Rig",
                "description": "Working rig, fixtures, groups and transform state.",
                "mimeType": "application/json",
            },
            {
                "uri": "hueme://mcp/tools",
                "name": "MCP Tool Guide",
                "description": "All Hueme Studio MCP tools with schemas.",
                "mimeType": "application/json",
            },
        ]

    def _read_resource(self, uri: str) -> dict[str, Any]:
        if uri == "hueme://studio/context":
            payload: Any = self._studio_context({})
        elif uri == "hueme://show/current":
            payload = self._show({}).model_dump(mode="json")
        elif uri == "hueme://audio/current":
            payload = self._audio_detail({})
        elif uri == "hueme://rig/current":
            payload = {
                "active": self.runtime.rigs.active_id,
                "dirty": self.runtime.rig_dirty,
                "document": self.runtime.rig.model_dump(mode="json"),
            }
        elif uri == "hueme://mcp/tools":
            payload = {"tools": TOOLS}
        else:
            raise _RpcError(-32602, f"unknown resource '{uri}'")
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": _json_text(payload),
                }
            ]
        }

    def _show_id(self, args: dict[str, Any]) -> str:
        show_id = args.get("show_id") or self.runtime.show_id
        if not show_id:
            raise ValueError("no active show; pass show_id")
        return str(show_id)

    def _show(self, args: dict[str, Any]) -> Any:
        show_id = self._show_id(args)
        if self.runtime.show is not None and show_id == self.runtime.show_id:
            return self.runtime.show
        return self.runtime.shows.get(show_id)

    def _show_patch(self, args: dict[str, Any]) -> dict[str, Any]:
        patch = args.get("patch")
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        if "id" in patch:
            raise ValueError("show id cannot be changed by a patch")
        show_id = self._show_id(args)
        current = self.runtime.shows.get(show_id).model_dump(mode="json")
        merged = _merge(current, patch)
        return self._commit(show_id, merged, args.get("label") or "MCP: patch")

    def _show_commit(self, args: dict[str, Any]) -> dict[str, Any]:
        document = args.get("document")
        if not isinstance(document, dict):
            raise ValueError("document must be an object")
        show_id = self._show_id(args)
        return self._commit(show_id, document, args.get("label") or "MCP: commit")

    def _commit(
        self, show_id: str, document: dict[str, Any], label: str
    ) -> dict[str, Any]:
        if str(document.get("id")) != show_id:
            raise ValueError("document id does not match show_id")
        doc = ShowDoc.model_validate(document)
        doc = self.runtime.commit_show(show_id, doc, label)
        return {
            "id": doc.id,
            "name": doc.name,
            "tracks": len(doc.tracks),
            "clips": len(doc.all_clips()),
            "history": self.runtime.shows.history(show_id),
        }

    def _undo(self, args: dict[str, Any]) -> dict[str, Any]:
        show_id = self._show_id(args)
        doc = self.runtime.undo_show(show_id)
        return {
            "document": doc.model_dump(mode="json") if doc else None,
            "history": self.runtime.shows.history(show_id),
        }

    def _redo(self, args: dict[str, Any]) -> dict[str, Any]:
        show_id = self._show_id(args)
        doc = self.runtime.redo_show(show_id)
        return {
            "document": doc.model_dump(mode="json") if doc else None,
            "history": self.runtime.shows.history(show_id),
        }

    def _snapshot(self, args: dict[str, Any]) -> dict[str, Any]:
        show_id = self._show_id(args)
        self.runtime.shows.snapshot(show_id, args.get("label") or "MCP snapshot")
        return self.runtime.shows.history(show_id)

    def _lint(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.lint_show(self._show_id(args))

    def _blueprint(self, args: dict[str, Any]) -> dict[str, Any] | None:
        return self.runtime.blueprints.get(self._show_id(args))

    def _blueprint_extract(self, args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("path is required")
        show_id = self._show_id(args)
        # handle_async dispatches every tool call through asyncio.to_thread, so
        # this blocking analysis never runs on the event loop.
        blueprint = self.runtime.blueprints.extract_file(show_id, path)
        self.runtime.attach_blueprint(show_id)
        if bool(args.get("sync_show", True)):
            self.runtime.sync_show_from_blueprint(show_id, blueprint)
        return (
            BlueprintStore.summary(
                blueprint,
                audio_available=(
                    self.runtime.blueprints.audio_path(show_id) is not None
                ),
            )
            or {}
        )

    def _palette_save(self, args: dict[str, Any]) -> dict[str, Any]:
        palette = self.runtime.palettes.upsert(dict(args))
        self.runtime.invalidate_preview_cache()
        return palette

    def _palette_delete(self, args: dict[str, Any]) -> dict[str, Any]:
        palette_id = str(args.get("id") or "").strip()
        if not palette_id:
            raise ValueError("id is required")
        self.runtime.palettes.delete(palette_id)
        self.runtime.invalidate_preview_cache()
        return {"deleted": palette_id}

    def _transport(self, args: dict[str, Any]) -> dict[str, Any]:
        unknown = set(args) - TRANSPORT_KEYS
        if unknown:
            raise ValueError(f"unknown transport fields: {sorted(unknown)}")
        return self.runtime.apply_transport(dict(args))

    # -- granular authoring ----------------------------------------------
    def _create_show(self, args: dict[str, Any]) -> dict[str, Any]:
        show_id = str(args.get("id") or "").strip()
        name = str(args.get("name") or "").strip()
        if not show_id or not name:
            raise ValueError("id and name are required")
        duration = float(args.get("duration_s") or 60.0)
        if not math.isfinite(duration) or duration <= 0.0:
            raise ValueError("duration_s must be a positive finite number")
        bpm = float(args.get("bpm") or 120.0)
        if not math.isfinite(bpm) or not 1.0 <= bpm <= 400.0:
            raise ValueError("bpm must be between 1 and 400")
        match = args.get("match") or {}
        if not isinstance(match, dict):
            raise ValueError("match must be an object")
        payload = {
            "schema_version": 1,
            "id": show_id,
            "name": name,
            "duration_s": duration,
            "match": match,
            "timing": {"bpm": bpm},
            "tracks": [
                {
                    "id": "base",
                    "name": "Base Field",
                    "target_group": "all",
                    "clips": [
                        {
                            "id": "base_clip",
                            "start_s": 0.0,
                            "end_s": duration,
                            "preset": "solid",
                            "params": default_params("solid"),
                        }
                    ],
                }
            ],
        }
        doc = ShowDoc.model_validate(payload)
        created = self.runtime.create_show(doc)
        return {
            "document": created.model_dump(mode="json"),
            "history": self.runtime.shows.history(created.id),
        }

    @staticmethod
    def _op_payload(op: dict[str, Any]) -> dict[str, Any]:
        nested = op.get("data") or op.get("patch") or op.get("fields")
        out = dict(nested) if isinstance(nested, dict) else {}
        for key, value in op.items():
            if key not in {"op", "type", "action", "data", "patch", "fields"}:
                out[key] = value
        return out

    def _batch_apply(self, args: dict[str, Any]) -> dict[str, Any]:
        operations = args.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValueError("operations must be a non-empty array")
        show_id = self._show_id(args)
        label = str(args.get("label") or f"MCP: {len(operations)} edits")

        def mutate(doc: ShowDoc) -> ShowDoc:
            for index, raw in enumerate(operations):
                if not isinstance(raw, dict):
                    raise ValueError(f"operation {index} must be an object")
                op = str(raw.get("op") or raw.get("type") or raw.get("action") or "")
                payload = self._op_payload(raw)
                try:
                    self._apply_operation(doc, op, payload)
                except (KeyError, ValueError, TypeError) as exc:
                    raise ValueError(f"operation {index} ({op}): {exc}") from exc
            return doc

        updated = self.runtime.shows.apply(show_id, label, mutate)
        return {
            "id": updated.id,
            "tracks": len(updated.tracks),
            "clips": len(updated.all_clips()),
            "applied": len(operations),
            "history": self.runtime.shows.history(show_id),
        }

    def _apply_operation(
        self, doc: ShowDoc, op: str, payload: dict[str, Any]
    ) -> None:
        if op == "add_track":
            track = payload.get("track")
            if not isinstance(track, dict):
                track = {
                    key: value
                    for key, value in payload.items()
                    if key
                    in {
                        "id",
                        "name",
                        "target_group",
                        "blend_mode",
                        "opacity",
                        "muted",
                        "solo",
                        "kind",
                        "parent_id",
                        "clipped_to_below",
                        "height_px",
                    }
                }
            if not track.get("id") or not track.get("name"):
                raise ValueError("add_track needs id and name")
            if doc.find_track(str(track["id"])) is not None:
                raise ValueError(f"track '{track['id']}' already exists")
            doc.tracks.append(Track.model_validate(track))
            return
        if op in {"update_track", "delete_track"}:
            track_id = str(payload.get("track_id") or payload.get("id") or "")
            track = doc.find_track(track_id)
            if track is None:
                raise ValueError(f"track '{track_id}' not found")
            if op == "delete_track":
                doc.tracks = [item for item in doc.tracks if item.id != track_id]
                for child in doc.tracks:
                    if child.parent_id == track_id:
                        child.parent_id = None
                        if child.kind == "sub":
                            child.kind = "layer"
                return
            patch = payload.get("patch") if isinstance(payload.get("patch"), dict) else payload
            for key in (
                "name",
                "target_group",
                "blend_mode",
                "opacity",
                "muted",
                "solo",
                "kind",
                "parent_id",
                "clipped_to_below",
                "height_px",
            ):
                if key in patch:
                    setattr(track, key, patch[key])
            return
        if op in {"add_clip", "update_clip", "delete_clip"}:
            clip_id = str(payload.get("clip_id") or payload.get("id") or "")
            if op == "delete_clip":
                found = doc.find_clip(clip_id)
                if found is None:
                    raise ValueError(f"clip '{clip_id}' not found")
                found[0].clips = [
                    clip for clip in found[0].clips if clip.id != clip_id
                ]
                return
            if op == "add_clip":
                track_id = str(payload.get("track_id") or "")
                track = doc.find_track(track_id)
                if track is None:
                    raise ValueError(f"track '{track_id}' not found")
                clip = payload.get("clip")
                if not isinstance(clip, dict):
                    clip = {
                        key: value
                        for key, value in payload.items()
                        if key
                        not in {
                            "op",
                            "type",
                            "action",
                            "track_id",
                            "data",
                            "patch",
                            "fields",
                        }
                    }
                if not clip.get("id") or "start_s" not in clip or "end_s" not in clip:
                    raise ValueError("add_clip needs id, start_s and end_s")
                if doc.find_clip(str(clip["id"])) is not None:
                    raise ValueError(f"clip '{clip['id']}' already exists")
                track.clips.append(Clip.model_validate(clip))
                return
            found = doc.find_clip(clip_id)
            if found is None:
                raise ValueError(f"clip '{clip_id}' not found")
            clip = found[1]
            patch = payload.get("patch") if isinstance(payload.get("patch"), dict) else payload
            for key in (
                "name",
                "start_s",
                "end_s",
                "fade_in_s",
                "fade_out_s",
                "preset",
                "opacity",
                "snap_zcl",
            ):
                if key in patch:
                    setattr(clip, key, patch[key])
            if isinstance(patch.get("params"), dict):
                clip.params.update(patch["params"])
            if isinstance(patch.get("expressions"), dict):
                clip.expressions.update(
                    {str(k): str(v) for k, v in patch["expressions"].items()}
                )
            if isinstance(patch.get("spatial_mask"), dict):
                clip.spatial_mask = type(clip.spatial_mask).model_validate(
                    {**clip.spatial_mask.model_dump(), **patch["spatial_mask"]}
                )
            return
        if op in {"set_keyframe", "delete_keyframe"}:
            clip_id = str(payload.get("clip_id") or "")
            found = doc.find_clip(clip_id)
            if found is None:
                raise ValueError(f"clip '{clip_id}' not found")
            clip = found[1]
            param = str(payload.get("param") or "opacity")
            if op == "delete_keyframe":
                t_s = float(payload.get("t_s", 0.0))
                clip.keyframes = [
                    kf
                    for kf in clip.keyframes
                    if not (
                        kf.param == param and abs(kf.t_s - t_s) <= 1e-6
                    )
                ]
                return
            keyframe = payload.get("keyframe")
            if not isinstance(keyframe, dict):
                keyframe = {
                    "t_s": payload.get("t_s", 0.0),
                    "param": param,
                    "value": payload.get("value", 1.0),
                    "easing": payload.get("easing", "linear"),
                }
            clip.keyframes = [
                kf
                for kf in clip.keyframes
                if not (
                    kf.param == keyframe.get("param", param)
                    and abs(kf.t_s - float(keyframe.get("t_s", 0.0))) <= 1e-6
                )
            ]
            clip.keyframes.append(Keyframe.model_validate(keyframe))
            clip.keyframes.sort(key=lambda kf: kf.t_s)
            return
        if op in {"add_filter", "update_filter"}:
            clip_id = str(payload.get("clip_id") or "")
            found = doc.find_clip(clip_id)
            if found is None:
                raise ValueError(f"clip '{clip_id}' not found")
            clip = found[1]
            if op == "add_filter":
                filter_type = str(payload.get("filter") or payload.get("filter_type") or "")
                if not filter_type:
                    raise ValueError("add_filter needs filter")
                clip.filters.append(
                    FilterSpec(
                        type=filter_type,
                        enabled=bool(payload.get("enabled", True)),
                        params=dict(payload.get("params") or {}),
                    )
                )
                return
            index = payload.get("index")
            filter_type = payload.get("filter_type") or payload.get("filter")
            target = None
            if isinstance(index, int) and 0 <= index < len(clip.filters):
                target = clip.filters[index]
            elif filter_type:
                target = next(
                    (f for f in clip.filters if f.type == filter_type), None
                )
            if target is None:
                raise ValueError("update_filter target not found")
            if "enabled" in payload:
                target.enabled = bool(payload["enabled"])
            if isinstance(payload.get("params"), dict):
                target.params.update(payload["params"])
            return
        if op == "set_markers":
            markers = payload.get("markers")
            if not isinstance(markers, list):
                raise ValueError("set_markers needs markers array")
            doc.timing.markers = [Marker.model_validate(m) for m in markers]
            return
        if op == "set_expression":
            clip_id = str(payload.get("clip_id") or "")
            found = doc.find_clip(clip_id)
            if found is None:
                raise ValueError(f"clip '{clip_id}' not found")
            param = str(payload.get("param") or "")
            if not param:
                raise ValueError("set_expression needs param")
            expression = payload.get("expression")
            if expression in (None, ""):
                found[1].expressions.pop(param, None)
            else:
                found[1].expressions[param] = str(expression)
            return
        raise ValueError(f"unknown operation '{op}'")

    def _auto_keyframes(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.runtime.auto_keyframes(
            self._show_id(args),
            clip_id=args.get("clip_id"),
            source=str(args.get("source") or "kick"),
            param=str(args.get("param") or "opacity"),
            division=str(args.get("division") or "1/4"),
            depth=float(args.get("depth") or 0.65),
            threshold=float(args.get("threshold") or 0.18),
        )

    def _telemetry_strip(self, args: dict[str, Any]) -> dict[str, Any]:
        show_id = self._show_id(args)
        if self.runtime.show is not None and self.runtime.show_id == show_id:
            doc = self.runtime.show
        else:
            doc = self.runtime.shows.get(show_id)
        start = float(args.get("start_s") or 0.0)
        end = float(args.get("end_s") or doc.duration_s)
        samples = int(args.get("samples") or 16)
        samples = max(2, min(128, samples))
        start = max(0.0, min(doc.duration_s, start))
        end = max(start, min(doc.duration_s, end))
        if end - start <= 1e-6:
            end = min(doc.duration_s, start + 1e-3)
        times = np.linspace(start, end, samples, endpoint=False)
        runtime = self.runtime
        from hueme_studio.compositor import Compositor

        compositor = (
            runtime.compositor
            if runtime.show_id == show_id and runtime.compositor is not None
            else Compositor(runtime.compiled, doc, fps=runtime.fps)
        )
        group_ids: list[str] = []
        names: dict[str, str] = {}
        for group in runtime.rig.groups:
            group_ids.append(group.id)
            names[group.id] = group.name
        for group in runtime.rig.smart_groups:
            if group.id not in names:
                group_ids.append(group.id)
                names[group.id] = f"# {group.name}"
        for track in doc.tracks:
            if track.target_group not in names:
                group_ids.append(track.target_group)
                names[track.target_group] = track.target_group
        matrices: dict[str, list[list[float]]] = {
            gid: [] for gid in group_ids
        }
        brightness: dict[str, list[float]] = {gid: [] for gid in group_ids}
        for t in times:
            frame = compositor.evaluate(float(t))
            for gid in group_ids:
                try:
                    idx = runtime.compiled.group_indices(gid)
                except KeyError:
                    matrices[gid].append([0.0, 0.0, 0.0])
                    brightness[gid].append(0.0)
                    continue
                if idx.size == 0:
                    rgb = np.zeros(3)
                else:
                    rgb = np.mean(frame.rgb[idx].astype(np.float64), axis=0)
                matrices[gid].append([round(float(v), 1) for v in rgb])
                luminance = (
                    0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
                ) / 255.0
                brightness[gid].append(round(float(luminance), 3))
        return {
            "show_id": show_id,
            "duration_s": doc.duration_s,
            "bpm": doc.timing.bpm,
            "start_s": round(start, 4),
            "end_s": round(end, 4),
            "samples": samples,
            "times": [round(float(t), 4) for t in times],
            "groups": [
                {
                    "id": gid,
                    "name": names[gid],
                    "brightness": brightness[gid],
                    "rgb": matrices[gid],
                }
                for gid in group_ids
            ],
        }

    # -- telemetry --------------------------------------------------------
    def _record(
        self, method: str, start: float, tool: str | None, error: str | None
    ) -> None:
        elapsed = (time.perf_counter() - start) * 1000.0
        self.calls += 1
        if error:
            self.errors += 1
        self._latencies.append(elapsed)
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "method": method,
            "tool": tool,
            "ms": round(elapsed, 2),
            "ok": error is None,
            "error": error,
        }
        self._log.append(entry)
        note = getattr(self.runtime, "note_mcp", None)
        if callable(note):
            note(entry)

    @staticmethod
    def _percentile(values: list[float], pct: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
        return ordered[index]

    def telemetry(self) -> dict[str, Any]:
        latencies = list(self._latencies)
        return {
            "transport": "http",
            "uptime_s": round(time.monotonic() - self.started_at, 1),
            "tools": len(TOOLS),
            "calls": self.calls,
            "errors": self.errors,
            "p50_ms": round(self._percentile(latencies, 50), 2),
            "p95_ms": round(self._percentile(latencies, 95), 2),
            "log": list(self._log)[-60:],
        }


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


# ---------------------------------------------------------------------------
# stdio proxy
# ---------------------------------------------------------------------------
def _post(url: str, payload: Any, session_id: str | None) -> tuple[str, str | None]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8"), response.headers.get("Mcp-Session-Id")


def stdio_proxy(url: str) -> int:
    session_id: str | None = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            print(_json_text(_rpc_error(None, -32700, "parse error")), flush=True)
            continue
        try:
            text, new_session = _post(url, payload, session_id)
            session_id = new_session or session_id
            if text:
                print(text, flush=True)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            request_id = payload.get("id") if isinstance(payload, dict) else None
            print(
                _json_text(_rpc_error(request_id, -32603, f"HTTP {exc.code}: {detail}")),
                flush=True,
            )
        except OSError as exc:
            request_id = payload.get("id") if isinstance(payload, dict) else None
            print(
                _json_text(
                    _rpc_error(
                        request_id,
                        -32603,
                        f"studio server unreachable at {url}: {exc}",
                    )
                ),
                flush=True,
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hueme Studio MCP stdio proxy")
    parser.add_argument("--stdio", action="store_true", help="read JSON-RPC on stdin")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8090/mcp",
        help="running studio Streamable HTTP endpoint",
    )
    args = parser.parse_args(argv)
    if not args.stdio:
        parser.print_help()
        return 2
    return stdio_proxy(args.url)


if __name__ == "__main__":
    raise SystemExit(main())
