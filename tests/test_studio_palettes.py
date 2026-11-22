from __future__ import annotations

import shutil
import struct
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from hueme_studio.palettes import PaletteStore
from hueme_studio.presets import PresetUtils
from hueme_studio.server import MAX_PREVIEW_CACHE, create_app
from hueme_studio.showdoc import Clip, ShowDoc, ShowStore, Track

ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = ROOT / "config" / "rigs"
SHOWS_DIR = ROOT / "config" / "shows"


def _png_size(payload: bytes) -> tuple[int, int]:
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", payload[16:24])


def test_palette_store_crud_and_custom_gradient(tmp_path: Path):
    path = tmp_path / "palettes.json"
    store = PaletteStore(path)
    assert any(palette["builtin"] for palette in store.list())

    saved = store.upsert(
        {"name": "Test Fire", "colors": [[255, 0, 0], [0, 0, 255]]}
    )
    assert saved["id"] == "test_fire"
    assert saved["builtin"] is False
    assert store.list()[-1]["name"] == "Test Fire"

    gradient = PresetUtils.gradient("test_fire", np.linspace(0.0, 1.0, 7))
    assert gradient.shape == (7, 3)
    assert gradient[0, 0] > gradient[-2, 0]
    assert gradient[0, 2] < gradient[-2, 2]

    # Reloading must restore the same custom palette.
    reloaded = PaletteStore(path)
    assert reloaded.list()[-1]["colors"] == [[255, 0, 0], [0, 0, 255]]

    with pytest.raises(ValueError):
        store.upsert({"id": "synth", "name": "Nope", "colors": [[0, 0, 0], [1, 1, 1]]})
    with pytest.raises(ValueError):
        store.upsert({"name": "Too Few", "colors": [[0, 0, 0]]})

    store.delete("test_fire")
    assert all(palette["id"] != "test_fire" for palette in store.list())

    path.write_text("{broken", encoding="utf-8")
    broken = PaletteStore(path)
    assert all(palette["builtin"] for palette in broken.list())


def test_auto_track_name_follows_preset_then_freezes(tmp_path: Path):
    store = ShowStore(tmp_path)
    store.create(
        ShowDoc(
            id="auto_name",
            name="Auto Name",
            duration_s=10.0,
            tracks=[
                Track(
                    id="layer",
                    name="Old Name",
                    name_auto=True,
                    clips=[Clip(id="clip", start_s=0.0, end_s=5.0, preset="solid")],
                )
            ],
        )
    )
    assert store.get("auto_name").tracks[0].name == "Solid Field"

    store.apply(
        "auto_name",
        "change preset",
        lambda doc: setattr(doc.tracks[0].clips[0], "preset", "shockwave_3d") or doc,
    )
    assert store.get("auto_name").tracks[0].name == "Shockwave 3D"

    store.apply(
        "auto_name",
        "manual clip name",
        lambda doc: setattr(doc.tracks[0].clips[0], "name", "My Look") or doc,
    )
    assert store.get("auto_name").tracks[0].name == "My Look"

    store.apply(
        "auto_name",
        "freeze name",
        lambda doc: setattr(doc.tracks[0], "name_auto", False) or doc,
    )
    store.apply(
        "auto_name",
        "change preset again",
        lambda doc: setattr(doc.tracks[0].clips[0], "preset", "solid") or doc,
    )
    assert store.get("auto_name").tracks[0].name == "My Look"


@pytest.fixture()
def client(tmp_path: Path):
    rigs = tmp_path / "rigs"
    shows = tmp_path / "shows"
    shutil.copytree(RIGS_DIR, rigs)
    shutil.copytree(SHOWS_DIR, shows)
    app = create_app(rigs_dir=rigs, shows_dir=shows, fps=60.0)
    with TestClient(app) as test_client:
        yield test_client


def test_palette_api_roundtrip(client: TestClient):
    created = client.post(
        "/api/palettes",
        json={"name": "API Palette", "colors": [[10, 20, 30], [200, 100, 0]]},
    )
    assert created.status_code == 201
    palette = created.json()
    assert palette["id"] == "api_palette"
    assert any(p["id"] == "api_palette" for p in client.get("/api/palettes").json()["palettes"])
    assert any(
        p["id"] == "api_palette"
        for p in client.get("/api/studio/state").json()["palettes"]
    )

    duplicate = client.post(
        "/api/palettes",
        json={"name": "API Palette", "colors": [[1, 2, 3], [4, 5, 6]]},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] != "api_palette"

    assert client.delete("/api/palettes/api_palette").status_code == 204
    assert client.delete("/api/palettes/synth").status_code == 400


def test_preview_cache_stays_bounded(client: TestClient):
    """Scrubbing previews use distinct cache keys; the cache must not grow."""
    runtime = client.app.state.studio
    for step in range(MAX_PREVIEW_CACHE + 20):
        response = client.get(
            f"/api/presets/solid/preview.png?t={step / 10:.1f}&width=16&height=9"
        )
        assert response.status_code == 200
    assert len(runtime._preview_cache) == MAX_PREVIEW_CACHE

    # A cache hit refreshes recency but does not add an entry.
    hit = next(iter(runtime._preview_cache))
    hit_t = hit.split(":")[2]
    client.get(f"/api/presets/solid/preview.png?t={hit_t}&width=16&height=9")
    assert len(runtime._preview_cache) == MAX_PREVIEW_CACHE
    assert next(reversed(runtime._preview_cache)) == hit


def test_effect_and_filter_preview_png(client: TestClient):
    preset = client.get("/api/presets/shockwave_3d/preview.png")
    assert preset.status_code == 200
    assert preset.headers["content-type"] == "image/png"
    assert _png_size(preset.content) == (128, 72)
    cached = client.get("/api/presets/shockwave_3d/preview.png")
    assert cached.content == preset.content

    filtered = client.get("/api/filters/bloom/preview.png")
    assert filtered.status_code == 200
    assert _png_size(filtered.content) == (48, 27)

    doc = client.get("/api/shows/dance_loft_v1").json()
    clip = next(c for track in doc["tracks"] for c in track["clips"])
    live = client.post(
        "/api/preview/effect",
        json={
            "clip": clip,
            "t": 1.0,
            "timing": doc["timing"],
            "width": 64,
            "height": 36,
        },
    )
    assert live.status_code == 200
    assert live.headers["content-type"] == "image/png"
    assert _png_size(live.content) == (64, 36)
