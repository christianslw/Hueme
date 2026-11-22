from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from hueme_studio.fxpreview import render_clip_rgb
from hueme_studio.server import create_app
from hueme_studio.showdoc import Clip, FilterSpec, SpatialMask, Timing
from hueme_studio.userpresets import UserPresetStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path):
    rigs = tmp_path / "rigs"
    shows = tmp_path / "shows"
    shutil.copytree(ROOT / "config" / "rigs", rigs)
    shutil.copytree(ROOT / "config" / "shows", shows)
    app = create_app(rigs_dir=rigs, shows_dir=shows, fps=30.0)
    with TestClient(app) as test_client:
        yield test_client


def test_user_preset_crud_and_snapshot(client):
    payload = {
        "name": "Amber Room",
        "preset": "color_wash",
        "params": {"brightness": 0.6, "saturation": 1.4},
        "spatial_mask": {"kind": "sphere_narrow", "center": [0.5, 0.5, 0.5], "radius": 0.4},
        "filters": [{"type": "bloom", "enabled": True, "params": {"gain": 0.5}}],
    }
    created = client.post("/api/user-presets", json=payload)
    assert created.status_code == 201
    saved = created.json()
    assert saved["id"] == "amber_room"
    assert saved["preset"] == "color_wash"
    assert saved["filters"][0]["uid"] == ""

    second = client.post("/api/user-presets", json={**payload, "name": "Amber Room"})
    assert second.json()["id"] == "amber_room_2"
    listed = client.get("/api/user-presets").json()["presets"]
    assert {item["id"] for item in listed} == {"amber_room", "amber_room_2"}
    assert client.get("/api/studio/state").json()["user_presets"]
    assert client.delete("/api/user-presets/amber_room").status_code == 204
    assert [item["id"] for item in client.get("/api/user-presets").json()["presets"]] == [
        "amber_room_2"
    ]


def test_user_preset_store_persists_and_rejects_extra_fields(tmp_path):
    path = tmp_path / "user-presets.json"
    store = UserPresetStore(path)
    store.upsert({"name": "Look", "preset": "solid", "params": {"brightness": 0.5}})
    assert path.is_file()
    reloaded = UserPresetStore(path)
    assert reloaded.list()[0]["name"] == "Look"
    with pytest.raises(ValueError):
        reloaded.upsert({"name": "Bad", "preset": "solid", "unexpected": 1})


def test_fx_preview_accepts_blueprint_envelope_provider():
    clip = Clip(
        id="preview",
        start_s=0.0,
        end_s=4.0,
        preset="solid",
        params={"color": [255, 255, 255], "brightness": 1.0},
        filters=[
            FilterSpec(
                type="audio_mod",
                params={"source": "kick", "depth": 1.0, "mode": "brightness"},
            )
        ],
        spatial_mask=SpatialMask(kind="none"),
    )

    def dark(_show, _t):
        return {"kick": 0.0}

    def bright(_show, _t):
        return {"kick": 1.0}

    low = render_clip_rgb(
        clip, 0.5, timing=Timing(bpm=120.0), width=32, height=18, env_provider=dark
    )
    high = render_clip_rgb(
        clip, 0.5, timing=Timing(bpm=120.0), width=32, height=18, env_provider=bright
    )
    assert float(low.mean()) < float(high.mean())
    assert np.isfinite(high).all()
