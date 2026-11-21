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
    assert set(first["curves"]) == {"harmonic", "percussive", "prominence"}

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
    assert {"blueprint_duration", "blueprint_bpm"} & {
        issue["code"] for issue in lint["issues"]
    }


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
