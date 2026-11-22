"""Non-destructive filter stack for clip voxel buffers.

Filters run after a preset has rendered a clip and before the clip is blended
into its track.  They never mutate the incoming buffer.  ``inertia`` is a
deterministic look-back filter: the compositor supplies a reference buffer
built from earlier samples of the same stateless preset, so seeking to a
timestamp still yields a repeatable result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from hueme_studio.presets import ParamSpec
from hueme_studio.showdoc import FilterSpec, Timing, lerp_oklab, oklab_to_rgb, rgb_to_oklab

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


def _hash01(x: np.ndarray, y: np.ndarray, z: np.ndarray, seed: float) -> np.ndarray:
    value = np.sin(x * 127.1 + y * 311.7 + z * 74.7 + seed * 269.5) * 43758.5453123
    return value - np.floor(value)


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


def spatial_blur(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    """3D Gaussian blur using real metre distances between voxels."""
    radius = max(1e-3, _num(params, "radius_m", 0.45))
    mix = float(np.clip(_num(params, "mix", 1.0), 0.0, 1.0))
    if ctx.n <= 1 or mix <= 0.0:
        return rgb.astype(np.float32, copy=True)
    positions = ctx.positions.astype(np.float64)
    delta = positions[:, None, :] - positions[None, :, :]
    distances2 = np.sum(delta * delta, axis=2)
    weights = np.exp(-distances2 / (2.0 * radius * radius))
    weights /= np.maximum(np.sum(weights, axis=1, keepdims=True), 1e-9)
    blurred = weights @ rgb.astype(np.float64)
    out = (1.0 - mix) * rgb + mix * blurred
    return np.clip(out, 0.0, 255.0).astype(np.float32)


def bloom(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    """Additive 3D glow: bright voxels bleed softly onto their neighbours."""
    threshold = float(np.clip(_num(params, "threshold", 150.0), 0.0, 255.0))
    radius = max(1e-3, _num(params, "radius_m", 0.8))
    gain = max(0.0, _num(params, "gain", 0.8))
    mix = float(np.clip(_num(params, "mix", 1.0), 0.0, 1.0))
    if ctx.n <= 1 or mix <= 0.0 or gain <= 0.0:
        return rgb.astype(np.float32, copy=True)
    src = np.asarray(rgb, dtype=np.float64)
    peak = np.max(src, axis=1)
    weight = np.clip(
        (peak - threshold) / max(1e-6, 255.0 - threshold), 0.0, 1.0
    )
    positions = ctx.positions.astype(np.float64)
    delta = positions[:, None, :] - positions[None, :, :]
    distances2 = np.sum(delta * delta, axis=2)
    weights = np.exp(-distances2 / (2.0 * radius * radius))
    weights /= np.maximum(np.sum(weights, axis=1, keepdims=True), 1e-9)
    glow = weights @ (src * weight[:, None])
    return np.clip(src + glow * gain * mix, 0.0, 255.0).astype(np.float32)


def grain_noise(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    """Deterministic 3D film grain (monochrome or per-channel)."""
    amount = float(np.clip(_num(params, "amount", 0.18), 0.0, 1.0))
    scale = max(0.1, _num(params, "scale", 18.0))
    speed = _num(params, "speed", 2.0)
    seed = _num(params, "seed", 1.0)
    monochrome = bool(params.get("monochrome", True))
    if amount <= 0.0 or ctx.n == 0:
        return rgb.astype(np.float32, copy=True)
    p = ctx.normalized.astype(np.float64) * scale
    frame = math.floor(ctx.t * speed)
    blend = ctx.t * speed - frame
    h1 = _hash01(p[:, 0], p[:, 1], p[:, 2], seed + frame)
    h2 = _hash01(p[:, 0], p[:, 1], p[:, 2], seed + frame + 1.0)
    grain = (h1 + (h2 - h1) * blend) * 2.0 - 1.0
    if monochrome:
        factor = 1.0 + grain * amount
        return np.clip(rgb * factor[:, None], 0.0, 255.0).astype(np.float32)
    channels = []
    for index in range(3):
        h = _hash01(p[:, 0], p[:, 1], p[:, 2], seed + frame + index * 17.0)
        channels.append(h * 2.0 - 1.0)
    tint = np.stack(channels, axis=1)
    return np.clip(rgb + tint * amount * 255.0, 0.0, 255.0).astype(np.float32)


def color_grade(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    """Hue rotate, saturation and contrast in Oklab."""
    hue_shift = math.radians(_num(params, "hue_shift", 0.0))
    saturation = max(0.0, _num(params, "saturation", 1.0))
    contrast = max(0.0, _num(params, "contrast", 1.0))
    lift = _num(params, "lift", 0.0)
    if ctx.n == 0:
        return rgb.astype(np.float32, copy=True)
    lab = rgb_to_oklab(rgb).astype(np.float64)
    lightness = (lab[:, 0] - 0.5) * contrast + 0.5 + lift
    a = lab[:, 1] * saturation
    b = lab[:, 2] * saturation
    angle = np.arctan2(b, a) + hue_shift
    chroma = np.hypot(a, b)
    graded = np.stack(
        (lightness, chroma * np.cos(angle), chroma * np.sin(angle)), axis=1
    )
    return np.clip(oklab_to_rgb(graded), 0.0, 255.0).astype(np.float32)


def strobe_shutter(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    """A hard, deterministic shutter on the musical grid."""
    division = str(params.get("division", "1/8"))
    duty = float(np.clip(_num(params, "duty", 0.10), 0.01, 0.95))
    floor = float(np.clip(_num(params, "floor", 0.0), 0.0, 1.0))
    softness = float(np.clip(_num(params, "softness", 0.01), 0.0, 0.3))
    try:
        interval = ctx.timing.grid_interval(division)
    except ValueError:
        interval = ctx.timing.grid_interval("1/8")
    phase = ((ctx.t - ctx.timing.beat_offset_s) / interval) % 1.0
    if softness <= 1e-9:
        gate = 1.0 if phase <= duty else 0.0
    else:
        gate = float(
            1.0
            - _smoothstep(duty - softness, duty + softness, np.asarray(phase))
        )
    level = floor + (1.0 - floor) * gate
    return (rgb * float(level)).astype(np.float32)


def invert_color(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    mix = float(np.clip(_num(params, "mix", 1.0), 0.0, 1.0))
    inverted = 255.0 - rgb
    return ((1.0 - mix) * rgb + mix * inverted).astype(np.float32)


def saturation_control(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    """Scale chroma while preserving Oklab lightness."""
    amount = max(0.0, _num(params, "amount", 1.0))
    mix = float(np.clip(_num(params, "mix", 1.0), 0.0, 1.0))
    lab = rgb_to_oklab(rgb).astype(np.float64)
    lab[:, 1] *= amount
    lab[:, 2] *= amount
    adjusted = oklab_to_rgb(lab)
    return ((1.0 - mix) * rgb + mix * adjusted).astype(np.float32)


def brightness_curve(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    """Gain + gamma curve, applied in linear-looking sRGB space."""
    gain = max(0.0, _num(params, "gain", 1.0))
    gamma = max(0.05, _num(params, "gamma", 1.0))
    mix = float(np.clip(_num(params, "mix", 1.0), 0.0, 1.0))
    normalized = np.clip(rgb / 255.0, 0.0, 1.0)
    curved = np.power(normalized, gamma) * gain * 255.0
    return np.clip((1.0 - mix) * rgb + mix * curved, 0.0, 255.0).astype(np.float32)


def scanline(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    """Moving periodic shading bands along one room axis."""
    axis = {"x": 0, "y": 1, "z": 2}.get(str(params.get("axis", "y")), 1)
    frequency = max(0.5, _num(params, "frequency", 24.0))
    speed = _num(params, "speed", 0.25)
    depth = float(np.clip(_num(params, "depth", 0.55), 0.0, 1.0))
    floor = float(np.clip(_num(params, "floor", 0.12), 0.0, 1.0))
    phase = ctx.normalized[:, axis] * frequency - ctx.t * speed * frequency
    wave = 0.5 + 0.5 * np.sin(phase * math.tau)
    level = floor + (1.0 - floor) * (1.0 - depth + depth * wave)
    return (rgb * level[:, None]).astype(np.float32)


def posterize(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    levels = int(np.clip(round(_num(params, "levels", 6.0)), 2, 32))
    mix = float(np.clip(_num(params, "mix", 1.0), 0.0, 1.0))
    quantized = np.round(np.clip(rgb, 0.0, 255.0) / 255.0 * (levels - 1)) / (
        levels - 1
    ) * 255.0
    return ((1.0 - mix) * rgb + mix * quantized).astype(np.float32)


def hue_rotate(
    rgb: np.ndarray, ctx: FilterContext, params: dict[str, Any]
) -> np.ndarray:
    mix = float(np.clip(_num(params, "mix", 1.0), 0.0, 1.0))
    rotated = color_grade(
        rgb,
        ctx,
        {
            "hue_shift": _num(params, "degrees", 45.0),
            "saturation": 1.0,
            "contrast": 1.0,
            "lift": 0.0,
        },
    )
    return ((1.0 - mix) * rgb + mix * rotated).astype(np.float32)


FILTERS: dict[str, FilterInfo] = {
    "spatial_blur": FilterInfo(
        id="spatial_blur",
        name="3D Spatial Blur",
        description="Gaussian blur across real metre distances between voxels.",
        params=(
            ParamSpec("radius_m", "Radius (m)", "range", 0.45, 0.02, 3.0, 0.01),
            ParamSpec("mix", "Amount", "range", 1.0, 0.0, 1.0, 0.01),
        ),
        apply=spatial_blur,
    ),
    "bloom": FilterInfo(
        id="bloom",
        name="3D Bloom",
        description="Additive glow from bright voxels across real metre distances.",
        params=(
            ParamSpec("threshold", "Threshold", "range", 150.0, 0.0, 255.0, 1.0),
            ParamSpec("radius_m", "Radius (m)", "range", 0.8, 0.05, 4.0, 0.01),
            ParamSpec("gain", "Gain", "range", 0.8, 0.0, 2.0, 0.01),
            ParamSpec("mix", "Amount", "range", 1.0, 0.0, 1.0, 0.01),
        ),
        apply=bloom,
    ),
    "grain_noise": FilterInfo(
        id="grain_noise",
        name="3D Grain",
        description="Deterministic 3D film grain keyed to voxel position and time.",
        params=(
            ParamSpec("amount", "Amount", "range", 0.18, 0.0, 1.0, 0.01),
            ParamSpec("scale", "Grain Scale", "range", 18.0, 0.1, 120.0, 0.1),
            ParamSpec("speed", "Speed", "range", 2.0, 0.0, 24.0, 0.1),
            ParamSpec("seed", "Seed", "range", 1.0, 0.0, 100.0, 1.0),
            ParamSpec("monochrome", "Monochrome", "bool", True),
        ),
        apply=grain_noise,
    ),
    "color_grade": FilterInfo(
        id="color_grade",
        name="Colour Grade",
        description="Hue rotate, saturation and contrast in Oklab.",
        params=(
            ParamSpec("hue_shift", "Hue Shift", "range", 0.0, -180.0, 180.0, 1.0),
            ParamSpec("saturation", "Saturation", "range", 1.0, 0.0, 3.0, 0.01),
            ParamSpec("contrast", "Contrast", "range", 1.0, 0.0, 3.0, 0.01),
            ParamSpec("lift", "Lift", "range", 0.0, -0.5, 0.5, 0.01),
        ),
        apply=color_grade,
    ),
    "strobe_shutter": FilterInfo(
        id="strobe_shutter",
        name="Strobe Shutter",
        description="Hard musical shutter independent of incoming audio.",
        params=(
            ParamSpec(
                "division",
                "Grid",
                "select",
                "1/8",
                options=(
                    ("1/1", "Bar"),
                    ("1/2", "Half"),
                    ("1/4", "Quarter"),
                    ("1/8", "Eighth"),
                    ("1/16", "Sixteenth"),
                ),
            ),
            ParamSpec("duty", "Duty", "range", 0.10, 0.01, 0.95, 0.01),
            ParamSpec("softness", "Edge", "range", 0.01, 0.0, 0.3, 0.005),
            ParamSpec("floor", "Closed Level", "range", 0.0, 0.0, 1.0, 0.01),
        ),
        apply=strobe_shutter,
    ),
    "invert_color": FilterInfo(
        id="invert_color",
        name="Invert Colour",
        description="Invert the RGB residual while preserving luminance structure.",
        params=(ParamSpec("mix", "Amount", "range", 1.0, 0.0, 1.0, 0.01),),
        apply=invert_color,
    ),
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
    "hue_rotate": FilterInfo(
        id="hue_rotate",
        name="Hue Rotate",
        description="Rotate hue while preserving lightness and chroma.",
        params=(
            ParamSpec("degrees", "Degrees", "range", 45.0, -180.0, 180.0, 1.0),
            ParamSpec("mix", "Amount", "range", 1.0, 0.0, 1.0, 0.01),
        ),
        apply=hue_rotate,
    ),
    "saturation": FilterInfo(
        id="saturation",
        name="Saturation",
        description="Boost or mute colour intensity without shifting hue.",
        params=(
            ParamSpec("amount", "Saturation", "range", 1.0, 0.0, 3.0, 0.01),
            ParamSpec("mix", "Amount", "range", 1.0, 0.0, 1.0, 0.01),
        ),
        apply=saturation_control,
    ),
    "brightness_curve": FilterInfo(
        id="brightness_curve",
        name="Brightness Curve",
        description="Gain and gamma curve for gentle dimming or punchy lifts.",
        params=(
            ParamSpec("gain", "Gain", "range", 1.0, 0.0, 3.0, 0.01),
            ParamSpec("gamma", "Gamma", "range", 1.0, 0.2, 3.0, 0.01),
            ParamSpec("mix", "Amount", "range", 1.0, 0.0, 1.0, 0.01),
        ),
        apply=brightness_curve,
    ),
    "scanline": FilterInfo(
        id="scanline",
        name="Scanline Bands",
        description="Moving periodic bands across the room for texture.",
        params=(
            ParamSpec("axis", "Axis", "select", "y", options=(("x", "X"), ("y", "Y"), ("z", "Z"))),
            ParamSpec("frequency", "Frequency", "range", 24.0, 0.5, 120.0, 0.5),
            ParamSpec("speed", "Speed", "range", 0.25, -4.0, 4.0, 0.01),
            ParamSpec("depth", "Depth", "range", 0.55, 0.0, 1.0, 0.01),
            ParamSpec("floor", "Floor", "range", 0.12, 0.0, 1.0, 0.01),
        ),
        apply=scanline,
    ),
    "posterize": FilterInfo(
        id="posterize",
        name="Posterize",
        description="Reduce colour steps for a printed, graphic look.",
        params=(
            ParamSpec("levels", "Levels", "range", 6.0, 2.0, 32.0, 1.0),
            ParamSpec("mix", "Amount", "range", 1.0, 0.0, 1.0, 0.01),
        ),
        apply=posterize,
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
    "bloom",
    "color_grade",
    "dim2warm",
    "filter_catalog",
    "grain_noise",
    "inertia",
    "invert_color",
    "mirror_symmetry",
    "spatial_blur",
    "strobe_shutter",
]
