"""Non-destructive filter stack for clip voxel buffers.

Filters run after a preset has rendered a clip and before the clip is blended
into its track.  They never mutate the incoming buffer.  ``inertia`` is a
deterministic look-back filter: the compositor supplies a reference buffer
built from earlier samples of the same stateless preset, so seeking to a
timestamp still yields a repeatable result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from hueme_studio.presets import ParamSpec
from hueme_studio.showdoc import FilterSpec, Timing, lerp_oklab

FilterApply = Callable[[np.ndarray, "FilterContext", dict[str, Any]], np.ndarray]


@dataclass(slots=True)
class FilterContext:
    positions: np.ndarray
    normalized: np.ndarray
    scene_min: np.ndarray
    scene_max: np.ndarray
    room_size: np.ndarray
    t: float
    local_t: float
    timing: Timing
    beat: dict[str, float | int | bool]
    env: dict[str, float]
    target_indices: np.ndarray
    target_rank: np.ndarray
    fps: float = 60.0
    inertia_reference: np.ndarray | None = None
    mirror_sources: dict[int, np.ndarray] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.positions.shape[0])


@dataclass(frozen=True, slots=True)
class FilterInfo:
    id: str
    name: str
    description: str
    params: tuple[ParamSpec, ...]
    apply: FilterApply

    def default_params(self) -> dict[str, Any]:
        return {p.key: p.default for p in self.params}

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "params": [p.as_dict() for p in self.params],
        }


def _num(params: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(params.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _rgb(params: dict[str, Any], key: str, default: list[float]) -> np.ndarray:
    raw = params.get(key, default)
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        raw = default
    return np.asarray([float(raw[0]), float(raw[1]), float(raw[2])], dtype=np.float64)


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    if abs(edge1 - edge0) < 1e-12:
        return np.where(x >= edge1, 1.0, 0.0)
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def mirror_symmetry(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    """Mirror voxel colours across the room centre on X, Y or Z."""
    axis_name = str(params.get("axis", "x"))
    axis = {"x": 0, "y": 1, "z": 2}.get(axis_name, 0)
    mix = float(np.clip(_num(params, "mix", 1.0), 0.0, 1.0))
    if ctx.n == 0 or mix <= 0.0:
        return rgb.astype(np.float32, copy=True)
    source = ctx.mirror_sources.get(axis)
    if source is None:
        pos = ctx.normalized.astype(np.float64)
        target = pos.copy()
        target[:, axis] = 1.0 - target[:, axis]
        # N is small (typically < 512), so a direct nearest-voxel search is
        # cheaper and more predictable than building a spatial index every
        # frame. The mapping is cached because rig geometry is static.
        d2 = np.sum((target[:, None, :] - pos[None, :, :]) ** 2, axis=2)
        source = np.argmin(d2, axis=1)
        ctx.mirror_sources[axis] = source
    mirrored = rgb[source]
    return ((1.0 - mix) * rgb + mix * mirrored).astype(np.float32)


def beat_gate(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    """Gate brightness on the exact musical grid (deterministic, not reactive)."""
    division = str(params.get("division", "1/4"))
    duty = float(np.clip(_num(params, "duty", 0.55), 0.05, 0.99))
    floor = float(np.clip(_num(params, "floor", 0.06), 0.0, 1.0))
    softness = float(np.clip(_num(params, "softness", 0.035), 0.0, 0.25))
    try:
        interval = ctx.timing.grid_interval(division)
    except ValueError:
        interval = ctx.timing.grid_interval("1/4")
    phase = ((ctx.t - ctx.timing.beat_offset_s) / interval) % 1.0
    if softness <= 1e-9:
        gate = np.where(phase <= duty, 1.0, 0.0)
    else:
        gate = 1.0 - _smoothstep(duty - softness, duty + softness, np.asarray(phase))
    level = floor + (1.0 - floor) * gate
    return (rgb * float(level)).astype(np.float32)


def dim2warm(rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]) -> np.ndarray:
    """Shift dim colours toward a 1800K amber while preserving bright hues."""
    amount = float(np.clip(_num(params, "amount", 0.65), 0.0, 1.0))
    curve = max(0.2, _num(params, "curve", 1.3))
    warm = _rgb(params, "warm_color", [255, 115, 24])
    if amount <= 0.0 or ctx.n == 0:
        return rgb.astype(np.float32, copy=True)
    lum = np.clip(
        (0.2126 * rgb[:, 0] + 0.7152 * rgb[:, 1] + 0.0722 * rgb[:, 2]) / 255.0,
        0.0,
        1.0,
    )
    mix = amount * (1.0 - lum) ** curve
    target = warm[None, :] * lum[:, None]
    out = lerp_oklab(rgb, target, mix)
    return np.clip(out, 0.0, 255.0).astype(np.float32)


def inertia(rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]) -> np.ndarray:
    """Blend toward the compositor's deterministic look-back reference."""
    mix = float(np.clip(_num(params, "mix", 0.65), 0.0, 1.0))
    if ctx.inertia_reference is None or mix <= 0.0:
        return rgb.astype(np.float32, copy=True)
    ref = np.asarray(ctx.inertia_reference, dtype=np.float64)
    if ref.shape != rgb.shape:
        return rgb.astype(np.float32, copy=True)
    return ((1.0 - mix) * rgb + mix * ref).astype(np.float32)


def audio_mod(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    """Deterministic audio/beat envelope modulation supplied by the compositor."""
    source = str(params.get("source", "kick"))
    mode = str(params.get("mode", "brightness"))
    depth = float(np.clip(_num(params, "depth", 0.45), 0.0, 1.0))
    value = float(np.clip(ctx.env.get(source, 0.0), 0.0, 1.0))
    if mode == "warmth":
        warm = _rgb(params, "warm_color", [255, 125, 32])
        mix = depth * value
        return np.clip(lerp_oklab(rgb, warm, mix), 0.0, 255.0).astype(np.float32)
    factor = (1.0 - depth) + depth * value
    return (rgb * factor).astype(np.float32)


FILTERS: dict[str, FilterInfo] = {
    "mirror_symmetry": FilterInfo(
        id="mirror_symmetry",
        name="Mirror Symmetry",
        description="Mirror voxel colour across the room centre on one axis.",
        params=(
            ParamSpec(
                "axis",
                "Mirror Axis",
                "select",
                "x",
                options=(("x", "X / Width"), ("y", "Y / Depth"), ("z", "Z / Height")),
            ),
            ParamSpec("mix", "Amount", "range", 1.0, 0.0, 1.0, 0.01),
        ),
        apply=mirror_symmetry,
    ),
    "beat_gate": FilterInfo(
        id="beat_gate",
        name="Beat Gate",
        description="Hard musical gate on the beat grid with a short release.",
        params=(
            ParamSpec(
                "division",
                "Grid",
                "select",
                "1/4",
                options=(
                    ("1/4", "Quarter"),
                    ("1/8", "Eighth"),
                    ("1/16", "Sixteenth"),
                    ("1/2", "Half"),
                ),
            ),
            ParamSpec("duty", "Duty Cycle", "range", 0.55, 0.05, 0.99, 0.01),
            ParamSpec("floor", "Closed Level", "range", 0.06, 0.0, 1.0, 0.01),
            ParamSpec("softness", "Edge Softness", "range", 0.035, 0.0, 0.25, 0.005),
        ),
        apply=beat_gate,
    ),
    "dim2warm": FilterInfo(
        id="dim2warm",
        name="Dim to Warm",
        description="Bias low-level output toward a 1800K amber, like dimmed filament.",
        params=(
            ParamSpec("amount", "Amount", "range", 0.65, 0.0, 1.0, 0.01),
            ParamSpec("curve", "Dim Curve", "range", 1.3, 0.2, 3.0, 0.05),
            ParamSpec("warm_color", "Warm Target", "color", [255, 115, 24]),
        ),
        apply=dim2warm,
    ),
    "inertia": FilterInfo(
        id="inertia",
        name="Inertia",
        description="Deterministic temporal smoothing using a look-back sample.",
        params=(
            ParamSpec("mix", "Inertia", "range", 0.65, 0.0, 1.0, 0.01),
            ParamSpec("tau_s", "Time Constant", "range", 0.35, 0.05, 3.0, 0.05),
        ),
        apply=inertia,
    ),
    "audio_mod": FilterInfo(
        id="audio_mod",
        name="Envelope Mod",
        description="Modulate brightness or warmth from the deterministic envelope.",
        params=(
            ParamSpec(
                "source",
                "Envelope",
                "select",
                "kick",
                options=(
                    ("pulse", "Pulse"),
                    ("kick", "Kick"),
                    ("snare", "Snare"),
                    ("hat", "Hat"),
                    ("energy", "Energy"),
                ),
            ),
            ParamSpec("depth", "Depth", "range", 0.45, 0.0, 1.0, 0.01),
            ParamSpec(
                "mode",
                "Target",
                "select",
                "brightness",
                options=(("brightness", "Brightness"), ("warmth", "Warmth")),
            ),
            ParamSpec("warm_color", "Warm Target", "color", [255, 125, 32]),
        ),
        apply=audio_mod,
    ),
}


def filter_catalog() -> list[dict[str, Any]]:
    return [FILTERS[key].as_dict() for key in sorted(FILTERS)]


def apply_filter_stack(
    rgb: np.ndarray,
    filters: list[FilterSpec],
    ctx: FilterContext,
) -> np.ndarray:
    """Apply enabled filters in declared order to a copy of ``rgb``."""
    out = np.asarray(rgb, dtype=np.float32).copy()
    for spec in filters:
        if not spec.enabled:
            continue
        info = FILTERS.get(spec.type)
        if info is None:
            raise KeyError(f"unknown filter '{spec.type}'")
        params = info.default_params()
        params.update(spec.params or {})
        out = info.apply(out, ctx, params)
        out = np.nan_to_num(out, nan=0.0, posinf=255.0, neginf=0.0)
        out = np.clip(out, 0.0, 255.0)
    return out.astype(np.float32)


__all__ = [
    "FILTERS",
    "FilterContext",
    "FilterInfo",
    "apply_filter_stack",
    "audio_mod",
    "beat_gate",
    "dim2warm",
    "filter_catalog",
    "inertia",
    "mirror_symmetry",
]
