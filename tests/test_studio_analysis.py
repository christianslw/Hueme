from __future__ import annotations

import numpy as np
import pytest

from hueme_studio.blueprint import BlueprintEnvelope, _waveform
from hueme_studio.compositor import deterministic_envelope
from hueme_studio.showdoc import ShowDoc, Timing

BASIS = (
    "pulse", "kick", "snare", "hat", "energy", "harmonic", "percussive",
    "prominence", "bass", "mid", "high", "sectionProgress", "beatsToDrop",
    "inDrop",
)


def test_deterministic_analysis_sampling_is_stable_and_bounded():
    show = ShowDoc(id="analysis", name="Analysis", duration_s=64.0, timing=Timing(bpm=120.0))
    samples = [deterministic_envelope(show, t) for t in (0.0, 1.25, 9.5, 33.0)]
    for sample in samples:
        assert set(sample) == set(BASIS)
        assert all(0.0 <= value <= 1.0 for value in sample.values())
    repeated = [deterministic_envelope(show, t) for t in (0.0, 1.25, 9.5, 33.0)]
    assert samples == repeated
    assert samples[0]["inDrop"] == pytest.approx(0.0)
    assert samples[-1]["sectionProgress"] >= 0.0


def test_blueprint_analysis_curves_are_clipped_and_structural():
    blueprint = {
        "bpm": 120.0,
        "beats": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
        "curves": {
            "harmonic": [-0.2, 0.4, 1.4, 0.6, 0.1, 0.2, 0.3, 0.4],
            "percussive": [0.1] * 8,
            "prominence": [0.2] * 8,
            "energy": [0.3] * 8,
            "bass": [0.4] * 8,
        },
        "sections": [{"label": "build", "start": 0.0, "end": 2.0}],
        "drops": [2.0],
        "drop_windows": [{"start": 2.0, "end": 3.0}],
    }
    envelope = BlueprintEnvelope(blueprint)
    before = envelope(None, 1.0)
    inside_drop = envelope(None, 2.5)
    assert set(before) == set(BASIS)
    assert all(0.0 <= value <= 1.0 for value in before.values())
    assert before["sectionProgress"] == pytest.approx(0.5)
    assert inside_drop["inDrop"] == pytest.approx(1.0)
    assert inside_drop["beatsToDrop"] == pytest.approx(1.0)


def test_waveform_buckets_normalize_to_unit_range():
    signal = np.concatenate(
        [np.linspace(-4000, 4000, 2000), np.linspace(4000, -4000, 2000)]
    ).astype(np.float32)
    waveform = _waveform(signal, buckets=256)
    assert len(waveform) == 256
    assert all(0.0 <= value <= 1.0 for value in waveform)
    assert max(waveform) == pytest.approx(1.0, abs=0.02)
