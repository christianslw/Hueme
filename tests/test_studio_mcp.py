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
    assert undone["structuredContent"]["document"]["name"] == "Dance Loft V1"


def test_invalid_patch_is_a_tool_error_without_mutation(client):
    result = _call(client, "show_patch", {"patch": {"duration_s": -1}})
    assert result["isError"] is True
    assert "duration_s" in result["content"][0]["text"]
    document = client.get("/api/studio/state").json()["show"]["document"]
    assert document["duration_s"] == pytest.approx(236.25)
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
