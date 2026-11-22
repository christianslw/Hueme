from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from hueme_studio.compositor import Compositor
from hueme_studio.rig import RigLibrary, compile_rig, load_rig
from hueme_studio.showdoc import (
    Clip,
    FilterSpec,
    Keyframe,
    ShowDoc,
    SpatialMask,
    Timing,
    Track,
)

ROOT = Path(__file__).resolve().parents[1]
RIG_PATH = ROOT / "config" / "rigs" / "hueme_loft.json"
SHOW_PATH = ROOT / "config" / "shows" / "dance_loft_v1.json"


def simple_show(
    tracks: list[Track], duration: float = 4.0, bpm: float = 120.0
) -> ShowDoc:
    return ShowDoc(
        id="test_show",
        name="Test Show",
        duration_s=duration,
        timing=Timing(bpm=bpm),
        tracks=tracks,
    )


def solid_clip(
    clip_id: str, color: list[int], start: float = 0.0, end: float = 4.0, **kwargs
) -> Clip:
    return Clip(
        id=clip_id,
        start_s=start,
        end_s=end,
        preset="solid",
        params={"color": color, "brightness": 1.0},
        **kwargs,
    )


@pytest.fixture(scope="module")
def rig():
    return compile_rig(load_rig(RIG_PATH))


def test_basic_normal_blend_track_opacity_and_group_scope(rig):
    base = Track(
        id="base",
        name="Base",
        target_group="all",
        clips=[solid_clip("base", [100, 100, 100])],
    )
    top = Track(
        id="top",
        name="Top",
        target_group="galerie_oben",
        blend_mode="normal",
        clips=[solid_clip("top", [200, 0, 0], opacity=0.5)],
    )
    result = Compositor(rig, simple_show([base, top])).evaluate(1.0)
    assert result.rgb.dtype == np.float32
    assert np.allclose(result.rgb[0], [150.0, 50.0, 50.0])
    assert np.allclose(result.rgb[6], [150.0, 50.0, 50.0])
    assert np.allclose(
        result.rgb[7], [100.0, 100.0, 100.0]
    )  # lower Hue spots untouched


@pytest.mark.parametrize(
    ("mode", "color", "opacity", "expected"),
    [
        ("add", [50, 20, 10], 1.0, [150.0, 120.0, 110.0]),
        ("max", [200, 50, 50], 1.0, [200.0, 100.0, 100.0]),
        (
            "multiply",
            [200, 50, 50],
            1.0,
            [100.0 * 200 / 255, 100.0 * 50 / 255, 100.0 * 50 / 255],
        ),
        ("override", [200, 50, 50], 1.0, [200.0, 50.0, 50.0]),
    ],
)
def test_blend_modes(rig, mode, color, opacity, expected):
    base = Track(
        id="base",
        name="Base",
        target_group="all",
        clips=[solid_clip("base", [100, 100, 100])],
    )
    top = Track(
        id="top",
        name="Top",
        target_group="galerie_oben",
        blend_mode=mode,
        clips=[solid_clip("top", color, opacity=opacity)],
    )
    result = Compositor(rig, simple_show([base, top])).evaluate(1.0)
    assert np.allclose(result.rgb[0], expected, atol=1e-4)
    assert np.allclose(result.rgb[7], [100.0, 100.0, 100.0])


def test_override_disappears_when_clip_ends(rig):
    base = Track(
        id="base",
        name="Base",
        target_group="all",
        clips=[solid_clip("base", [100, 100, 100])],
    )
    top = Track(
        id="top",
        name="Top",
        target_group="all",
        blend_mode="override",
        clips=[solid_clip("top", [200, 0, 0], start=1.0, end=2.0)],
    )
    comp = Compositor(rig, simple_show([base, top]))
    assert np.allclose(comp.evaluate(1.5).rgb[0], [200, 0, 0])
    assert np.allclose(comp.evaluate(2.5).rgb[0], [100, 100, 100])


def test_mute_and_solo(rig):
    base = Track(
        id="base",
        name="Base",
        target_group="all",
        clips=[solid_clip("base", [100, 100, 100])],
    )
    top = Track(
        id="top",
        name="Top",
        target_group="galerie_oben",
        clips=[solid_clip("top", [200, 0, 0])],
    )
    muted = top.model_copy(deep=True)
    muted.muted = True
    result = Compositor(rig, simple_show([base, muted])).evaluate(1.0)
    assert np.allclose(result.rgb[0], [100, 100, 100])

    solo = top.model_copy(deep=True)
    solo.solo = True
    result = Compositor(rig, simple_show([base, solo])).evaluate(1.0)
    assert np.allclose(result.rgb[0], [200, 0, 0])
    assert np.allclose(result.rgb[7], [0, 0, 0])  # non-solo base is excluded


def test_fades_and_keyframed_opacity(rig):
    base = Track(
        id="base",
        name="Base",
        target_group="all",
        clips=[solid_clip("base", [100, 100, 100])],
    )
    faded = Track(
        id="top",
        name="Top",
        target_group="galerie_oben",
        clips=[
            solid_clip(
                "top",
                [200, 0, 0],
                start=1.0,
                end=2.0,
                fade_in_s=1.0,
                fade_out_s=0.0,
            )
        ],
    )
    comp = Compositor(rig, simple_show([base, faded]))
    assert np.allclose(comp.evaluate(1.5).rgb[0], [150.0, 50.0, 50.0], atol=1.0)

    keyed = Track(
        id="keyed",
        name="Keyed",
        target_group="galerie_oben",
        clips=[
            Clip(
                id="keyed",
                start_s=1.0,
                end_s=2.0,
                preset="solid",
                params={"color": [200, 0, 0]},
                keyframes=[
                    Keyframe(t_s=0.0, param="opacity", value=0.0, easing="linear"),
                    Keyframe(t_s=1.0, param="opacity", value=1.0, easing="linear"),
                ],
            )
        ],
    )
    comp = Compositor(rig, simple_show([base, keyed]))
    assert np.allclose(comp.evaluate(1.5).rgb[0], [150.0, 50.0, 50.0], atol=1.0)


def test_spatial_masks_apply_inside_compositor(rig):
    clip = solid_clip(
        "masked",
        [255, 255, 255],
        spatial_mask=SpatialMask(
            kind="box",
            box_min=[0.0, 0.0, 0.0],
            box_max=[0.3, 1.0, 1.0],
            softness=0.0,
        ),
    )
    track = Track(id="mask", name="Mask", target_group="all", clips=[clip])
    result = Compositor(rig, simple_show([track])).evaluate(1.0)
    assert result.rgb[0].max() > 0.0  # gallery 1 at x=0.085
    assert result.rgb[6].max() == 0.0  # gallery 7 at x=0.915
    assert result.rgb[189].max() == 0.0  # last strip voxel at x=0.946


def test_parity_mask_selects_alternating_voxels(rig):
    track = Track(
        id="parity",
        name="Parity",
        target_group="all",
        clips=[
            solid_clip(
                "parity",
                [120, 120, 120],
                spatial_mask=SpatialMask(kind="parity", modulo=2, remainder=0),
            )
        ],
    )
    result = Compositor(rig, simple_show([track])).evaluate(1.0)
    assert np.all(result.rgb[0::2].max(axis=1) > 0)
    assert np.all(result.rgb[1::2].max(axis=1) == 0)


def test_zcl_snap_propagates_per_fixture_on_boundary_beat_and_hold(rig):
    clip = solid_clip(
        "snap",
        [120, 0, 0],
        start=0.25,
        end=2.0,
        snap_zcl=True,
        keyframes=[Keyframe(t_s=0.95, param="brightness", value=1.0, easing="hold")],
    )
    track = Track(id="snap", name="Snap", target_group="galerie_oben", clips=[clip])
    comp = Compositor(rig, simple_show([track]))
    at_start = comp.evaluate(0.25)
    assert at_start.snap_zcl[:7].all()
    assert not at_start.snap_zcl[7:].any()

    off_beat = comp.evaluate(1.1)
    assert not off_beat.snap_zcl.any()

    on_beat = comp.evaluate(1.0)
    assert on_beat.snap_zcl[:7].all()

    on_hold = comp.evaluate(1.2)
    assert on_hold.snap_zcl[:7].all()

    at_end = comp.evaluate(2.0)
    assert at_end.snap_zcl[:7].all()

    clip.snap_zcl = False
    assert not comp.evaluate(0.25).snap_zcl.any()


def test_evaluation_is_repeatable_and_no_show_noops(rig):
    show = ShowDoc.from_json(SHOW_PATH.read_text(encoding="utf-8"))
    comp = Compositor(rig, show)
    for t in (5.0, 40.0, 120.0, 200.0):
        first = comp.evaluate(t)
        second = comp.evaluate(t)
        assert np.array_equal(first.rgb, second.rgb)
        assert np.array_equal(first.snap_zcl, second.snap_zcl)
        assert np.isfinite(first.rgb).all()
        assert first.rgb.min() >= 0.0 and first.rgb.max() <= 255.0
        assert first.active_clips == second.active_clips


def test_override_blackout_and_default_show_render(rig):
    # Blackout semantics are independent of the user-authored demo show.
    show = simple_show(
        [
            Track(id="base", name="Base", clips=[solid_clip("base", [255, 255, 255])]),
            Track(
                id="cut",
                name="Blackout",
                blend_mode="override",
                clips=[solid_clip("black", [0, 0, 0], start=1.0, end=2.0)],
            ),
        ]
    )
    comp = Compositor(rig, show)
    assert comp.evaluate(0.5).rgb.max() > 0.0
    assert np.allclose(comp.evaluate(1.5).rgb, 0.0)
    assert comp.evaluate(2.5).rgb.max() > 0.0

    # The real show is runtime data: it must evaluate at any point in its
    # duration without NaNs or out-of-range values.
    user_show = ShowDoc.from_json(SHOW_PATH.read_text(encoding="utf-8"))
    user_comp = Compositor(rig, user_show)
    for t in np.linspace(0.5, user_show.duration_s - 0.5, 4):
        frame = user_comp.evaluate(float(t))
        assert np.isfinite(frame.rgb).all()
        assert frame.rgb.min() >= 0.0 and frame.rgb.max() <= 255.0


def test_empty_rig_is_a_valid_sandbox():
    lib = RigLibrary(ROOT / "config" / "rigs")
    empty = lib.load("empty_loft").compile()
    track = Track(
        id="t", name="T", target_group="all", clips=[solid_clip("c", [255, 255, 255])]
    )
    result = Compositor(empty, simple_show([track])).evaluate(1.0)
    assert result.rgb.shape == (0, 3)
    assert result.snap_zcl.shape == (0,)
    assert result.active_clips


def test_filters_are_applied_without_changing_showdoc(rig):
    clip = solid_clip(
        "fx",
        [120, 120, 120],
        filters=[FilterSpec(type="beat_gate", params={"duty": 0.2, "floor": 0.0})],
    )
    track = Track(id="fx", name="FX", target_group="galerie_oben", clips=[clip])
    comp = Compositor(rig, simple_show([track]))
    early = comp.evaluate(0.0)
    late = comp.evaluate(0.25)
    assert early.rgb[0].max() > 0.0
    assert late.rgb[0].max() == 0.0
    assert clip.filters[0].params == {"duty": 0.2, "floor": 0.0}


def test_expression_generator_overrides_opacity_and_parameters(rig):
    clip = Clip(
        id="expr",
        start_s=0.0,
        end_s=4.0,
        preset="solid",
        opacity=1.0,
        params={"color": [200, 0, 0], "brightness": 1.0},
        expressions={
            "opacity": "0.5 + 0.5 * sin(t * TAU)",
            "brightness": "clamp(val * (0.5 + kick), 0, 1)",
        },
    )
    # Unknown symbol must fail validation rather than silently evaluating.
    with pytest.raises(Exception):
        Clip(
            id="bad",
            start_s=0.0,
            end_s=1.0,
            preset="solid",
            expressions={"opacity": "spring"},
        )
    track = Track(id="expr", name="Expr", target_group="all", clips=[clip])
    comp = Compositor(rig, simple_show([track]))
    early = comp.evaluate(0.0).rgb[0]
    quarter = comp.evaluate(0.25).rgb[0]
    assert np.allclose(early, [100.0, 0.0, 0.0], atol=2.0)
    assert quarter[0] > 0.0


def test_safe_expression_evaluator_has_no_builtins_or_attribute_access():
    from hueme_studio.expressions import ExpressionError, evaluate_expression

    assert evaluate_expression("clamp(val + sin(TAU / 4), 0, 10)", {"val": 2.0}) == pytest.approx(
        3.0
    )
    for expression in (
        "__import__('os').system('true')",
        "t.__class__",
        "val[0]",
        "open('x')",
        "lambda: 1",
    ):
        with pytest.raises(ExpressionError):
            evaluate_expression(expression, {"val": 1.0, "t": 0.0})


def test_clipping_mask_uses_the_layer_below_alpha(rig):
    base = Track(
        id="base",
        name="Base",
        target_group="all",
        clips=[
            solid_clip(
                "base",
                [100, 100, 100],
                spatial_mask=SpatialMask(kind="parity", modulo=2, remainder=0),
            )
        ],
    )
    clipped = Track(
        id="clip",
        name="Clipped",
        target_group="all",
        clipped_to_below=True,
        blend_mode="normal",
        clips=[solid_clip("clip", [255, 0, 0])],
    )
    result = Compositor(rig, simple_show([base, clipped])).evaluate(1.0)
    assert np.allclose(result.rgb[0], [255, 0, 0])
    assert np.allclose(result.rgb[1], [0, 0, 0])
    assert np.allclose(result.rgb[2], [255, 0, 0])


def test_global_tracks_evaluate_last_and_single_fixture_targets_work(rig):
    base = Track(
        id="base",
        name="Base",
        target_group="all",
        clips=[solid_clip("base", [50, 50, 50])],
    )
    single = Track(
        id="single",
        name="Single",
        target_group="galerie_01",
        clips=[solid_clip("single", [0, 255, 0])],
    )
    global_track = Track(
        id="global",
        name="Global",
        target_group="all",
        kind="global",
        clips=[solid_clip("global", [200, 0, 0])],
    )
    result = Compositor(rig, simple_show([global_track, base, single])).evaluate(1.0)
    assert np.allclose(result.rgb[0], [200, 0, 0])
    assert np.allclose(result.rgb[7], [200, 0, 0])


def test_sub_track_requires_parent_and_inherits_order(rig):
    show = simple_show(
        [
            Track(id="parent", name="Parent", target_group="all"),
            Track(
                id="child",
                name="Child",
                target_group="boden_01",
                kind="sub",
                parent_id="parent",
            ),
        ]
    )
    assert [track.id for track in show.evaluation_order()] == ["parent", "child"]
    with pytest.raises(Exception):
        ShowDoc(
            id="bad",
            name="Bad",
            duration_s=1.0,
            tracks=[Track(id="orphan", name="Orphan", kind="sub")],
        )
