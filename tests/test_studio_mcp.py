from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hueme_studio import mcp
from hueme_studio.server import create_app

ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = ROOT / "config" / "rigs"
SHOWS_DIR = ROOT / "config" / "shows"


@pytest.fixture()
def client(tmp_path):
    rigs = tmp_path / "rigs"
    shows = tmp_path / "shows"
    shutil.copytree(RIGS_DIR, rigs)
    shutil.copytree(SHOWS_DIR, shows)
    app = create_app(
        rigs_dir=rigs,
        shows_dir=shows,
        blueprints_dir=tmp_path / "blueprints",
        fps=60.0,
    )
    with TestClient(app) as test_client:
        yield test_client


def _rpc(client, method: str, params: dict | None = None, request_id: int = 1):
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        },
    )
    return response


def _call(client, name: str, arguments: dict | None = None):
    payload = _rpc(
        client,
        "tools/call",
        {"name": name, "arguments": arguments or {}},
    ).json()
    return payload["result"] if "result" in payload else payload


def test_initialize_tools_list_and_session_header(client):
    response = _rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
    )
    assert response.status_code == 200
    assert response.headers["Mcp-Session-Id"]
    result = response.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"]["tools"]["listChanged"] is False

    listed = _rpc(client, "tools/list").json()["result"]["tools"]
    names = {tool["name"] for tool in listed}
    assert {"show_patch", "show_commit", "show_lint", "blueprint_extract"} <= names
    for tool in listed:
        assert tool["inputSchema"]["type"] == "object"


def test_notification_returns_202_without_body(client):
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert response.status_code == 202
    assert response.content == b""


def test_show_patch_is_one_undoable_transaction(client):
    before = client.get("/api/shows/dance_loft_v1/history").json()
    original_name = client.get("/api/studio/state").json()["show"]["document"]["name"]
    result = _call(
        client,
        "show_patch",
        {"patch": {"name": "MCP Edit", "notes": "touched by mcp"}, "label": "MCP: 2 edits"},
    )
    assert result["isError"] is False
    assert result["structuredContent"]["history"]["undo_depth"] == before["undo_depth"] + 1
    assert client.get("/api/studio/state").json()["show"]["document"]["name"] == "MCP Edit"

    undone = _call(client, "show_undo")
    assert undone["isError"] is False
    assert undone["structuredContent"]["document"]["name"] == original_name


def test_mcp_context_track_audio_and_resources(client):
    state = _call(client, "studio_context")
    assert state["isError"] is False
    context = state["structuredContent"]
    assert context["show"]["id"] == "dance_loft_v1"
    assert context["show"]["tracks"]
    assert any(track["active_clip_ids"] for track in context["show"]["tracks"])
    assert "beat" in context["show"]
    assert context["rig"]["fixture_count"] > 0
    assert context["rig"]["groups"]

    first_track_id = context["show"]["tracks"][0]["id"]
    track = _call(client, "track_detail", {"track_id": first_track_id})
    assert track["isError"] is False
    detail = track["structuredContent"]
    assert detail["track"]["id"] == first_track_id
    assert detail["clips"]
    assert detail["target_group"]["fixture_ids"]
    assert detail["clips"][0]["preset_meta"]["name"]

    audio = _call(client, "audio_detail", {"include_beats": True})
    assert audio["isError"] is False
    assert audio["structuredContent"]["attached"] is False

    resources = _rpc(client, "resources/list").json()["result"]["resources"]
    uris = {item["uri"] for item in resources}
    assert "hueme://studio/context" in uris
    assert "hueme://audio/current" in uris
    read = _rpc(
        client, "resources/read", {"uri": "hueme://studio/context"}
    ).json()["result"]["contents"][0]
    assert read["mimeType"] == "application/json"
    assert json.loads(read["text"])["show"]["id"] == "dance_loft_v1"


def test_mcp_audio_detail_after_blueprint_upload(client):
    from test_studio_blueprint import _click_wav

    payload = _click_wav(seconds=6.0)
    uploaded = client.post(
        "/api/shows/dance_loft_v1/blueprint?source=click.wav&sync_show=true",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200
    result = _call(client, "audio_detail", {"include_beats": True, "include_curves": True})
    assert result["isError"] is False
    detail = result["structuredContent"]
    assert detail["attached"] is True
    assert detail["audio_available"] is True
    assert detail["sections"]
    assert detail["beats"]
    assert detail["curves"]
    assert detail["envelope_at_playhead"]["kick"] >= 0.0
    assert detail["curve_stats"]["percussive"]["max"] > 0.0


def test_invalid_patch_is_a_tool_error_without_mutation(client):
    original = client.get("/api/studio/state").json()["show"]["document"]["duration_s"]
    result = _call(client, "show_patch", {"patch": {"duration_s": -1}})
    assert result["isError"] is True
    assert "duration_s" in result["content"][0]["text"]
    document = client.get("/api/studio/state").json()["show"]["document"]
    assert document["duration_s"] == pytest.approx(original)
    assert client.get("/api/shows/dance_loft_v1/history").json()["undo_depth"] == 0


def test_lint_tool_and_unknown_tool(client):
    lint = _call(client, "show_lint")
    assert lint["isError"] is False
    assert "ok" in lint["structuredContent"]

    unknown = _call(client, "not_a_tool")
    assert unknown["error"]["code"] == -32602
    assert "unknown tool" in unknown["error"]["message"]


def test_telemetry_records_calls(client):
    _call(client, "show_lint")
    _call(client, "show_lint")
    telemetry = client.get("/api/mcp/telemetry").json()
    assert telemetry["calls"] >= 2
    assert telemetry["tools"] >= 1
    assert telemetry["log"][-1]["tool"] == "show_lint"


def test_unknown_method_is_rpc_error(client):
    response = _rpc(client, "nope/nope")
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601


def test_stdio_proxy_frames_newline_delimited_json(monkeypatch):
    seen: list[dict] = []

    def fake_post(url, payload, session_id):
        seen.append(payload)
        return json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": {}}), "sid"

    monkeypatch.setattr(mcp, "_post", fake_post)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"jsonrpc":"2.0","id":7,"method":"ping"}\n'),
    )
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert mcp.stdio_proxy("http://127.0.0.1:8090/mcp") == 0
    assert seen[0]["method"] == "ping"
    assert json.loads(out.getvalue())["id"] == 7


def test_granular_tools_create_batch_and_telemetry_strip(client):
    created = _call(
        client,
        "create_show",
        {
            "id": "mcp_new",
            "name": "MCP New",
            "duration_s": 16.0,
            "bpm": 120.0,
            "match": {"artist": "A", "title": "T"},
        },
    )
    assert created["isError"] is False
    assert created["structuredContent"]["document"]["id"] == "mcp_new"

    batched = _call(
        client,
        "batch_apply_edits",
        {
            "show_id": "mcp_new",
            "label": "MCP: atomic batch",
            "operations": [
                {
                    "op": "update_track",
                    "track_id": "base",
                    "patch": {"name": "Bed", "blend_mode": "add"},
                },
                {
                    "op": "add_track",
                    "track": {
                        "id": "accents",
                        "name": "Accents",
                        "target_group": "galerie_oben",
                        "blend_mode": "add",
                    },
                },
                {
                    "op": "add_clip",
                    "track_id": "accents",
                    "clip": {
                        "id": "acc_1",
                        "start_s": 0.0,
                        "end_s": 8.0,
                        "preset": "solid",
                        "params": {"color": [255, 0, 0]},
                    },
                },
                {
                    "op": "set_keyframe",
                    "clip_id": "acc_1",
                    "param": "opacity",
                    "t_s": 2.0,
                    "value": 0.5,
                },
                {
                    "op": "add_filter",
                    "clip_id": "acc_1",
                    "filter": "beat_gate",
                    "params": {"duty": 0.4},
                },
                {
                    "op": "set_expression",
                    "clip_id": "acc_1",
                    "param": "brightness",
                    "expression": "clamp(0.5 + 0.5 * sin(t * TAU), 0, 1)",
                },
                {
                    "op": "set_markers",
                    "markers": [{"t_s": 4.0, "name": "Drop", "kind": "drop"}],
                },
            ],
        },
    )
    assert batched["isError"] is False, batched
    summary = batched["structuredContent"]
    assert summary["applied"] == 7
    assert summary["history"]["undo_depth"] == 1
    document = client.get("/api/shows/mcp_new").json()
    assert document["tracks"][1]["clips"][0]["expressions"]["brightness"].startswith(
        "clamp"
    )
    assert document["timing"]["markers"][0]["name"] == "Drop"

    telemetry = _call(
        client,
        "render_telemetry_strip",
        {"show_id": "mcp_new", "start_s": 0.0, "end_s": 8.0, "samples": 4},
    )
    assert telemetry["isError"] is False, telemetry
    strip = telemetry["structuredContent"]
    assert strip["samples"] == 4
    assert len(strip["times"]) == 4
    group = next(item for item in strip["groups"] if item["id"] == "galerie_oben")
    assert len(group["brightness"]) == 4
    assert any(value > 0.0 for value in group["brightness"])

    undone = _call(client, "show_undo", {"show_id": "mcp_new"})
    assert undone["structuredContent"]["document"]["tracks"][0]["name"] == "Base Field"


def test_mcp_note_is_a_direct_event_not_a_state_invalidation(client):
    runtime = client.app.state.studio
    runtime._events.clear()
    runtime.note_mcp({"time": "12:00:00", "tool": "show_lint", "ms": 1.0, "ok": True})
    event = runtime._events[-1]
    assert event["type"] == "mcp"
    assert event["tool"] == "show_lint"
    assert not any(
        item.get("type") == "invalidate" and item.get("scope") == "mcp"
        for item in runtime._events
    )


def test_palette_mcp_tools(client):
    listed = _call(client, "palette_list")
    assert listed["isError"] is False
    assert any(item["builtin"] for item in listed["structuredContent"]["palettes"])

    saved = _call(
        client,
        "palette_save",
        {"name": "MCP Palette", "colors": [[255, 0, 0], [0, 0, 255]]},
    )
    assert saved["isError"] is False
    palette = saved["structuredContent"]
    assert palette["id"] == "mcp_palette"
    assert palette["builtin"] is False

    protected = _call(
        client,
        "palette_save",
        {"id": "synth", "name": "Nope", "colors": [[0, 0, 0], [1, 1, 1]]},
    )
    assert protected["isError"] is True

    deleted = _call(client, "palette_delete", {"id": "mcp_palette"})
    assert deleted["isError"] is False
    assert deleted["structuredContent"]["deleted"] == "mcp_palette"
