from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hueme_studio.server import FRAME_HEADER, FRAME_MAGIC, create_app

ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = ROOT / "config" / "rigs"
SHOWS_DIR = ROOT / "config" / "shows"


@pytest.fixture()
def client(tmp_path):
    rigs = tmp_path / "rigs"
    shows = tmp_path / "shows"
    shutil.copytree(RIGS_DIR, rigs)
    shutil.copytree(SHOWS_DIR, shows)
    app = create_app(rigs_dir=rigs, shows_dir=shows, fps=60.0)
    with TestClient(app) as test_client:
        yield test_client


def _frame_header(payload: bytes) -> tuple[int, float, int]:
    magic, count, time_s, flags = FRAME_HEADER.unpack(payload[: FRAME_HEADER.size])
    assert magic == FRAME_MAGIC
    return count, time_s, flags


def test_ui_assets_are_revalidated(client):
    for path in ("/studio", "/static/studio.js", "/static/ui.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert "no-cache" in response.headers["cache-control"]


def test_state_snapshot_carries_rig_show_and_catalogs(client):
    response = client.get("/api/studio/state")
    assert response.status_code == 200
    state = response.json()
    assert state["rig"]["document"]["id"] == "hueme_loft"
    assert state["rig"]["compiled"]["voxel_count"] == 190
    assert state["rig"]["compiled"]["fixture_slices"]["stahltraeger"] == [10, 190]
    assert state["show"]["document"]["id"] == "dance_loft_v1"
    assert {p["id"] for p in state["presets"]} >= {"solid", "ma3_wings"}
    assert {f["id"] for f in state["filters"]} >= {"inertia", "beat_gate"}
    assert state["playback"]["voxel_count"] == 190


def test_rig_library_crud_and_activation(client):
    created = client.post(
        "/api/rigs",
        json={"name": "Gallery Test", "preset_id": "gallery_test"},
    )
    assert created.status_code == 201
    assert created.json()["id"] == "gallery_test"
    state = client.get("/api/studio/state").json()
    assert state["rig"]["document"]["id"] == "gallery_test"
    assert state["rig"]["document"]["fixtures"] == []

    cloned = client.post(
        "/api/rigs/gallery_test/clone",
        json={"new_id": "gallery_clone", "name": "Gallery Clone"},
    )
    assert cloned.status_code == 201
    assert cloned.json()["id"] == "gallery_clone"

    activated = client.post("/api/rigs/hueme_loft/activate")
    assert activated.status_code == 200
    assert client.get("/api/studio/state").json()["rig"]["document"]["id"] == (
        "hueme_loft"
    )

    deleted = client.delete("/api/rigs/gallery_clone")
    assert deleted.status_code == 204
    assert "gallery_clone" not in {
        entry["id"] for entry in client.get("/api/rigs").json()["presets"]
    }


def test_fixture_patch_compiles_saves_and_reloads(client, tmp_path):
    response = client.patch(
        "/api/rig/fixtures/galerie_01",
        json={"geometry": {"pos": [1.0, 2.0, 3.0]}},
    )
    assert response.status_code == 200
    state = client.get("/api/studio/state").json()
    assert state["rig"]["dirty"] is True
    positions = state["rig"]["compiled"]["positions"]
    assert positions[:3] == pytest.approx([1.0, 2.0, 3.0])

    saved = client.post("/api/rig/save")
    assert saved.status_code == 200
    assert client.get("/api/studio/state").json()["rig"]["dirty"] is False

    payload = json.loads((tmp_path / "rigs" / "hueme_loft.json").read_text())
    assert payload["fixtures"][0]["geometry"]["pos"] == pytest.approx([1.0, 2.0, 3.0])


def test_add_fixture_group_assignment_and_delete(client):
    fixture = {
        "id": "test_spot",
        "name": "Test Spot",
        "geometry": {"type": "point", "pos": [3.0, 3.0, 4.0]},
    }
    added = client.post("/api/rig/fixtures", json=fixture)
    assert added.status_code == 201
    assert added.json()["fixtures"][-1]["id"] == "test_spot"
    assert client.get("/api/studio/state").json()["rig"]["compiled"][
        "voxel_count"
    ] == 191

    assigned = client.post(
        "/api/rig/groups/galerie_oben/assign",
        json={"fixture_ids": ["test_spot"]},
    )
    assert assigned.status_code == 200
    group = next(
        g for g in assigned.json()["groups"] if g["id"] == "galerie_oben"
    )
    assert "test_spot" in group["fixtures"]

    removed = client.delete("/api/rig/fixtures/test_spot")
    assert removed.status_code == 200
    group = next(
        g for g in removed.json()["groups"] if g["id"] == "galerie_oben"
    )
    assert "test_spot" not in group["fixtures"]


def test_group_upsert_and_delete(client):
    group = {
        "id": "test_list",
        "name": "Test List",
        "type": "list",
        "fixtures": ["galerie_01"],
    }
    created = client.put("/api/rig/groups/test_list", json=group)
    assert created.status_code == 200
    assert "test_list" in {g["id"] for g in created.json()["groups"]}

    deleted = client.delete("/api/rig/groups/test_list")
    assert deleted.status_code == 200
    assert "test_list" not in {g["id"] for g in deleted.json()["groups"]}


def test_show_commit_undo_redo_and_history(client):
    original = client.get("/api/shows/dance_loft_v1").json()
    changed = json.loads(json.dumps(original))
    changed["name"] = "Renamed Show"
    committed = client.put(
        "/api/shows/dance_loft_v1?label=rename", json=changed
    )
    assert committed.status_code == 200
    assert committed.json()["name"] == "Renamed Show"

    undone = client.post("/api/shows/dance_loft_v1/undo")
    assert undone.status_code == 200
    assert undone.json()["document"]["name"] == original["name"]

    redone = client.post("/api/shows/dance_loft_v1/redo")
    assert redone.status_code == 200
    assert redone.json()["document"]["name"] == "Renamed Show"

    history = client.get("/api/shows/dance_loft_v1/history")
    assert history.status_code == 200
    assert history.json()["undo_depth"] >= 1


def test_transport_seek_blackout_and_frame_encoding(client):
    response = client.post(
        "/api/transport", json={"time_s": 10.0, "blackout": True, "gain": 0.5}
    )
    assert response.status_code == 200
    transport = response.json()
    assert transport["time_s"] == pytest.approx(10.0)
    assert transport["blackout"] is True
    assert transport["gain"] == pytest.approx(0.5)

    runtime = client.app.state.studio
    count, time_s, flags = _frame_header(runtime.evaluate())
    assert count == 190
    assert time_s == pytest.approx(10.0)
    assert flags & 2  # blackout bit

    client.post("/api/transport", json={"blackout": False, "gain": 1.0})
    assert client.get("/api/health").json()["voxel_count"] == 190


def test_websocket_hello_and_render_frame(client):
    with client.websocket_connect("/ws/preview") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello"
        assert hello["state"]["rig"]["document"]["id"] == "hueme_loft"

        count, _time_s, _flags = _frame_header(ws.receive_bytes())
        assert count == 190

        ws.send_text(json.dumps({"type": "render", "time_s": 5.0}))
        count, time_s, _flags = _frame_header(ws.receive_bytes())
        assert count == 190
        assert time_s == pytest.approx(5.0, abs=0.01)


def test_websocket_pushes_rig_invalidation(client):
    with client.websocket_connect("/ws/preview") as ws:
        ws.receive_text()
        ws.receive_bytes()
        response = client.patch(
            "/api/rig/fixtures/galerie_02",
            json={"geometry": {"pos": [2.0, 2.0, 2.0]}},
        )
        assert response.status_code == 200
        event = json.loads(ws.receive_text())
        assert event["type"] == "invalidate"
        assert event["scope"] == "rig"


def test_activation_guard_requires_force_when_dirty(client):
    client.patch(
        "/api/rig/fixtures/galerie_01",
        json={"geometry": {"pos": [1.0, 1.0, 1.0]}},
    )
    guarded = client.post("/api/rigs/empty_loft/activate")
    assert guarded.status_code == 400
    forced = client.post("/api/rigs/empty_loft/activate?force=true")
    assert forced.status_code == 200
    assert client.get("/api/studio/state").json()["rig"]["dirty"] is False


def test_invalid_fixture_patch_is_rejected(client):
    response = client.patch(
        "/api/rig/fixtures/galerie_01",
        json={"geometry": {"pos": [1.0]}},
    )
    assert response.status_code == 400
    assert client.get("/api/studio/state").json()["rig"]["dirty"] is False


def test_empty_rig_state_and_preview(client):
    client.post("/api/rigs/empty_loft/activate?force=true")
    state = client.get("/api/studio/state").json()
    assert state["rig"]["compiled"]["voxel_count"] == 0
    with client.websocket_connect("/ws/preview") as ws:
        ws.receive_text()
        count, _time_s, _flags = _frame_header(ws.receive_bytes())
        assert count == 0


def test_save_as_creates_new_preset_and_stays_active(client):
    response = client.post(
        "/api/rig/save-as", json={"id": "loft_copy", "name": "Loft Copy"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == "loft_copy"
    state = client.get("/api/studio/state").json()
    assert state["rig"]["active"] == "loft_copy"
    assert state["rig"]["document"]["id"] == "loft_copy"
    assert state["rig"]["document"]["name"] == "Loft Copy"


def test_save_as_requires_force_to_overwrite_another_preset(client):
    guarded = client.post(
        "/api/rig/save-as", json={"id": "empty_loft", "name": "Overwrite"}
    )
    assert guarded.status_code == 400
    forced = client.post(
        "/api/rig/save-as?force=true",
        json={"id": "empty_loft", "name": "Overwrite"},
    )
    assert forced.status_code == 200
    assert forced.json()["name"] == "Overwrite"


def test_static_studio_and_live_pages(client):
    root = client.get("/", follow_redirects=False)
    assert root.status_code in {302, 307}
    assert root.headers["location"] == "/studio"

    studio = client.get("/studio")
    assert studio.status_code == 200
    assert "HUEME" in studio.text
    assert 'id="viewport"' in studio.text
    assert 'id="timeline"' in studio.text

    live = client.get("/live")
    assert live.status_code == 200
    assert 'id="blackout"' in live.text

    css = client.get("/static/ui.css")
    assert css.status_code == 200
    assert "--bg-0: #111214" in css.text

    script = client.get("/static/studio.js")
    assert script.status_code == 200
    assert "StudioClient" in script.text
