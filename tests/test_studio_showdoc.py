from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from hueme_studio.showdoc import (
    Clip,
    FilterSpec,
    Keyframe,
    Marker,
    ShowDoc,
    ShowStore,
    ShowStoreError,
    SpatialMask,
    Timing,
    Track,
    evaluate_keyframes,
    evaluate_spatial_mask,
    lerp_color,
    lerp_oklab,
    oklab_to_rgb,
    rgb_to_oklab,
)

ROOT = Path(__file__).resolve().parents[1]
SHOW_PATH = ROOT / "config" / "shows" / "dance_loft_v1.json"


def test_oklab_roundtrip_and_neutral_white():
    for colour in ([255, 0, 0], [12, 200, 90], [255, 255, 255], [0, 0, 0]):
        roundtrip = oklab_to_rgb(rgb_to_oklab(colour))
        assert np.allclose(roundtrip, colour, atol=0.6)
    white_lab = rgb_to_oklab([255, 255, 255])
    assert abs(float(white_lab[0]) - 1.0) < 1e-5
    assert abs(float(white_lab[1])) < 1e-5


def test_oklab_midpoint_avoids_muddy_grey():
    # sRGB interpolation of yellow and blue gives grey; Oklab keeps chroma.
    midpoint = lerp_oklab([255, 255, 0], [0, 0, 255], 0.5)
    assert np.linalg.norm(midpoint - np.array([127.5, 127.5, 127.5])) > 35.0
    endpoints = lerp_oklab([255, 0, 0], [0, 255, 0], np.array([0.0, 1.0]))
    assert np.allclose(endpoints[0], [255, 0, 0], atol=0.6)
    assert np.allclose(endpoints[1], [0, 255, 0], atol=0.6)


def test_colour_keyframes_interpolate_in_oklab():
    mixed = lerp_color([255, 255, 0], [0, 0, 255], 0.5)
    assert len(mixed) == 3
    assert np.linalg.norm(np.asarray(mixed) - 127.5) > 35.0
    rgba = lerp_color([255, 0, 0, 10], [0, 0, 255, 50], 0.5)
    assert len(rgba) == 4
    assert rgba[3] == pytest.approx(30.0)


def test_timing_grid_and_beat_context():
    timing = Timing(bpm=120.0)
    assert timing.beat_grid_s == pytest.approx(0.5)
    assert timing.grid_interval("1/4") == pytest.approx(0.5)
    assert timing.grid_interval("1/8") == pytest.approx(0.25)
    assert timing.grid_interval("1/1") == pytest.approx(2.0)
    assert timing.quantize(1.01, "1/4") == pytest.approx(1.0)
    beat = timing.beat_context(1.0)
    assert beat["beat_index"] == 2
    assert beat["beat_phase"] == pytest.approx(0.0)
    assert beat["bar_pos"] == 2
    with pytest.raises(ValueError):
        timing.grid_interval("1/7")
    repaired = Timing(bpm=112.4, beat_grid_s=112.4)
    assert repaired.beat_grid_s == pytest.approx(60.0 / 112.4)


def test_keyframe_easing_and_hold_semantics():
    linear = [
        Keyframe(t_s=0.0, param="brightness", value=0.0, easing="linear"),
        Keyframe(t_s=2.0, param="brightness", value=1.0, easing="linear"),
    ]
    assert evaluate_keyframes(linear, 1.0)["brightness"] == pytest.approx(0.5)
    assert evaluate_keyframes(linear, -1.0)["brightness"] == pytest.approx(0.0)
    assert evaluate_keyframes(linear, 5.0)["brightness"] == pytest.approx(1.0)

    held = [
        Keyframe(t_s=0.0, param="brightness", value=0.2, easing="hold"),
        Keyframe(t_s=2.0, param="brightness", value=1.0, easing="linear"),
    ]
    assert evaluate_keyframes(held, 1.9)["brightness"] == pytest.approx(0.2)
    assert evaluate_keyframes(held, 2.0)["brightness"] == pytest.approx(1.0)

    ease_in = [
        Keyframe(t_s=0.0, value=0.0, easing="ease_in"),
        Keyframe(t_s=1.0, value=1.0),
    ]
    assert evaluate_keyframes(ease_in, 0.5)["opacity"] == pytest.approx(0.25)


def test_spatial_masks_all_kinds():
    positions = np.array(
        [
            [0.05, 0.05, 0.05],
            [0.5, 0.5, 0.5],
            [0.95, 0.95, 0.95],
            [0.25, 0.75, 0.5],
        ],
        dtype=np.float32,
    )
    assert np.allclose(evaluate_spatial_mask(SpatialMask(), positions), [1, 1, 1, 1])
    plane = evaluate_spatial_mask(
        SpatialMask(
            kind="plane_sweep", axis="x", position=0.5, width=0.2, softness=0.0
        ),
        positions,
    )
    assert np.allclose(plane, [0, 1, 0, 0])
    sphere = evaluate_spatial_mask(
        SpatialMask(
            kind="sphere_narrow", center=[0.5, 0.5, 0.5], radius=0.3, softness=0.0
        ),
        positions,
    )
    assert np.allclose(sphere, [0, 1, 0, 0])
    box = evaluate_spatial_mask(
        SpatialMask(
            kind="box",
            box_min=[0.0, 0.0, 0.0],
            box_max=[0.3, 0.3, 0.3],
            softness=0.0,
        ),
        positions,
    )
    assert np.allclose(box, [1, 0, 0, 0])
    parity = evaluate_spatial_mask(
        SpatialMask(kind="parity", modulo=2, remainder=0, softness=0.0),
        positions,
        np.arange(4),
    )
    assert np.allclose(parity, [1, 0, 1, 0])
    inverted = evaluate_spatial_mask(
        SpatialMask(kind="parity", invert=True), positions, np.arange(4)
    )
    assert np.allclose(inverted, [0, 1, 0, 1])


def test_showdoc_validation_rejects_bad_geometry_and_ids():
    good_clip = Clip(id="c", start_s=0.0, end_s=4.0)
    with pytest.raises(ValidationError):
        Clip(id="c", start_s=1.0, end_s=1.0)
    with pytest.raises(ValidationError):
        Clip(id="c", start_s=0.0, end_s=1.0, fade_in_s=0.8, fade_out_s=0.8)
    with pytest.raises(ValidationError):
        Clip(id="c", start_s=0.0, end_s=1.0, keyframes=[Keyframe(t_s=2.0, value=1.0)])
    with pytest.raises(ValidationError):
        ShowDoc(
            id="s",
            name="S",
            duration_s=4.0,
            tracks=[
                Track(id="t1", name="T1", clips=[good_clip]),
                Track(id="t2", name="T2", clips=[good_clip.model_copy(deep=True)]),
            ],
        )
    with pytest.raises(ValidationError):
        ShowDoc(
            id="s",
            name="S",
            duration_s=4.0,
            timing=Timing(markers=[Marker(t_s=9.0, name="Too late")]),
        )


def test_filter_colour_label_roundtrips_without_affecting_uid():
    clip = Clip(
        id="fx",
        start_s=0.0,
        end_s=1.0,
        filters=[FilterSpec(type="bloom", color="#d06b4a")],
    )
    restored = Clip.model_validate(clip.model_dump())
    assert restored.filters[0].color == "#d06b4a"
    assert restored.filters[0].uid


def test_default_show_loads_with_valid_structure():
    # The show file is user data: assert its invariants, not a demo snapshot.
    show = ShowDoc.from_json(SHOW_PATH.read_text(encoding="utf-8"))
    assert show.id == "dance_loft_v1"
    assert show.duration_s > 0
    assert show.timing.bpm > 0
    assert show.tracks
    assert show.all_clips()
    assert {t.blend_mode for t in show.tracks} <= {
        "normal",
        "add",
        "max",
        "multiply",
        "override",
    }
    assert all(
        c.spatial_mask.kind
        in {"none", "plane_sweep", "sphere_narrow", "box", "parity"}
        for _, c in show.all_clips()
    )
    for track in show.tracks:
        for clip in track.clips:
            assert clip.start_s < clip.end_s
            assert clip.end_s <= show.duration_s


def test_show_store_undo_redo_and_persistence(tmp_path: Path):
    store = ShowStore(tmp_path, history_limit=50)
    base = ShowDoc(
        id="s1",
        name="v0",
        duration_s=8.0,
        tracks=[
            Track(
                id="t",
                name="T",
                clips=[
                    Clip(id="c", start_s=0.0, end_s=8.0, params={"color": [255, 0, 0]})
                ],
            )
        ],
    )
    changes: list[tuple[str, str]] = []
    store.on_change = lambda _sid, _doc, action, label: changes.append((action, label))
    store.create(base)
    assert store.ids() == ["s1"]
    assert (tmp_path / "s1.json").exists()
    assert (tmp_path / "index.json").exists()

    doc = store.get("s1").model_copy(deep=True)
    doc.name = "v1"
    store.commit("s1", doc, "rename v1")
    assert store.get("s1").name == "v1"
    assert store.can_undo("s1")
    assert store.undo("s1").name == "v0"
    assert store.can_redo("s1")
    assert store.redo("s1").name == "v1"

    # A new edit after undo discards the redo branch.
    store.undo("s1")
    doc = store.get("s1").model_copy(deep=True)
    doc.name = "v2"
    store.commit("s1", doc, "rename v2")
    assert not store.can_redo("s1")
    assert [label for action, label in changes if action == "commit"][-1] == "rename v2"


def test_undo_history_keeps_at_least_fifty_snapshots(tmp_path: Path):
    store = ShowStore(tmp_path, history_limit=50)
    doc = ShowDoc(
        id="long",
        name="v0",
        duration_s=4.0,
        tracks=[Track(id="t", name="T", clips=[Clip(id="c", start_s=0.0, end_s=4.0)])],
    )
    store.create(doc)
    for i in range(1, 61):
        current = store.get("long").model_copy(deep=True)
        current.name = f"v{i}"
        store.commit("long", current, f"edit {i}")

    assert store.history("long")["undo_depth"] == 50
    for _ in range(50):
        assert store.undo("long") is not None
    assert store.get("long").name == "v10"
    assert not store.can_undo("long")
    assert store.undo("long") is None
    with pytest.raises(ValueError):
        ShowStore(tmp_path, history_limit=49, persist=False)


def test_no_persist_store_still_loads_existing_shows(tmp_path: Path):
    writer = ShowStore(tmp_path, persist=True)
    writer.create(ShowDoc(id="a", name="A", duration_s=4.0))

    reader = ShowStore(tmp_path, persist=False)
    assert reader.ids() == ["a"]
    assert reader.get("a").name == "A"


def test_show_store_apply_is_atomic_and_rolls_back_on_invalid_mutation(tmp_path: Path):
    store = ShowStore(tmp_path, persist=False)
    store.create(ShowDoc(id="a", name="A", duration_s=4.0))
    with pytest.raises(ValidationError):
        store.apply("a", "bad", lambda d: ShowDoc(id="a", name="A", duration_s=0.0))
    assert store.get("a").duration_s == 4.0

    def rename(current: ShowDoc) -> None:
        current.name = "Renamed"

    out = store.apply("a", "rename", rename)
    assert out.name == "Renamed"
    assert store.undo("a").name == "A"


def test_store_rejects_duplicate_create(tmp_path: Path):
    store = ShowStore(tmp_path, persist=False)
    doc = ShowDoc(id="a", name="A", duration_s=4.0)
    store.create(doc)
    with pytest.raises(ShowStoreError):
        store.create(doc)
