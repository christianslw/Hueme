from __future__ import annotations

import io
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from hueme_studio.blueprint import BlueprintEnvelope, BlueprintStore
from hueme_studio.lint import lint_show
from hueme_studio.server import create_app

ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = ROOT / "config" / "rigs"
SHOWS_DIR = ROOT / "config" / "shows"


def _click_wav(bpm: float = 120.0, seconds: float = 10.0) -> bytes:
    rate = 48000
    t = np.arange(int(rate * seconds), dtype=np.float32) / rate
    y = 0.01 * np.sin(2.0 * np.pi * 220.0 * t)
    click = np.exp(-np.arange(int(rate * 0.03)) / (rate * 0.008)) * np.sin(
        2.0 * np.pi * 80.0 * np.arange(int(rate * 0.03)) / rate
    )
    for beat in np.arange(0.0, seconds, 60.0 / bpm):
        i = int(beat * rate)
        y[i : i + click.size] += 0.8 * click[: max(0, y.size - i)]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((np.clip(y, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


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


def test_extraction_is_deterministic_and_cached(tmp_path):
    store = BlueprintStore(tmp_path / "blueprints")
    payload = _click_wav()
    first = store.extract("show", payload, source="click.wav")
    assert 118.0 <= first["bpm"] <= 122.0
    assert first["beats"] and first["waveform"]
    assert len(first["waveform"]) == 1024
    assert set(first["curves"]) == {
        "harmonic",
        "percussive",
        "prominence",
        "energy",
        "bass",
    }

    def _must_not_analyze(*_args, **_kwargs):
        raise AssertionError("cache miss on identical bytes")

    store._analyze = _must_not_analyze
    second = store.extract("show", payload, source="click.wav")
    assert first == second
    assert store.has("show")

    cache_files = [p for p in (tmp_path / "blueprints").glob("*.*")]
    assert {p.name for p in cache_files} == {
        "index.json",
        f"{first['key']}.json",
        f"{first['key']}.wav",
    }
    index = (tmp_path / "blueprints" / "index.json").read_text()
    assert first["key"] in index
    assert store.audio_path("show") is not None


def test_envelope_is_deterministic_and_bounded(tmp_path):
    store = BlueprintStore(tmp_path / "blueprints")
    blueprint = store.extract("show", _click_wav(), source="click.wav")
    envelope = BlueprintEnvelope(blueprint)
    first = envelope(None, 1.25)
    second = envelope(None, 1.25)
    assert first == second
    assert set(first) >= {"pulse", "kick", "snare", "hat", "energy", "bass"}
    assert all(0.0 <= value <= 1.0 for value in first.values())


def test_upload_attaches_blueprint_and_lints_the_show(client, tmp_path):
    payload = _click_wav(seconds=8.0)
    uploaded = client.post(
        "/api/shows/dance_loft_v1/blueprint?source=click.wav",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200
    summary = uploaded.json()
    assert summary["bpm"] == pytest.approx(120.0, abs=2.0)
    assert summary["audio_available"] is True

    state = client.get("/api/studio/state").json()
    assert state["show"]["blueprint"]["key"] == summary["key"]
    assert state["show"]["blueprint"]["beat_count"] > 0
    assert state["show"]["blueprint"]["audio_available"] is True

    stored = client.get("/api/shows/dance_loft_v1/blueprint")
    assert stored.status_code == 200
    assert stored.json()["key"] == summary["key"]

    audio = client.get("/api/shows/dance_loft_v1/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")
    assert audio.content == payload
    ranged = client.get(
        "/api/shows/dance_loft_v1/audio", headers={"Range": "bytes=0-99"}
    )
    assert ranged.status_code == 206
    assert ranged.content == payload[:100]
    assert ranged.headers["content-range"].startswith("bytes 0-99/")

    runtime = client.app.state.studio
    envelope = runtime.compositor.env_provider(None, 1.0)
    assert envelope["kick"] >= 0.0

    lint = client.get("/api/shows/dance_loft_v1/lint").json()
    assert lint["ok"] is True
    assert not {"blueprint_duration", "blueprint_bpm"} & {
        issue["code"] for issue in lint["issues"]
    }
    synced = client.get("/api/shows/dance_loft_v1").json()
    assert synced["duration_s"] == pytest.approx(8.0, abs=0.05)
    assert synced["timing"]["bpm"] == pytest.approx(120.0, abs=2.0)
    assert synced["timing"]["beat_grid_s"] == pytest.approx(
        60.0 / synced["timing"]["bpm"], rel=1e-3
    )
    assert synced["timing"]["markers"]


def test_bad_wav_is_rejected_and_leaves_show_unchanged(client):
    response = client.post(
        "/api/shows/dance_loft_v1/blueprint",
        content=b"not a wav",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400
    assert client.get("/api/studio/state").json()["show"]["blueprint"] is None
    assert client.get("/api/shows/dance_loft_v1/audio").status_code == 404


def test_missing_group_is_a_lint_error(client):
    runtime = client.app.state.studio
    doc = runtime.shows.get("dance_loft_v1")
    payload = doc.model_dump(mode="json")
    payload["tracks"][0]["target_group"] = "missing_group"
    linted = lint_show(
        type(doc).model_validate(payload),
        runtime.rig,
        blueprint=None,
    )
    assert linted["ok"] is False
    assert "missing_group" in {issue["code"] for issue in linted["issues"]}


def test_ffmpeg_conversion_contract(monkeypatch):
    from types import SimpleNamespace

    from hueme_studio import blueprint as blueprint_module

    wav = _click_wav(seconds=2.0)
    monkeypatch.setattr(blueprint_module.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(wav)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(blueprint_module.subprocess, "run", fake_run)
    converted = blueprint_module.convert_to_wav(b"ID3 fake mp3", "track.mp3")
    assert converted == wav

    monkeypatch.setattr(blueprint_module.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="ffmpeg"):
        blueprint_module.convert_to_wav(b"ID3 fake mp3", "track.mp3")


def test_envelope_uses_real_energy_and_bass_curves():
    blueprint = {
        "bpm": 120.0,
        "beats": [0.0, 0.5, 1.0],
        "downbeats": [0.0],
        "curves": {
            "harmonic": [0.2, 0.2],
            "percussive": [0.3, 0.3],
            "prominence": [0.4, 0.4],
            "energy": [0.1, 0.9],
            "bass": [0.25, 0.75],
        },
    }
    envelope = BlueprintEnvelope(blueprint)
    first = envelope(None, 0.1)
    second = envelope(None, 0.6)
    assert first["energy"] == pytest.approx(0.1)
    assert second["energy"] == pytest.approx(0.9)
    assert first["bass"] == pytest.approx(0.25)
    assert second["bass"] == pytest.approx(0.75)


def test_allin1_http_backend_shapes_blueprint_and_falls_back(tmp_path, monkeypatch):
    from hueme_studio import blueprint as blueprint_module

    store = BlueprintStore(
        tmp_path / "blueprints", analyzer_url="http://analyzer.test:8700"
    )
    assert store.http_analyzer is not None
    bpm = 128.0
    beats = [round(index * 60.0 / bpm, 4) for index in range(17)]
    curves = {
        "harmonic": [0.25] * 17,
        "percussive": [0.75] * 17,
        "prominence": [0.5] * 17,
    }

    def fake_analyze(_wav_bytes, *, source="upload"):
        return {
            "model": "allin1-test",
            "bpm": bpm,
            "beats": beats,
            "downbeats": beats[::4],
            "sections": [
                {"start": 0.0, "end": 4.0, "label": "intro"},
                {"start": 4.0, "end": 7.5, "label": "drop"},
            ],
            "drops": [4.0],
            "curves": curves,
            "duration_s": 8.0,
        }

    monkeypatch.setattr(store.http_analyzer, "analyze", fake_analyze)
    result = store.extract("show", _click_wav(seconds=8.0), source="track.mp3")
    assert result["analyzer"] == "allin1"
    assert result["model"] == "allin1-test"
    assert result["bpm"] == pytest.approx(128.0)
    assert result["sections"][1]["label"] == "drop"
    assert result["drops"] == [4.0]
    assert len(result["waveform"]) == 1024

    def broken(*_args, **_kwargs):
        raise OSError("analyzer offline")

    other = BlueprintStore(tmp_path / "fallback", analyzer_url="http://offline:8700")
    monkeypatch.setattr(other.http_analyzer, "analyze", broken)
    fallback = other.extract("show", _click_wav(seconds=4.0), source="fallback.wav")
    assert fallback["analyzer"] == "numpy"
    assert "offline" in (other.last_analyzer_error or "")


def test_later_blueprint_upload_can_be_disabled_and_manual_sync_is_undoable(client):
    payload = _click_wav(seconds=8.0)
    uploaded = client.post(
        "/api/shows/dance_loft_v1/blueprint?source=click.wav&sync_show=false",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["synced"] is False
    before = client.get("/api/shows/dance_loft_v1").json()["duration_s"]

    synced = client.post("/api/shows/dance_loft_v1/sync-from-blueprint")
    assert synced.status_code == 200
    assert synced.json()["document"]["duration_s"] == pytest.approx(8.0, abs=0.05)
    assert synced.json()["document"]["timing"]["markers"]

    undone = client.post("/api/shows/dance_loft_v1/undo").json()
    assert undone["document"]["duration_s"] == pytest.approx(before)


def test_auto_keyframes_stamp_rhythmic_keyframes_and_undo(client):
    payload = _click_wav(seconds=8.0)
    uploaded = client.post(
        "/api/shows/dance_loft_v1/blueprint?source=click.wav",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200
    initial = client.get("/api/shows/dance_loft_v1").json()
    initial_keys = sum(
        len(clip.get("keyframes", []))
        for track in initial["tracks"]
        for clip in track["clips"]
    )

    response = client.post(
        "/api/shows/dance_loft_v1/auto-keyframes",
        json={
            "source": "kick",
            "param": "opacity",
            "division": "1/8",
            "depth": 0.8,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["added"] > 0
    assert body["analyzer"] in {"numpy", "deterministic", "allin1"}
    document = body["document"]
    keyed = [
        keyframe
        for track in document["tracks"]
        for clip in track["clips"]
        for keyframe in clip["keyframes"]
        if keyframe["param"] == "opacity"
    ]
    assert keyed
    assert all(0.0 <= keyframe["value"] <= 1.0 for keyframe in keyed)

    undone = client.post("/api/shows/dance_loft_v1/undo").json()
    undone_keys = sum(
        len(clip.get("keyframes", []))
        for track in undone["document"]["tracks"]
        for clip in track["clips"]
    )
    assert undone_keys == initial_keys
