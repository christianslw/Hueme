from __future__ import annotations

import math
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hueme_studio.blueprint import BlueprintEnvelope
from hueme_studio.compositor import Compositor, deterministic_envelope
from hueme_studio.expressions import (
    ExpressionError,
    evaluate_expression,
    expression_names,
)
from hueme_studio.server import create_app
from hueme_studio.showdoc import Clip, ShowDoc, Timing

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


def test_expression_function_vocabulary_is_deterministic():
    assert evaluate_expression("smoothstep(0, 1, 0.5)") == pytest.approx(0.5)
    assert evaluate_expression("lerp(2, 4, 0.25)") == pytest.approx(2.5)
    assert evaluate_expression("step(0.5, 0.5)") == pytest.approx(1.0)
    assert evaluate_expression("wrap(1.25, 0, 1)") == pytest.approx(0.25)
    assert evaluate_expression("pulse(0.1, 0.5)") == pytest.approx(1.0)
    assert evaluate_expression("pulse(0.9, 0.5)") == pytest.approx(0.0)
    first = evaluate_expression("fbm(0.1, 0.2, 0.3, 2.0)")
    second = evaluate_expression("fbm(0.1, 0.2, 0.3, 2.0)")
    assert first == second
    assert 0.0 <= first <= 1.0


def test_env_string_literals_are_only_allowed_for_named_curves():
    assert evaluate_expression("env('kick', 0)", {"kick": 0.8}) == pytest.approx(0.8)
    assert evaluate_expression("env('kick', 0.5)", {"kick": 0.8}) == pytest.approx(
        0.8 * math.exp(-1.5)
    )
    with pytest.raises(ExpressionError):
        evaluate_expression("'kick'", {"kick": 0.8})
    with pytest.raises(ExpressionError):
        evaluate_expression("env('kick', 0) + 'x'", {"kick": 0.8})
    for expression in ("t.__class__", "val[0]", "__import__('os')"):
        with pytest.raises(ExpressionError):
            evaluate_expression(expression, {"t": 0.0, "val": 1.0})


def test_expression_names_include_new_audio_and_structure_variables():
    names = expression_names(
        "bass + mid + high + harmonic + percussive + prominence + bar + "
        "barPhase + phrase + phrasePhase + sectionProgress + beatsToDrop + inDrop"
    )
    assert names == {
        "bass", "mid", "high", "harmonic", "percussive", "prominence",
        "bar", "barPhase", "phrase", "phrasePhase", "sectionProgress",
        "beatsToDrop", "inDrop",
    }
    clip = Clip(
        id="expr",
        start_s=0.0,
        end_s=2.0,
        preset="solid",
        expressions={
            "brightness": "clamp(0.2 + bass + sectionProgress + beatsToDrop + inDrop, 0, 1)"
        },
    )
    assert clip.expressions["brightness"]


def test_deterministic_and_blueprint_contexts_share_every_basis():
    show = ShowDoc(id="s", name="S", duration_s=32.0, timing=Timing(bpm=120.0))
    env = deterministic_envelope(show, 3.1)
    assert set(env) >= {
        "pulse", "kick", "snare", "hat", "energy", "harmonic", "percussive",
        "prominence", "bass", "mid", "high", "sectionProgress", "beatsToDrop",
        "inDrop",
    }
    assert all(0.0 <= value <= 1.0 for value in env.values())
    context = Compositor._expression_context(show, 3.1, show.beat_context(3.1), env)
    assert context["bass"] == pytest.approx(env["bass"])
    assert context["bar"] >= 0
    assert context["phrasePhase"] >= 0.0

    blueprint = {
        "bpm": 120.0,
        "beats": [0.0, 0.5, 1.0, 1.5, 2.0],
        "curves": {
            "harmonic": [0.1, 0.2, 0.3, 0.4, 0.5],
            "percussive": [0.5, 0.5, 0.5, 0.5, 0.5],
            "prominence": [0.4] * 5,
            "energy": [0.2] * 5,
            "bass": [0.3] * 5,
        },
        "sections": [{"label": "drop", "start": 1.0, "end": 2.0}],
        "drops": [1.0],
        "drop_windows": [{"start": 1.0, "end": 2.0}],
    }
    bp_env = BlueprintEnvelope(blueprint)(None, 1.5)
    assert bp_env["sectionProgress"] == pytest.approx(0.5)
    assert bp_env["inDrop"] == pytest.approx(1.0)
    assert 0.0 <= bp_env["beatsToDrop"] <= 1.0
    assert set(bp_env) >= set(env)


def test_expression_validation_endpoint(client):
    ok = client.post(
        "/api/expressions/validate",
        json={"expression": "clamp(sectionProgress + beatsToDrop, 0, 1)", "context": {}},
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    bad = client.post(
        "/api/expressions/validate",
        json={"expression": "unknown_symbol + 1", "context": {}},
    )
    assert bad.status_code == 200
    assert bad.json()["ok"] is False
    assert "unknown" in bad.json()["error"]
