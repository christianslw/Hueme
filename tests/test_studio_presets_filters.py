from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from hueme_studio.filters import (
    FILTERS,
    FilterContext,
    apply_filter_stack,
    audio_mod,
    beat_gate,
    bloom,
    dim2warm,
    inertia,
    mirror_symmetry,
)
from hueme_studio.presets import PRESETS, PresetContext, render_preset
from hueme_studio.rig import compile_rig
from hueme_studio.rig import load_rig
from hueme_studio.showdoc import FilterSpec, Timing

ROOT = Path(__file__).resolve().parents[1]
RIG_PATH = ROOT / "config" / "rigs" / "hueme_loft.json"


def make_ctx(t: float = 10.0, params=None) -> PresetContext:
    rig = compile_rig(load_rig(RIG_PATH))
    indices = rig.group_indices("galerie_oben")
    beats = t * 2.0
    phase = beats % 1.0
    return PresetContext(
        positions=rig.positions,
        normalized=rig.normalized_positions,
        scene_min=np.asarray(rig.room.min),
        scene_max=np.asarray(rig.room.max),
        room_size=rig.room.size,
        t=t,
        local_t=max(0.0, t - 10.0),
        beat={
            "beat_phase": phase,
            "bar_phase": (beats % 4.0) / 4.0,
            "beat_count": int(beats),
            "bar_pos": int(beats) % 4,
        },
        env={
            "pulse": 0.2 + 0.7 * np.exp(-phase * 3.0),
            "kick": 0.7 * np.exp(-phase * 8.0),
            "snare": 0.2 if int(beats) % 4 in (1, 3) else 0.04,
            "hat": 0.2,
            "energy": 0.5,
            "harmonic": 0.35,
        },
        params=params or {},
        target_indices=indices,
        target_rank=rig.group_rank("galerie_oben"),
        target_size=indices.size,
    )


def make_filter_ctx(rig=None, t: float = 0.0) -> FilterContext:
    if rig is None:
        rig = compile_rig(load_rig(RIG_PATH))
    indices = rig.group_indices("all")
    return FilterContext(
        positions=rig.positions,
        normalized=rig.normalized_positions,
        scene_min=np.asarray(rig.room.min),
        scene_max=np.asarray(rig.room.max),
        room_size=rig.room.size,
        t=t,
        local_t=t,
        timing=Timing(bpm=120.0),
        beat={
            "beat_phase": (t / 0.5) % 1.0,
            "bar_phase": 0.0,
            "beat_count": int(t / 0.5),
            "bar_pos": 0,
        },
        env={"pulse": 0.7, "kick": 0.8, "snare": 0.2, "hat": 0.1, "energy": 0.5},
        target_indices=indices,
        target_rank=rig.group_rank("all"),
    )


def test_all_presets_render_deterministically():
    assert set(PRESETS) >= {
        "solid",
        "gradient_3d",
        "pulse_breathe",
        "chase_scanner",
        "pattern_bands",
        "ma3_wings",
        "particles_3d",
        "sparkle_twinkle",
        "shockwave_3d",
        "strobe_blinder",
        "turrell_horizon",
        "caustic_abyss",
        "blackbody_forge",
        "noise_plasma",
        "aurora_veil",
        "cell_bloom",
        "silk_folds",
        "color_wash",
        "wave_interference",
        "shutter_matrix",
        "emitter_swarm",
    }
    assert len(PRESETS) == 31
    for preset_id in PRESETS:
        ctx = make_ctx(10.0)
        first = render_preset(preset_id, ctx)
        second = render_preset(preset_id, ctx)
        assert first.shape == (ctx.n, 3)
        assert first.dtype == np.float32
        assert np.isfinite(first).all()
        assert first.min() >= 0.0 and first.max() <= 255.0
        assert np.array_equal(first, second)
        if preset_id not in {"solid", "strobe_blinder"}:
            later = render_preset(preset_id, make_ctx(12.3))
            assert not np.array_equal(first, later), (
                f"{preset_id} did not animate with time"
            )


def test_ma3_wings_only_illuminates_its_target_group():
    ctx = make_ctx(10.0, {"brightness": 1.0, "bg_level": 0.05})
    out = render_preset("ma3_wings", ctx)
    lit = np.flatnonzero(out.max(axis=1) > 0.0)
    assert set(lit.tolist()).issubset(set(ctx.target_indices.tolist()))
    assert lit.size > 0


def test_blackbody_is_warm_and_never_nan_without_audio():
    out = render_preset("blackbody_forge", make_ctx(10.0))
    warm = out[out.max(axis=1) > 0]
    assert warm.size > 0
    assert float(warm[:, 0].mean()) > float(warm[:, 2].mean())


def test_filters_are_non_destructive_and_catalogued():
    rig = compile_rig(load_rig(RIG_PATH))
    rgb = np.full((rig.voxel_count, 3), 100.0, dtype=np.float32)
    original = rgb.copy()
    ctx = make_filter_ctx(rig)
    out = apply_filter_stack(
        rgb,
        [
            FilterSpec(
                type="beat_gate", params={"division": "1/4", "duty": 0.4, "floor": 0.05}
            ),
            FilterSpec(type="dim2warm", params={"amount": 0.5}),
        ],
        ctx,
    )
    assert np.array_equal(rgb, original)
    assert out.shape == rgb.shape
    assert set(FILTERS) == {
        "spatial_blur",
        "bloom",
        "grain_noise",
        "mirror_symmetry",
        "beat_gate",
        "dim2warm",
        "color_grade",
        "strobe_shutter",
        "invert_color",
        "inertia",
        "audio_mod",
        "hue_rotate",
        "saturation",
        "brightness_curve",
        "scanline",
        "posterize",
    }


def test_filter_kind_accepts_every_registered_filter():
    for filter_id in FILTERS:
        assert FilterSpec(type=filter_id).type == filter_id


def test_every_filter_runs_on_a_gradient_without_nan():
    ctx = make_filter_ctx()
    base = render_preset("gradient_3d", make_ctx(4.0, {"brightness": 1.0}))
    assert set(FILTERS) >= {"hue_rotate", "saturation", "brightness_curve", "scanline", "posterize"}
    for filter_id, info in FILTERS.items():
        first = info.apply(base, ctx, info.default_params())
        second = info.apply(base, ctx, info.default_params())
        assert first.shape == base.shape
        assert np.isfinite(first).all()
        assert first.min() >= 0.0 and first.max() <= 255.0
        assert np.array_equal(first, second)
    # These filters must actually change the source buffer.
    for filter_id in ("hue_rotate", "saturation", "brightness_curve", "posterize"):
        info = FILTERS[filter_id]
        out = info.apply(base, ctx, {**info.default_params(), "mix": 1.0})
        assert not np.array_equal(out, base)


def test_beat_gate_opens_on_the_grid_and_closes_after_duty():
    ctx = make_filter_ctx()
    rgb = np.full((ctx.n, 3), 100.0, dtype=np.float32)
    open_out = beat_gate(rgb, ctx, {"duty": 0.4, "floor": 0.05, "softness": 0.0})
    late = make_filter_ctx(t=0.3)
    closed_out = beat_gate(rgb, late, {"duty": 0.4, "floor": 0.05, "softness": 0.0})
    assert np.allclose(open_out, 100.0)
    assert np.allclose(closed_out, 5.0)


def test_mirror_symmetry_maps_the_gallery_across_x():
    rig = compile_rig(load_rig(RIG_PATH))
    rgb = np.zeros((rig.voxel_count, 3), dtype=np.float32)
    rgb[0] = [250, 10, 5]
    rgb[6] = [5, 10, 250]
    out = mirror_symmetry(rgb, make_filter_ctx(rig), {"axis": "x", "mix": 1.0})
    assert np.allclose(out[6], [250, 10, 5])
    assert np.allclose(out[0], [5, 10, 250])


def test_dim2warm_and_envelope_modulation():
    ctx = make_filter_ctx()
    rgb = np.tile(np.array([[45.0, 22.0, 10.0]], dtype=np.float32), (ctx.n, 1))
    warm = dim2warm(rgb, ctx, {"amount": 1.0, "curve": 1.0})
    assert float(warm[:, 2].mean()) < float(rgb[:, 2].mean())
    assert float(warm[:, 0].mean() / max(1e-6, warm[:, 2].mean())) > float(
        rgb[:, 0].mean() / rgb[:, 2].mean()
    )
    dimmed = audio_mod(rgb, ctx, {"source": "kick", "depth": 0.5, "mode": "brightness"})
    assert float(dimmed.mean()) < float(rgb.mean())


def test_bloom_spreads_light_from_bright_voxels():
    rig = compile_rig(load_rig(RIG_PATH))
    rgb = np.zeros((rig.voxel_count, 3), dtype=np.float32)
    rgb[0] = [255.0, 255.0, 255.0]
    original = rgb.copy()
    out = bloom(
        rgb,
        make_filter_ctx(rig),
        {"threshold": 100.0, "radius_m": 1.0, "gain": 1.0, "mix": 1.0},
    )
    assert out[0, 0] == pytest.approx(255.0)
    assert out[1:].max() > 0.0
    assert np.array_equal(rgb, original)


def test_inertia_uses_reference_and_is_deterministic():
    ctx = make_filter_ctx()
    rgb = np.full((ctx.n, 3), 200.0, dtype=np.float32)
    reference = np.zeros_like(rgb)
    ctx.inertia_reference = reference
    out = inertia(rgb, ctx, {"mix": 0.5})
    assert np.allclose(out, 100.0)
    ctx.inertia_reference = None
    assert np.array_equal(inertia(rgb, ctx, {"mix": 0.5}), rgb)


def test_preset_param_defaults_are_available():
    from hueme_studio.presets import (
        PRESET_ALIASES,
        default_params,
        get_preset,
        render_preset,
    )

    assert default_params("solid")["color"] == [255, 110, 20]
    assert get_preset("turrell_skyspace").id == "turrell_horizon"
    assert get_preset("ma3_matrix_wing").id == "ma3_wings"
    assert get_preset("quad_gradient").id == "color_wash"
    assert PRESET_ALIASES["ma3_matrix_wing"] == "ma3_wings"
    assert PRESET_ALIASES["quad_gradient"] == "color_wash"
    with pytest.raises(KeyError):
        get_preset("missing")
    alias_out = render_preset("turrell_skyspace", make_ctx(10.0))
    canonical_out = render_preset("turrell_horizon", make_ctx(10.0))
    assert np.array_equal(alias_out, canonical_out)


def test_phase2_presets_are_deterministic_and_expression_addressable():
    from hueme_studio.showdoc import Clip

    for preset_id in (
        "color_wash",
        "wave_interference",
        "shutter_matrix",
        "emitter_swarm",
    ):
        first = render_preset(preset_id, make_ctx(10.0))
        second = render_preset(preset_id, make_ctx(10.0))
        assert np.array_equal(first, second)
        assert np.isfinite(first).all()

    swarm = render_preset(
        "emitter_swarm",
        make_ctx(10.0, {"birth_rate": 4.0, "lifetime": 1.5, "turbulence": 0.0}),
    )
    assert swarm.max() > 0.0
    clip = Clip(
        id="swarm",
        start_s=0.0,
        end_s=4.0,
        preset="emitter_swarm",
        expressions={"birth_rate": "4 + 6 * kick", "particle_size": "0.04 + 0.02 * energy"},
    )
    params = clip.effective_params(0.0, {"kick": 0.5, "energy": 0.25})
    assert params["birth_rate"] == pytest.approx(7.0)
    assert params["particle_size"] == pytest.approx(0.045)
