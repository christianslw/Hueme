"""Fixture-free front-view effect previews.

The Studio viewport normally projects the rig.  These helpers render a flat
front plane through the same preset and filter code, so the user can inspect
particles, gradients and colour fields without fixture geometry.  Output is a
small PNG rendered on demand; it is an editor preview, not a hardware path.
"""

from __future__ import annotations

import logging
import math
import struct
import zlib
from typing import Any, Sequence

import numpy as np

from hueme_studio.compositor import Compositor, deterministic_envelope
from hueme_studio.filters import FILTERS, FilterContext
from hueme_studio.presets import PresetContext, get_preset
from hueme_studio.showdoc import Clip, FilterSpec, ShowDoc, Timing

log = logging.getLogger("hueme_studio.fxpreview")

MAX_WIDTH = 192
MAX_HEIGHT = 108


def _preview_show(timing: Timing, t: float) -> ShowDoc:
    local_timing = Timing(
        bpm=timing.bpm,
        beat_offset_s=timing.beat_offset_s,
        beat_grid_s=timing.beat_grid_s,
    )
    return ShowDoc(
        id="fx_preview",
        name="FX Preview",
        duration_s=max(1.0, float(t) + 1.0),
        timing=local_timing,
    )


def _front_grid(
    width: int, height: int, room_size: Sequence[float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    size = np.asarray(room_size, dtype=np.float64).reshape(3)
    xs = np.linspace(0.0, 1.0, width)
    zs = np.linspace(0.0, 1.0, height)
    nx, nz = np.meshgrid(xs, zs)
    normalized = np.stack(
        (nx, np.full_like(nx, 0.5), nz), axis=-1
    ).reshape(-1, 3)
    positions = np.stack(
        (nx * size[0], np.full_like(nx, size[1] * 0.5), nz * size[2]),
        axis=-1,
    ).reshape(-1, 3)
    return (
        positions,
        normalized,
        np.zeros(3, dtype=np.float64),
        size,
    )


def _apply_filters(
    rgb: np.ndarray,
    specs: Sequence[FilterSpec],
    *,
    positions: np.ndarray,
    normalized: np.ndarray,
    scene_min: np.ndarray,
    scene_max: np.ndarray,
    room_size: np.ndarray,
    t: float,
    local_t: float,
    timing: Timing,
    beat: dict[str, Any],
    env: dict[str, float],
) -> np.ndarray:
    out = np.asarray(rgb, dtype=np.float32)
    n = int(positions.shape[0])
    indices = np.arange(n, dtype=np.int32)
    for spec in specs:
        if not spec.enabled:
            continue
        info = FILTERS.get(spec.type)
        if info is None:
            continue
        params = info.default_params()
        params.update(spec.params or {})
        context = FilterContext(
            positions=positions,
            normalized=normalized,
            scene_min=scene_min,
            scene_max=scene_max,
            room_size=room_size,
            t=float(t),
            local_t=float(local_t),
            timing=timing,
            beat=beat,
            env={key: max(0.0, min(1.0, float(value))) for key, value in env.items()},
            target_indices=indices,
            target_rank=indices,
            fps=30.0,
        )
        try:
            out = np.asarray(info.apply(out, context, params), dtype=np.float32)
        except Exception:  # preview must never take the Studio down
            log.exception("effect preview filter '%s' failed", spec.type)
    return out


def _render(
    preset_id: str,
    params: dict[str, Any],
    filters: Sequence[FilterSpec],
    opacity: float,
    *,
    t: float,
    timing: Timing,
    width: int,
    height: int,
    room_size: Sequence[float],
    env_values: dict[str, float] | None = None,
) -> np.ndarray:
    width = max(16, min(MAX_WIDTH, int(width)))
    height = max(9, min(MAX_HEIGHT, int(height)))
    preset = get_preset(preset_id)
    positions, normalized, scene_min, size = _front_grid(width, height, room_size)
    n = int(positions.shape[0])
    show = _preview_show(timing, t)
    beat = timing.beat_context(t)
    env = env_values if env_values is not None else deterministic_envelope(show, t)
    ctx = PresetContext(
        positions=positions,
        normalized=normalized,
        scene_min=scene_min,
        scene_max=size,
        room_size=size,
        t=float(t),
        local_t=float(t),
        beat=beat,
        env={key: max(0.0, min(1.0, float(value))) for key, value in env.items()},
        params=dict(params or {}),
        target_indices=np.arange(n, dtype=np.int64),
        target_rank=np.arange(n, dtype=np.int64),
        target_size=n,
    )
    try:
        rgb = np.asarray(preset.render(ctx), dtype=np.float32)
    except Exception:
        log.exception("effect preview preset '%s' failed", preset_id)
        rgb = np.zeros((n, 3), dtype=np.float32)
    if rgb.shape != (n, 3):
        raise ValueError(
            f"preset '{preset_id}' returned {rgb.shape}, expected {(n, 3)}"
        )
    rgb = _apply_filters(
        rgb,
        filters,
        positions=positions,
        normalized=normalized,
        scene_min=scene_min,
        scene_max=size,
        room_size=size,
        t=t,
        local_t=t,
        timing=timing,
        beat=beat,
        env=env,
    )
    rgb = np.clip(rgb * max(0.0, min(1.0, float(opacity))), 0.0, 255.0)
    image = rgb.reshape(height, width, 3)[::-1]
    return np.clip(np.rint(image), 0, 255).astype(np.uint8)


def render_preset_rgb(
    preset_id: str,
    params: dict[str, Any] | None = None,
    *,
    t: float = 0.0,
    timing: Timing | None = None,
    width: int = 128,
    height: int = 72,
    room_size: Sequence[float] = (6.5, 8.0, 5.1),
    filters: Sequence[FilterSpec] = (),
    opacity: float = 1.0,
    env_provider: Any | None = None,
) -> np.ndarray:
    preset = get_preset(preset_id)
    merged = preset.default_params()
    merged.update(params or {})
    timing = timing or Timing(bpm=120.0)
    show = _preview_show(timing, t)
    env = (env_provider or deterministic_envelope)(show, t)
    return _render(
        preset_id,
        merged,
        filters,
        opacity,
        t=t,
        timing=timing,
        width=width,
        height=height,
        room_size=room_size,
        env_values=env,
    )


def render_clip_rgb(
    clip: Clip,
    t: float,
    *,
    timing: Timing | None = None,
    width: int = 128,
    height: int = 72,
    room_size: Sequence[float] = (6.5, 8.0, 5.1),
    env_provider: Any | None = None,
) -> np.ndarray:
    timing = timing or Timing(bpm=120.0)
    show = _preview_show(timing, t)
    beat = timing.beat_context(t)
    env = (env_provider or deterministic_envelope)(show, t)
    local_t = max(0.0, min(clip.duration_s, clip.local_time(t)))
    context = Compositor._expression_context(show, t, beat, env)
    params = clip.effective_params(local_t, context)
    opacity = clip.effective_opacity(local_t, context)
    return _render(
        clip.preset,
        params,
        clip.effective_filters(local_t, context),
        opacity,
        t=t,
        timing=timing,
        width=width,
        height=height,
        room_size=room_size,
        env_values=env,
    )


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def encode_png(image: np.ndarray) -> bytes:
    """Encode a uint8 ``(h, w, 3)`` array without a PIL dependency."""
    array = np.asarray(image, dtype=np.uint8)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("PNG preview needs an (h, w, 3) RGB array")
    height, width = int(array.shape[0]), int(array.shape[1])
    raw = b"".join(
        b"\x00" + array[row].tobytes() for row in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + _png_chunk(b"IDAT", zlib.compress(raw, 6))
        + _png_chunk(b"IEND", b"")
    )


def preview_png(image: np.ndarray) -> bytes:
    return encode_png(image)


__all__ = [
    "encode_png",
    "render_clip_rgb",
    "render_preset_rgb",
]
