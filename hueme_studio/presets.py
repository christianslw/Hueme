"""Stateless 3D voxel presets.

Every preset is a pure function of world geometry, show time, beat/envelope
context, and its own parameters.  No frame-to-frame state is kept.  Presets
return an ``(N, 3)`` float32 sRGB buffer in 0..255 for every rig voxel; the
compositor applies the clip's target group and spatial mask afterwards.

The five legacy architectural shaders are ported here as
``turrell_horizon``, ``caustic_abyss``, ``celestial_orrery``,
``blackbody_forge`` and ``ma3_wings``.  ``solid`` is the sixth core preset and
the base for washes, blackouts and tests.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Literal

import numpy as np

from hueme_studio.showdoc import lerp_oklab

ParamType = Literal["color", "range", "select", "bool", "number"]


@dataclass(frozen=True, slots=True)
class ParamSpec:
    key: str
    label: str
    type: ParamType
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "default": copy.deepcopy(self.default),
        }
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        if self.step is not None:
            out["step"] = self.step
        if self.options:
            out["options"] = [list(opt) for opt in self.options]
        return out


def _color(key: str, label: str, default: list[int]) -> ParamSpec:
    return ParamSpec(key, label, "color", default)


def _range(
    key: str,
    label: str,
    default: float,
    lo: float,
    hi: float,
    step: float,
) -> ParamSpec:
    return ParamSpec(key, label, "range", float(default), lo, hi, step)


class PresetUtils:
    """Small vector-safe maths helpers.  Kept separate for testability."""

    palettes = {
        "cyberpunk": [(255, 0, 85), (0, 240, 255), (180, 0, 255)],
        "fire_ice": [(255, 40, 0), (255, 160, 0), (0, 180, 255)],
        "toxic": [(0, 255, 60), (200, 255, 0), (140, 0, 255)],
        "sunset": [(255, 20, 60), (255, 110, 0), (255, 210, 80)],
        "rave": [(255, 0, 0), (0, 255, 0), (0, 80, 255)],
        "warm_gold": [(255, 90, 0), (255, 180, 60), (255, 240, 180)],
    }

    cosine_palettes = {
        "ember": (
            (0.55, 0.18, 0.05),
            (0.40, 0.25, 0.05),
            (1.0, 1.0, 1.0),
            (0.00, 0.10, 0.15),
        ),
        "synth": (
            (0.50, 0.12, 0.50),
            (0.50, 0.10, 0.50),
            (1.0, 1.0, 1.0),
            (0.00, 0.25, 0.50),
        ),
        "sunset": (
            (0.60, 0.20, 0.12),
            (0.35, 0.18, 0.10),
            (1.0, 1.0, 1.0),
            (0.00, 0.05, 0.10),
        ),
        "moonlight": (
            (0.30, 0.40, 0.55),
            (0.25, 0.25, 0.30),
            (1.0, 1.0, 1.0),
            (0.10, 0.20, 0.30),
        ),
        "acid": (
            (0.35, 0.50, 0.15),
            (0.30, 0.40, 0.15),
            (1.0, 1.0, 1.0),
            (0.00, 0.10, 0.20),
        ),
        "ice": (
            (0.35, 0.55, 0.70),
            (0.20, 0.25, 0.25),
            (1.0, 1.0, 1.0),
            (0.00, 0.10, 0.20),
        ),
    }
    gradient_aliases = {
        "cyberpunk": "synth",
        "fire_ice": "ember",
        "rave": "synth",
        "sunset": "sunset",
        "warm_gold": "ember",
        "toxic": "acid",
        "rainbow": "synth",
    }

    @staticmethod
    def clamp(value: float | np.ndarray, lo: float = 0.0, hi: float = 1.0) -> Any:
        return np.clip(value, lo, hi)

    @staticmethod
    def smoothstep(edge0: float, edge1: float, x: np.ndarray | float) -> np.ndarray:
        if abs(edge1 - edge0) < 1e-12:
            return np.where(np.asarray(x) >= edge1, 1.0, 0.0)
        t = np.clip((np.asarray(x) - edge0) / (edge1 - edge0), 0.0, 1.0)
        return t * t * (3.0 - 2.0 * t)

    @staticmethod
    def lerp(
        c1: np.ndarray | list[float],
        c2: np.ndarray | list[float],
        t: np.ndarray | float,
    ) -> np.ndarray:
        # Oklab keeps architectural colour fades from passing through grey.
        return np.asarray(lerp_oklab(c1, c2, t), dtype=np.float64)

    @staticmethod
    def scale(c: np.ndarray | list[float], factor: float | np.ndarray) -> np.ndarray:
        colour = np.asarray(c, dtype=np.float64)
        f = np.asarray(factor, dtype=np.float64)
        if f.ndim > 0 and f.shape[-1] != colour.shape[-1]:
            f = f[..., None]
        return colour * f

    @staticmethod
    def add(c1: np.ndarray | list[float], c2: np.ndarray | list[float]) -> np.ndarray:
        return np.asarray(c1, dtype=np.float64) + np.asarray(c2, dtype=np.float64)

    @classmethod
    def gradient(cls, name: str, t: np.ndarray | float) -> np.ndarray:
        key = str(name)
        if key not in cls.cosine_palettes:
            key = cls.gradient_aliases.get(key, "synth")
        a, b, c, d = cls.cosine_palettes[key]
        tt = np.asarray(t, dtype=np.float64)
        rr = np.asarray(a) + np.asarray(b) * np.cos(
            2.0 * np.pi * (np.asarray(c) * tt[..., None] + np.asarray(d))
        )
        return np.clip(rr * 255.0, 0.0, 255.0)

    @classmethod
    def palette(cls, name: str) -> list[tuple[int, int, int]]:
        return cls.palettes.get(str(name), cls.palettes["cyberpunk"])

    @staticmethod
    def axis_positions(positions: np.ndarray) -> np.ndarray:
        pos = np.asarray(positions, dtype=np.float64)
        if pos.size == 0:
            return np.zeros((0,), dtype=np.float64)
        spans = np.ptp(pos, axis=0)
        axis = int(np.argmax(spans)) if float(np.max(spans)) > 1e-9 else 0
        col = pos[:, axis]
        lo, hi = float(np.min(col)), float(np.max(col))
        return (col - lo) / max(1e-9, hi - lo)


U = PresetUtils


@dataclass(slots=True)
class PresetContext:
    """Inputs shared by every stateless preset."""

    positions: np.ndarray
    normalized: np.ndarray
    scene_min: np.ndarray
    scene_max: np.ndarray
    room_size: np.ndarray
    t: float
    local_t: float
    beat: dict[str, float | int | bool]
    env: dict[str, float]
    params: dict[str, Any]
    target_indices: np.ndarray
    target_rank: np.ndarray
    target_size: int
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.positions.shape[0])


def _color_param(ctx: PresetContext, key: str, default: list[float]) -> np.ndarray:
    value = ctx.params.get(key, default)
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        value = default
    return np.asarray(
        [float(value[0]), float(value[1]), float(value[2])], dtype=np.float64
    )


def _num(ctx: PresetContext, key: str, default: float) -> float:
    try:
        return float(ctx.params.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _bool(ctx: PresetContext, key: str, default: bool) -> bool:
    value = ctx.params.get(key, default)
    return bool(value)


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
def solid(ctx: PresetContext) -> np.ndarray:
    colour = _color_param(ctx, "color", [255, 110, 20])
    level = float(np.clip(_num(ctx, "brightness", 1.0), 0.0, 1.0))
    return np.broadcast_to(colour * level, (ctx.n, 3)).astype(np.float32)


def turrell_horizon(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    master = _num(ctx, "brightness", 0.90)
    speed = _num(ctx, "drift_speed", 0.14)
    angle = math.radians(_num(ctx, "angle", 60.0))
    seam_w = max(0.05, _num(ctx, "seam_width", 0.24))
    contrast = _num(ctx, "contrast", 0.55)
    audio_mod = _num(ctx, "audio_mod", 0.20)

    c_sky = _color_param(ctx, "sky_color", [22, 12, 65])
    c_seam = _color_param(ctx, "seam_color", [255, 115, 25])
    c_earth = _color_param(ctx, "earth_color", [140, 20, 45])

    pulse = float(ctx.env.get("pulse", 0.0)) * audio_mod
    harmonic = float(ctx.env.get("harmonic", pulse)) * audio_mod

    dx = math.cos(angle)
    dy = math.sin(angle)
    phase = ctx.t * speed
    horizon = 0.52 * math.sin(phase) + 0.15 * math.sin(phase * 0.37) + 0.12 * harmonic
    seam_eff = seam_w * (1.0 + 0.35 * pulse)

    proj = p[:, 0] * dx + p[:, 1] * dy + p[:, 2] * 0.25
    dist = proj - horizon
    field = U.smoothstep(-seam_eff * 1.6, seam_eff * 1.6, dist)
    base = U.lerp(c_earth, c_sky, field)
    seam = np.exp(-((dist / max(1e-6, seam_eff)) ** 2))
    colour = U.lerp(base, c_seam, seam**0.85)
    base_bri = (1.0 - contrast * 0.78) + (contrast * 0.78) * seam
    bri = np.clip(base_bri + 0.15 * pulse * seam, 0.0, 1.0)
    return np.clip(colour * (bri * master)[:, None], 0.0, 255.0).astype(np.float32)


def caustic_abyss(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    master = _num(ctx, "brightness", 0.95)
    speed = _num(ctx, "speed", 0.28)
    spatial_k = _num(ctx, "scale", 1.6) * math.pi
    focus = max(0.2, _num(ctx, "focus", 2.8))
    floor = max(0.0, _num(ctx, "shadow_floor", 0.06))
    audio_mod = _num(ctx, "audio_mod", 0.25)
    palette = str(ctx.params.get("palette", "moonlight"))
    lens = _color_param(ctx, "caustic_tint", [255, 225, 175])

    pulse = float(ctx.env.get("pulse", 0.0)) * audio_mod
    kick = float(ctx.env.get("kick", 0.0)) * audio_mod

    t = ctx.t * speed
    nx, ny, nz = p[:, 0], p[:, 1], p[:, 2]
    w1 = np.sin(nx * spatial_k * 0.9 + ny * spatial_k * 1.1 - t)
    w2 = np.sin(nx * spatial_k * 1.4 - ny * spatial_k * 0.7 + t * 0.618 + 1.7)
    w3 = np.cos((nx - ny + nz * 0.5) * spatial_k * 0.85 - t * 0.382 + 3.1)
    raw = (w1 + w2 + w3 + 3.0) / 6.0
    caustic = raw**focus
    crest = kick * 0.35 * (raw**2.0)

    base = U.gradient(palette, 0.15 + raw * 0.55 + t * 0.04)
    lens_mix = np.clip((caustic**1.35) * 0.85, 0.0, 1.0)
    colour = U.lerp(base, lens, lens_mix)
    value = floor + (1.0 - floor) * np.clip(caustic + crest + pulse * 0.15, 0.0, 1.0)
    return np.clip(colour * (value * master)[:, None], 0.0, 255.0).astype(np.float32)


def celestial_orrery(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    master = _num(ctx, "brightness", 1.0)
    speed = _num(ctx, "orbit_speed", 0.24)
    aura = max(0.08, _num(ctx, "radius", 0.28))
    ratio = _num(ctx, "orbit_ratio", 1.6)
    audio_mod = _num(ctx, "audio_mod", 0.25)

    c_sun = _color_param(ctx, "sun_color", [255, 100, 15])
    c_moon = _color_param(ctx, "moon_color", [0, 165, 255])
    c_eclipse = _color_param(ctx, "eclipse_color", [255, 240, 210])

    pulse = float(ctx.env.get("pulse", 0.0)) * audio_mod
    kick = float(ctx.env.get("kick", 0.0)) * audio_mod

    t1 = ctx.t * speed
    t2 = -ctx.t * speed * ratio + 1.2
    sun_x = 0.5 + 0.42 * math.cos(t1)
    sun_y = 0.5 + 0.45 * math.sin(t1)
    moon_x = 0.5 + 0.44 * math.cos(t2)
    moon_y = 0.5 + 0.42 * math.sin(t2 * 0.85)

    r_sun = max(0.05, aura * (1.0 + 0.45 * pulse + 0.35 * kick))
    r_moon = max(0.05, aura * 0.85 * (1.0 + 0.30 * pulse))
    d_sun = np.hypot(p[:, 0] - sun_x, p[:, 1] - sun_y)
    d_moon = np.hypot(p[:, 0] - moon_x, p[:, 1] - moon_y)
    i_sun = 1.0 / (1.0 + (d_sun / r_sun) ** 2.6)
    i_moon = 1.0 / (1.0 + (d_moon / r_moon) ** 2.6)
    overlap = (i_sun * i_moon) ** 1.2

    colour = U.add(U.scale(c_sun, i_sun), U.scale(c_moon, i_moon))
    mix = np.clip(overlap * 1.4, 0.0, 1.0)
    colour = U.lerp(colour, c_eclipse, np.where(overlap > 0.08, mix, 0.0))
    return np.clip(colour * master, 0.0, 255.0).astype(np.float32)


def _thermal_rgb(
    temp: np.ndarray, k_min: float, k_max: float, indigo: bool
) -> np.ndarray:
    t = np.clip(temp, 0.0, 1.0)
    kelvin = k_min + (k_max - k_min) * (t**1.15)
    k = kelvin / 100.0
    r = np.full_like(t, 255.0)
    warm = np.clip(48.0 + (k - 15.0) * 18.5, 28.0, 255.0)
    cool = np.clip(99.4708 * np.log(np.maximum(1.0, k)) - 161.1195, 0.0, 255.0)
    g = np.where(k <= 19.0, warm, cool)
    b = np.where(
        k <= 19.0,
        0.0,
        np.clip(138.5177 * np.log(np.maximum(1.0, k - 10.0)) - 305.0448, 0.0, 255.0),
    )
    if indigo:
        shadow = np.clip((0.22 - t) / 0.22, 0.0, 1.0)
        r = r * (1.0 - 0.45 * shadow) + 35.0 * shadow
        g = g * (1.0 - 0.55 * shadow) + 18.0 * shadow
        b = b + 85.0 * (shadow**1.5)
    return np.clip(np.stack((r, g, b), axis=-1), 0.0, 255.0)


def blackbody_forge(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    k_min = _num(ctx, "base_kelvin", 1850.0)
    k_max = max(k_min + 400.0, _num(ctx, "peak_kelvin", 3800.0))
    master = _num(ctx, "brightness", 1.0)
    diffusion = np.clip(_num(ctx, "diffusion", 0.60), 0.0, 1.0)
    cooling = max(0.1, _num(ctx, "cooling", 1.3))
    indigo = _bool(ctx, "indigo_shadow", True)
    axis_name = str(ctx.params.get("axis", "x"))
    axis = {"x": 0, "y": 1, "z": 2}.get(axis_name, 0)

    coords = p[:, axis]
    phase = float(ctx.beat.get("beat_phase", 0.0))
    bar_phase = float(ctx.beat.get("bar_phase", 0.0))
    beat_count = int(ctx.beat.get("beat_count", 0))
    kick = float(ctx.env.get("kick", 0.0))
    snare = float(ctx.env.get("snare", 0.0))
    hat = float(ctx.env.get("hat", 0.0))
    pulse = float(ctx.env.get("pulse", 0.0))
    energy = float(ctx.env.get("energy", 0.0))

    focal = 0.5 + 0.35 * math.sin(2.0 * math.pi * bar_phase)
    swell = math.exp(-phase * 3.2)
    inject = (
        (0.45 + 0.55 * kick)
        * np.exp(-(((coords - focal) / 0.24) ** 2))
        * (0.45 + 0.55 * swell)
    )
    ambient = 0.08 + 0.26 * pulse + 0.10 * energy
    edge = np.abs(coords - 0.5) * 2.0
    crackle = np.where(((np.arange(ctx.n) + beat_count) % 3) == 0, hat * 0.16, 0.0)
    temps = np.clip(ambient + inject + snare * edge * 0.62 + crackle, 0.0, 1.0)

    # Stateless equivalents of the legacy heat equation: diffuse along the
    # chosen axis, then apply a fixed radiative loss. No frame state is stored.
    if diffusion > 1e-6 and ctx.n > 2:
        order = np.argsort(coords)
        ordered = temps[order]
        padded = np.pad(ordered, (1, 1), mode="edge")
        neighbours = 0.5 * (padded[:-2] + padded[2:])
        ordered = (1.0 - diffusion) * ordered + diffusion * neighbours
        temps[order] = np.clip(ordered, 0.0, 1.0)
    temps = np.clip(temps * math.exp(-0.12 * (cooling - 1.0)), 0.0, 1.0)
    colour = _thermal_rgb(temps, k_min, k_max, indigo)
    value = (0.06 + 0.94 * temps**1.35) * master
    return np.clip(colour * value[:, None], 0.0, 255.0).astype(np.float32)


def ma3_wings(ctx: PresetContext) -> np.ndarray:
    n = int(ctx.target_size)
    out = np.zeros((ctx.n, 3), dtype=np.float64)
    if n <= 0 or ctx.target_indices.size == 0:
        return out.astype(np.float32)

    master = _num(ctx, "brightness", 1.0)
    gate = np.clip(_num(ctx, "gate_time", 0.55), 0.05, 0.99)
    bg_level = np.clip(_num(ctx, "bg_level", 0.04), 0.0, 0.5)
    style = str(ctx.params.get("wing_style", "auto_phrase"))
    c_beam = _color_param(ctx, "color", [255, 45, 0])
    c_rim = _color_param(ctx, "color2", [0, 210, 255])
    c_bg = _color_param(ctx, "bg_color", [12, 6, 28])

    beat_count = int(ctx.beat.get("beat_count", 0))
    phase = float(ctx.beat.get("beat_phase", 0.0))
    bar_pos = int(ctx.beat.get("bar_pos", 0))
    phrase = (beat_count // 8) % 4

    center = (n - 1) / 2.0
    n_rings = max(1, int(math.ceil(center)) + 1)
    span = max(1, 2 * n_rings - 2)
    wing_pos = (beat_count // 4) % span
    if wing_pos >= n_rings:
        wing_pos = span - wing_pos

    active = style
    if style == "auto_phrase":
        active = ("inside_out", "outside_in", "bounce_wing", "frame_lock")[phrase]
    if active == "inside_out":
        active_ring = bar_pos % n_rings
    elif active == "outside_in":
        active_ring = (n_rings - 1) - (bar_pos % n_rings)
    elif active == "bounce_wing":
        active_ring = wing_pos
    else:
        active_ring = 0 if bar_pos % 2 == 0 else n_rings - 1

    if phase < gate:
        t_g = phase / max(0.05, gate)
        env_gate = 1.0 if t_g < 0.35 else max(0.0, 1.0 - (t_g - 0.35) / 0.65) ** 1.5
    else:
        env_gate = 0.0

    snare_hold = max(0.0, min(1.0, float(ctx.env.get("snare", 0.0))))
    bg = U.scale(c_bg, bg_level * master)
    valid = ctx.target_rank >= 0
    ranks = ctx.target_rank[valid]

    for rank, voxel in zip(ranks, ctx.target_indices):
        rank = int(rank)
        ring = int(round(abs(rank - center)))
        is_edge = rank == 0 or rank == n - 1
        if is_edge and snare_hold > 0.25 and ring != active_ring:
            colour = U.lerp(c_bg, c_rim, snare_hold)
            level = bg_level + (1.0 - bg_level) * snare_hold
            out[voxel] = U.scale(colour, level * master)
            continue
        if ring == active_ring:
            use_rim = bar_pos == 3 or (active == "frame_lock" and ring == n_rings - 1)
            colour = c_rim if use_rim else c_beam
            level = bg_level + (1.0 - bg_level) * max(0.18, env_gate)
            out[voxel] = U.scale(colour, level * master)
        else:
            out[voxel] = bg
    return np.clip(out, 0.0, 255.0).astype(np.float32)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Preset:
    id: str
    name: str
    description: str
    params: tuple[ParamSpec, ...]
    render: Callable[[PresetContext], np.ndarray]

    def default_params(self) -> dict[str, Any]:
        return {p.key: copy.deepcopy(p.default) for p in self.params}

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "params": [p.as_dict() for p in self.params],
        }


PRESETS: dict[str, Preset] = {
    "solid": Preset(
        id="solid",
        name="Solid Field",
        description="Flat colour wash for bases, blackouts and cue accents.",
        params=(
            _color("color", "Colour", [255, 110, 20]),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=solid,
    ),
    "turrell_horizon": Preset(
        id="turrell_horizon",
        name="Turrell Horizon",
        description="Architectural colour field with a luminous 3D horizon seam.",
        params=(
            _color("sky_color", "Upper Field", [22, 12, 65]),
            _color("seam_color", "Horizon Seam", [255, 115, 25]),
            _color("earth_color", "Lower Field", [140, 20, 45]),
            _range("brightness", "Master Brightness", 0.90, 0.05, 1.0, 0.01),
            _range("drift_speed", "Horizon Drift", 0.14, 0.02, 0.8, 0.01),
            _range("angle", "Spatial Axis", 60.0, 0.0, 360.0, 1.0),
            _range("seam_width", "Seam Width", 0.24, 0.08, 0.60, 0.01),
            _range("contrast", "Depth Contrast", 0.55, 0.0, 0.9, 0.01),
            _range("audio_mod", "Audio Breath", 0.20, 0.0, 1.0, 0.01),
        ),
        render=turrell_horizon,
    ),
    "caustic_abyss": Preset(
        id="caustic_abyss",
        name="Caustic Abyss",
        description="Three golden-ratio interference waves form optical caustic lenses.",
        params=(
            ParamSpec(
                "palette",
                "Colour Atmosphere",
                "select",
                "moonlight",
                options=(
                    ("moonlight", "Bioluminescent Reef"),
                    ("ember", "Molten Amber"),
                    ("synth", "Nebula Silk"),
                    ("sunset", "Golden Hour"),
                    ("ice", "Glacier Cave"),
                    ("acid", "Emerald Canopy"),
                ),
            ),
            _color("caustic_tint", "Lens Highlight", [255, 225, 175]),
            _range("brightness", "Master Brightness", 0.95, 0.05, 1.0, 0.01),
            _range("speed", "Current Speed", 0.28, 0.05, 1.2, 0.01),
            _range("scale", "Wave Density", 1.6, 0.5, 3.5, 0.01),
            _range("focus", "Lens Sharpness", 2.8, 1.2, 5.0, 0.01),
            _range("shadow_floor", "Deep Water Floor", 0.06, 0.0, 0.35, 0.01),
            _range("audio_mod", "Audio Ripple", 0.25, 0.0, 1.0, 0.01),
        ),
        render=caustic_abyss,
    ),
    "celestial_orrery": Preset(
        id="celestial_orrery",
        name="Celestial Orrery",
        description="Two luminous bodies orbit the loft and flare as they eclipse.",
        params=(
            _color("sun_color", "Primary Body", [255, 100, 15]),
            _color("moon_color", "Secondary Body", [0, 165, 255]),
            _color("eclipse_color", "Eclipse Flare", [255, 240, 210]),
            _range("brightness", "Master Brightness", 1.0, 0.05, 1.0, 0.01),
            _range("orbit_speed", "Orbital Speed", 0.24, 0.04, 1.2, 0.01),
            _range("radius", "Body Aura", 0.28, 0.12, 0.65, 0.01),
            _range("orbit_ratio", "Moon/Sun Ratio", 1.6, 0.5, 2.5, 0.01),
            _range("audio_mod", "Audio Gravity", 0.25, 0.0, 1.0, 0.01),
        ),
        render=celestial_orrery,
    ),
    "blackbody_forge": Preset(
        id="blackbody_forge",
        name="Blackbody Forge",
        description="Deterministic thermal ember field from 1800K amber to incandescent gold.",
        params=(
            _range("base_kelvin", "Idle Ember", 1850.0, 1600.0, 2600.0, 10.0),
            _range("peak_kelvin", "Peak Flash", 3800.0, 2800.0, 5500.0, 50.0),
            _range("brightness", "Master Brightness", 1.0, 0.05, 1.0, 0.01),
            _range("diffusion", "Heat Conduction", 0.60, 0.1, 1.0, 0.01),
            _range("cooling", "Radiative Cooling", 1.3, 0.5, 3.0, 0.01),
            ParamSpec("indigo_shadow", "Indigo Shadows", "bool", True),
            ParamSpec(
                "axis",
                "Heat Axis",
                "select",
                "x",
                options=(("x", "X / Width"), ("y", "Y / Depth"), ("z", "Z / Height")),
            ),
        ),
        render=blackbody_forge,
    ),
    "ma3_wings": Preset(
        id="ma3_wings",
        name="MA3 Wings",
        description="Beat-locked symmetric wing stepping across the target group.",
        params=(
            ParamSpec(
                "wing_style",
                "Wing Mode",
                "select",
                "auto_phrase",
                options=(
                    ("auto_phrase", "Auto Phrase"),
                    ("inside_out", "Inside Out"),
                    ("outside_in", "Outside In"),
                    ("bounce_wing", "Symmetrical Bounce"),
                    ("frame_lock", "Wall Frame / Core"),
                ),
            ),
            _color("color", "Primary Beam", [255, 45, 0]),
            _color("color2", "Rim / Snare", [0, 210, 255]),
            _color("bg_color", "Floor Tint", [12, 6, 28]),
            _range("brightness", "Master Brightness", 1.0, 0.1, 1.0, 0.01),
            _range("gate_time", "Beam Gate", 0.55, 0.15, 0.95, 0.01),
            _range("bg_level", "Ambient Floor", 0.04, 0.0, 0.25, 0.01),
        ),
        render=ma3_wings,
    ),
}


def get_preset(preset_id: str) -> Preset:
    canonical = PRESET_ALIASES.get(preset_id, preset_id)
    try:
        return PRESETS[canonical]
    except KeyError as exc:
        available = ", ".join(sorted(PRESETS))
        raise KeyError(
            f"unknown preset '{preset_id}' (available: {available})"
        ) from exc


def default_params(preset_id: str) -> dict[str, Any]:
    return get_preset(preset_id).default_params()


def preset_catalog() -> list[dict[str, Any]]:
    return [PRESETS[key].as_dict() for key in sorted(PRESETS)]


# Legacy effect ids are accepted on import; the canonical registry stays at six
# Studio presets so the UI and MCP catalog are not duplicated.
PRESET_ALIASES = {
    "turrell_skyspace": "turrell_horizon",
    "ma3_matrix_wing": "ma3_wings",
}


def render_preset(preset_id: str, ctx: PresetContext) -> np.ndarray:
    preset = get_preset(preset_id)
    params = preset.default_params()
    params.update(copy.deepcopy(ctx.params or {}))
    local_ctx = replace(ctx, params=params)
    out = preset.render(local_ctx)
    arr = np.asarray(out, dtype=np.float32)
    if arr.shape != (ctx.n, 3):
        raise ValueError(
            f"preset '{preset_id}' returned {arr.shape}, expected {(ctx.n, 3)}"
        )
    arr = np.nan_to_num(arr, nan=0.0, posinf=255.0, neginf=0.0)
    return np.clip(arr, 0.0, 255.0, out=arr)


__all__ = [
    "PRESET_ALIASES",
    "PRESETS",
    "ParamSpec",
    "Preset",
    "PresetContext",
    "PresetUtils",
    "default_params",
    "get_preset",
    "preset_catalog",
    "render_preset",
]
