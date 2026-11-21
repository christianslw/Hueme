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
import json
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Callable

from hueme_studio import __version__
from hueme_studio.blueprint import BlueprintStore
from hueme_studio.showdoc import ShowDoc

MCP_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}
SERVER_NAME = "hueme-studio"

# Tool schemas are deliberately flat: the studio is the source of truth for
# ShowDoc validation, so the schema only names arguments and types.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "studio_state",
        "description": "Full studio snapshot: rig, show catalog, playback and catalogs.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
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
            },
            "required": ["path"],
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
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
                "instructions": (
                    "Hueme Studio co-director. Read with studio_state/show_get, "
                    "edit with show_patch/show_commit (one undoable transaction "
                    "per call), check show_lint before committing big changes."
                ),
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method in {"resources/list", "prompts/list"}:
            return {method.split("/")[0]: []}
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
            "studio_state": lambda _a: self.runtime.snapshot(),
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
            "blueprint_get": lambda a: self._blueprint(a) or {},
            "blueprint_extract": self._blueprint_extract,
            "transport_set": self._transport,
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
        # ponytail: MCP extraction runs inline and can block the event loop on a
        # long track. The HTTP upload path already offloads; move tool calls to
        # a worker if MCP audio ingest becomes common.
        blueprint = self.runtime.blueprints.extract_file(show_id, path)
        self.runtime.attach_blueprint(show_id)
        return (
            BlueprintStore.summary(
                blueprint,
                audio_available=(
                    self.runtime.blueprints.audio_path(show_id) is not None
                ),
            )
            or {}
        )

    def _transport(self, args: dict[str, Any]) -> dict[str, Any]:
        unknown = set(args) - TRANSPORT_KEYS
        if unknown:
            raise ValueError(f"unknown transport fields: {sorted(unknown)}")
        return self.runtime.apply_transport(dict(args))

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
