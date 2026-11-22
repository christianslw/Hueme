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


def _voxel_count(document: dict) -> int:
    """Rig data is user-editable; derive counts instead of hardcoding a rig."""
    return sum(
        int(f["geometry"]["pixels"])
        if f["geometry"]["type"] == "line"
        else 1
        for f in document["fixtures"]
    )


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
    expected = _voxel_count(state["rig"]["document"])
    assert state["rig"]["compiled"]["voxel_count"] == expected
    strip = next(
        f for f in state["rig"]["document"]["fixtures"] if f["id"] == "stahltraeger"
    )
    start, stop = state["rig"]["compiled"]["fixture_slices"]["stahltraeger"]
    assert stop - start == strip["geometry"]["pixels"]
    assert len(state["rig"]["compiled"]["voxel_fixture_index"]) == expected
    assert state["rig"]["compiled"]["groups"]["galerie_oben"] == list(range(7))
    assert set(state["rig"]["compiled"]["groups"]) >= {"smart_hue_all", "boden_01"}
    assert state["show"]["document"]["id"] == "dance_loft_v1"
    assert {p["id"] for p in state["presets"]} >= {"solid", "ma3_wings"}
    assert {f["id"] for f in state["filters"]} >= {"inertia", "beat_gate"}
    assert state["playback"]["voxel_count"] == expected


def test_preset_and_filter_previews_are_real_frames(client):
    presets = client.get("/api/presets/previews")
    filters = client.get("/api/filters/previews")
    assert presets.status_code == 200
    assert filters.status_code == 200
    preset_frames = presets.json()["frames"]
    filter_frames = filters.json()["frames"]
    assert {"solid", "ma3_wings", "sidechain_pump"} <= set(preset_frames)
    assert {"beat_gate", "hue_rotate", "scanline"} <= set(filter_frames)
    assert all(len(frames) >= 2 for frames in preset_frames.values())
    for frames in preset_frames.values():
        assert any(frame for frame in frames), "preset preview needs at least one lit frame"
        for frame in frames:
            assert all(len(point) == 3 for point in frame)
            assert all(0.0 <= point[0] <= 1.0 and 0.0 <= point[1] <= 1.0 for point in frame)
            assert all(point[2].startswith("#") and len(point[2]) == 7 for point in frame)


def test_hardware_toggle_loads_gateway_mqtt_config(client, monkeypatch):
    from hueme_studio import server as server_module

    captured = {}

    class FakeHal:
        def __init__(self, rig, settings):
            captured["settings"] = settings
            self.enabled = False

        def set_enabled(self, enabled):
            self.enabled = bool(enabled)

        def set_rig(self, rig):
            pass

        def snapshot_stats(self):
            return {"enabled": self.enabled, "started": self.enabled, "tx_operations": 0, "last_error": None}

    monkeypatch.setattr(server_module, "Hal", FakeHal)
    runtime = client.app.state.studio
    runtime.hal = None
    runtime.hal_settings = None
    response = client.post("/api/hardware", json={"enabled": True})
    assert response.status_code == 200
    settings = captured["settings"]
    assert settings.mqtt.get("host"), "gateway MQTT host must be loaded for the HW toggle"
    assert settings.hue.get("proxies"), "gateway Hue proxies must be loaded for the HW toggle"
    assert response.json()["enabled"] is True


def test_fixture_selection_transform_bakes_geometry(client):
    before = client.get("/api/studio/state").json()["rig"]["document"]
    fixture = next(item for item in before["fixtures"] if item["id"] == "galerie_01")
    start = list(fixture["geometry"]["pos"])
    response = client.post(
        "/api/rig/fixtures/transform",
        json={
            "fixture_ids": ["galerie_01", "galerie_02"],
            "translate": [0.5, -0.25, 0.0],
            "rotate_axis": "z",
            "rotate_deg": 0.0,
            "scale": [1.0, 1.0, 1.0],
        },
    )
    assert response.status_code == 200
    state = client.get("/api/studio/state").json()
    after = next(item for item in state["rig"]["document"]["fixtures"] if item["id"] == "galerie_01")
    assert after["geometry"]["pos"][0] == pytest.approx(start[0] + 0.5)
    assert after["geometry"]["pos"][1] == pytest.approx(start[1] - 0.25)
    assert after["transform"]["translate"] == [0.0, 0.0, 0.0]
    assert state["rig"]["dirty"] is True

    rotated = client.post(
        "/api/rig/fixtures/transform",
        json={
            "fixture_ids": ["galerie_01"],
            "rotate_axis": "z",
            "rotate_deg": 90.0,
            "scale": [1.0, 1.0, 1.0],
            "pivot": [0.0, 0.0, 0.0],
        },
    )
    assert rotated.status_code == 200
    invalid = client.post(
        "/api/rig/fixtures/transform",
        json={"fixture_ids": ["galerie_01"], "rotate_axis": "w"},
    )
    assert invalid.status_code == 400


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
    before = _voxel_count(
        client.get("/api/studio/state").json()["rig"]["document"]
    )
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
    ] == before + 1

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
    expected = runtime.compiled.voxel_count
    count, time_s, flags = _frame_header(runtime.evaluate())
    assert count == expected
    assert time_s == pytest.approx(10.0)
    assert flags & 2  # blackout bit

    client.post("/api/transport", json={"blackout": False, "gain": 1.0})
    assert client.get("/api/health").json()["voxel_count"] == expected


def test_websocket_hello_and_render_frame(client):
    with client.websocket_connect("/ws/preview") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello"
        assert hello["state"]["rig"]["document"]["id"] == "hueme_loft"
        expected = hello["state"]["playback"]["voxel_count"]

        count, _time_s, _flags = _frame_header(ws.receive_bytes())
        assert count == expected

        ws.send_text(json.dumps({"type": "render", "time_s": 5.0}))
        count, time_s, _flags = _frame_header(ws.receive_bytes())
        assert count == expected
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
    for marker in (
        "PROXIES",
        "HW OUT",
        "MA LOCK",
        "Compose",
        "Stage",
        "Datei",
        "Bearbeiten",
        "Ebenen",
        "Filter &amp; Effekte",
        "Ansicht",
        "Hilfe",
        "+ Global",
        "+ Unterebene",
        "AI KEYS",
        "timecode",
    ):
        assert marker in studio.text

    live = client.get("/live")
    assert live.status_code == 200
    assert 'id="blackout"' in live.text

    css = client.get("/static/ui.css")
    assert css.status_code == 200
    assert "--bg-0: #111214" in css.text

    script = client.get("/static/studio.js")
    assert script.status_code == 200
    for marker in (
        "StudioClient",
        "rangeControl",
        "stepperWrap",
        "expressionEditor",
        "keyframeAtPoint",
        "AudioContext",
        "clipped_to_below",
        "smart_groups",
        "setIcon",
    ):
        assert marker in script.text


def test_hardware_toggle_and_health_metadata(client, monkeypatch):
    from hueme_studio.hal import Hal

    def fake_start(self):
        self._started = True

    monkeypatch.setattr(Hal, "start", fake_start)
    state = client.get("/api/studio/state").json()
    assert state["hardware"] == {
        "enabled": False,
        "started": False,
        "tx_operations": 0,
        "last_error": None,
    }
    assert state["proxy_health"]["ok"] is True
    assert state["proxy_health"]["required_proxies"] == 2
    for fixture in state["rig"]["document"]["fixtures"]:
        assert "incomplete_reasons" in fixture

    enabled = client.post("/api/hardware", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert client.get("/api/health").json()["hardware"]["enabled"] is True

    disabled = client.post("/api/hardware", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
