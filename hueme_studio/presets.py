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

from hueme_studio.palettes import (
    BUILTIN_COSINE,
    custom_palette_colors,
    palette_gradient,
)
from hueme_studio.showdoc import lerp_oklab

ParamType = Literal["color", "palette", "vec3", "range", "select", "bool", "number"]


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


def _palette(key: str, label: str, default: str) -> ParamSpec:
    return ParamSpec(key, label, "palette", default)


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

    cosine_palettes = BUILTIN_COSINE

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
        custom = custom_palette_colors(key)
        if custom is not None:
            return palette_gradient(custom, t)
        if key not in cls.cosine_palettes:
            key = cls.gradient_aliases.get(key, "synth")
        a, b, c, d = cls.cosine_palettes[key]
        tt = np.asarray(t, dtype=np.float64)
        rr = np.asarray(a) + np.asarray(b) * np.cos(
            2.0 * np.pi * (np.asarray(c) * tt[..., None] + np.asarray(d))
        )
        return np.clip(rr * 255.0, 0.0, 255.0)

    @staticmethod
    def sine01(
        t: float | np.ndarray,
        hz: float = 1.0,
        phase: float | np.ndarray = 0.0,
    ) -> np.ndarray:
        """Vector-safe 0..1 sine LFO for band-limited, frame-rate safe motion."""
        return 0.5 + 0.5 * np.sin(
            np.asarray(t, dtype=np.float64) * float(hz) * math.tau
            + np.asarray(phase, dtype=np.float64)
        )

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
# Phase 3 generators: all stateless, voxel-3D, audio optional
# ---------------------------------------------------------------------------
def _fract(value: np.ndarray | float) -> np.ndarray:
    arr = np.asarray(value, dtype=np.float64)
    return arr - np.floor(arr)


def _hash01(
    x: np.ndarray | float,
    y: np.ndarray | float,
    z: np.ndarray | float,
    seed: float = 0.0,
) -> np.ndarray:
    value = np.sin(
        np.asarray(x) * 127.1
        + np.asarray(y) * 311.7
        + np.asarray(z) * 74.7
        + float(seed) * 269.5
    ) * 43758.5453123
    return _fract(value)


def _value_channel(
    px: np.ndarray, py: np.ndarray, pz: np.ndarray, seed: float
) -> np.ndarray:
    ix = np.floor(px).astype(np.int64)
    iy = np.floor(py).astype(np.int64)
    iz = np.floor(pz).astype(np.int64)
    fx, fy, fz = px - ix, py - iy, pz - iz
    ux = fx * fx * (3.0 - 2.0 * fx)
    uy = fy * fy * (3.0 - 2.0 * fy)
    uz = fz * fz * (3.0 - 2.0 * fz)
    c000 = _hash01(ix, iy, iz, seed)
    c100 = _hash01(ix + 1, iy, iz, seed)
    c010 = _hash01(ix, iy + 1, iz, seed)
    c110 = _hash01(ix + 1, iy + 1, iz, seed)
    c001 = _hash01(ix, iy, iz + 1, seed)
    c101 = _hash01(ix + 1, iy, iz + 1, seed)
    c011 = _hash01(ix, iy + 1, iz + 1, seed)
    c111 = _hash01(ix + 1, iy + 1, iz + 1, seed)
    x00 = c000 + (c100 - c000) * ux
    x10 = c010 + (c110 - c010) * ux
    x01 = c001 + (c101 - c001) * ux
    x11 = c011 + (c111 - c011) * ux
    y0 = x00 + (x10 - x00) * uy
    y1 = x01 + (x11 - x01) * uy
    return y0 + (y1 - y0) * uz


def value_noise(
    points: np.ndarray, *, scale: float = 1.0, time: float = 0.0, seed: float = 0.0
) -> np.ndarray:
    p = np.asarray(points, dtype=np.float64) * float(scale)
    t0 = math.floor(float(time))
    double = float(time) - t0
    first = _value_channel(p[:, 0], p[:, 1], p[:, 2], seed + t0 * 17.0)
    second = _value_channel(p[:, 0], p[:, 1], p[:, 2], seed + (t0 + 1.0) * 17.0)
    return first + (second - first) * double


def fbm_noise(
    points: np.ndarray,
    *,
    scale: float = 1.0,
    time: float = 0.0,
    seed: float = 0.0,
    octaves: int = 3,
) -> np.ndarray:
    total = np.zeros(points.shape[0], dtype=np.float64)
    amplitude = 1.0
    norm = 0.0
    for octave in range(max(1, int(octaves))):
        total += amplitude * value_noise(
            points,
            scale=scale * (2.0**octave),
            time=time * (1.0 + octave * 0.5),
            seed=seed + octave * 13.0,
        )
        norm += amplitude
        amplitude *= 0.5
    return total / max(1e-9, norm)


def voronoi_field(
    points: np.ndarray,
    centers: np.ndarray,
    *,
    edge_width: float = 0.07,
) -> tuple[np.ndarray, np.ndarray]:
    """Nearest-cell id plus a smooth 0..1 interior mask (0 on cell borders).

    Cell borders are what make a Voronoi look read as glass instead of noise;
    keeping the mask smooth also keeps slow cell drift clean on 20-25 fps
    hardware, where hard edges would alias into visible stepping.
    """
    p = np.asarray(points, dtype=np.float64)
    c = np.asarray(centers, dtype=np.float64)
    if p.size == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float64)
    if c.shape[0] < 2:
        return np.zeros(p.shape[0], dtype=np.int64), np.ones(
            p.shape[0], dtype=np.float64
        )
    distances = np.linalg.norm(p[:, None, :] - c[None, :, :], axis=2)
    nearest = np.argmin(distances, axis=1).astype(np.int64)
    nearest_two = np.partition(distances, 1, axis=1)[:, :2]
    gap = nearest_two[:, 1] - nearest_two[:, 0]
    seam = np.clip(gap / max(1e-6, float(edge_width)), 0.0, 1.0)
    return nearest, seam * seam * (3.0 - 2.0 * seam)


def gradient_3d(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    mode = str(ctx.params.get("mode", "linear"))
    angle = math.radians(_num(ctx, "angle", 35.0))
    drift = _num(ctx, "drift", 0.25)
    speed = _num(ctx, "speed", 0.12)
    master = _num(ctx, "brightness", 1.0)
    c_a = _color_param(ctx, "color_a", [12, 18, 58])
    c_b = _color_param(ctx, "color_b", [255, 90, 24])
    c_c = _color_param(ctx, "color_c", [255, 230, 180])
    if mode == "radial":
        q = np.linalg.norm((p[:, :2] - 0.5) * 2.0, axis=1)
    elif mode == "spherical":
        q = np.linalg.norm((p - 0.5) * 2.0, axis=1)
    else:
        q = p[:, 0] * math.cos(angle) + p[:, 1] * math.sin(angle) + p[:, 2] * 0.22
    q = _fract(q + ctx.t * speed + drift * 0.1 * np.sin(ctx.t * 0.21))
    low = U.smoothstep(0.0, 0.72, q)
    high = U.smoothstep(0.52, 1.0, q)
    base = U.lerp(c_a, c_b, low)
    colour = U.lerp(base, c_c, high * 0.85)
    return np.clip(colour * master, 0.0, 255.0).astype(np.float32)


def pulse_breathe(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    c_a = _color_param(ctx, "color", [255, 96, 18])
    c_b = _color_param(ctx, "accent_color", [255, 210, 150])
    master = _num(ctx, "brightness", 0.95)
    rate = max(0.05, _num(ctx, "rate_hz", 0.6))
    depth = np.clip(_num(ctx, "depth", 0.7), 0.0, 1.0)
    spatial = _num(ctx, "spatial_offset", 0.45)
    axis = {"x": 0, "y": 1, "z": 2}.get(str(ctx.params.get("axis", "z")), 2)
    phase = ctx.t * rate * math.tau + p[:, axis] * spatial * math.tau
    breathe = 0.5 + 0.5 * np.sin(phase)
    breathe = breathe**1.7
    colour = U.lerp(c_a, c_b, breathe * 0.35)
    level = (1.0 - depth) + depth * breathe
    shimmer = 0.9 + 0.1 * np.sin(ctx.t * 2.7 + p[:, 0] * 9.0)
    return np.clip(colour * (level * shimmer * master)[:, None], 0.0, 255.0).astype(
        np.float32
    )


def chase_scanner(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    axis = {"x": 0, "y": 1, "z": 2}.get(str(ctx.params.get("axis", "x")), 0)
    coord = p[:, axis]
    speed = _num(ctx, "speed", 0.28)
    width = max(0.01, _num(ctx, "width", 0.14))
    direction = str(ctx.params.get("direction", "pingpong"))
    master = _num(ctx, "brightness", 1.0)
    c_head = _color_param(ctx, "color", [255, 255, 255])
    c_tail = _color_param(ctx, "tail_color", [255, 70, 20])
    c_bg = _color_param(ctx, "bg_color", [5, 7, 12])
    bg_level = np.clip(_num(ctx, "bg_level", 0.02), 0.0, 0.5)
    raw = _fract(ctx.t * speed)
    if direction == "pingpong":
        head = 1.0 - abs(2.0 * raw - 1.0)
    elif direction == "reverse":
        head = 1.0 - raw
    else:
        head = raw
    dist = np.abs(coord - head)
    dist = np.minimum(dist, 1.0 - dist)
    head_glow = np.exp(-((dist / width) ** 2))
    trail = np.exp(-np.maximum(0.0, head - coord) / max(0.03, width * 2.4))
    trail = np.where(coord <= head, trail, 0.0)
    colour = U.lerp(c_bg, c_tail, np.clip(trail, 0.0, 1.0))
    colour = U.lerp(colour, c_head, np.clip(head_glow, 0.0, 1.0))
    level = bg_level + (1.0 - bg_level) * np.maximum(head_glow, trail * 0.55)
    return np.clip(colour * (level * master)[:, None], 0.0, 255.0).astype(np.float32)


def pattern_bands(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    axis = {"x": 0, "y": 1, "z": 2}.get(str(ctx.params.get("axis", "y")), 1)
    bands = max(1.0, _num(ctx, "bands", 6.0))
    speed = _num(ctx, "speed", 0.18)
    contrast = np.clip(_num(ctx, "contrast", 0.85), 0.0, 1.0)
    master = _num(ctx, "brightness", 0.9)
    palette = str(ctx.params.get("palette", "synth"))
    phase = _fract(p[:, axis] * bands - ctx.t * speed)
    wave = 0.5 + 0.5 * np.cos(phase * math.tau)
    wave = 0.15 + 0.85 * wave ** (1.0 + contrast * 2.0)
    colour = U.gradient(palette, p[:, axis] * 0.7 + ctx.t * 0.03 + phase * 0.15)
    return np.clip(colour * (wave * master)[:, None], 0.0, 255.0).astype(np.float32)


def particles_3d(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    count = max(1, min(96, int(_num(ctx, "count", 28))))
    speed = _num(ctx, "speed", 0.14)
    trail_len = np.clip(_num(ctx, "trail", 0.24), 0.0, 0.9)
    size = max(0.01, _num(ctx, "size", 0.055))
    master = _num(ctx, "brightness", 1.0)
    c_head = _color_param(ctx, "color", [255, 236, 210])
    c_tail = _color_param(ctx, "tail_color", [255, 55, 12])
    c_bg = _color_param(ctx, "bg_color", [3, 4, 8])
    bg_level = np.clip(_num(ctx, "bg_level", 0.015), 0.0, 0.3)
    indices = np.arange(count, dtype=np.float64)
    sx = 0.08 + 0.84 * _hash01(indices, 0.0, 0.0, 1.0)
    sy = 0.08 + 0.84 * _hash01(indices, 0.0, 0.0, 2.0)
    phase = _hash01(indices, 0.0, 0.0, 3.0)
    heads = _fract(ctx.t * speed * (0.6 + 0.8 * phase) + phase)
    intensity = np.zeros(ctx.n, dtype=np.float64)
    mix = np.zeros(ctx.n, dtype=np.float64)
    for index in range(count):
        head = heads[index]
        tail = max(0.0, head - trail_len)
        dz = p[:, 2] - np.clip(p[:, 2], tail, head)
        dist = np.sqrt((p[:, 0] - sx[index]) ** 2 + (p[:, 1] - sy[index]) ** 2 + dz**2)
        glow = np.exp(-((dist / size) ** 2))
        along = np.clip((head - p[:, 2]) / max(1e-6, trail_len), 0.0, 1.0)
        glow *= 1.0 - along * 0.65
        take = glow > intensity
        intensity = np.where(take, glow, intensity)
        mix = np.where(take, along, mix)
    colour = U.lerp(c_head, c_tail, mix)
    level = bg_level + (1.0 - bg_level) * np.clip(intensity, 0.0, 1.0)
    out = U.lerp(U.scale(c_bg, bg_level), colour, np.clip(intensity, 0.0, 1.0))
    return np.clip(out * (level * master)[:, None], 0.0, 255.0).astype(np.float32)


def sparkle_twinkle(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    quant = np.floor(p * 34.0)
    hashed = _hash01(quant[:, 0], quant[:, 1], quant[:, 2], 7.0)
    speed = _num(ctx, "speed", 2.0)
    density = np.clip(_num(ctx, "density", 0.55), 0.0, 1.0)
    master = _num(ctx, "brightness", 1.0)
    c = _color_param(ctx, "color", [255, 232, 194])
    c_bg = _color_param(ctx, "bg_color", [8, 10, 18])
    twinkle = 0.5 + 0.5 * np.sin(ctx.t * speed * (0.4 + hashed) * math.tau + hashed * 30.0)
    twinkle = twinkle ** (2.0 + 5.0 * (1.0 - density))
    twinkle = np.where(hashed < density * 0.7 + 0.12, twinkle, 0.0)
    colour = U.lerp(c_bg, c, np.clip(twinkle, 0.0, 1.0))
    level = 0.03 + 0.97 * twinkle
    return np.clip(colour * (level * master)[:, None], 0.0, 255.0).astype(np.float32)


def shockwave_3d(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    centre = np.asarray(ctx.params.get("center", [0.5, 0.5, 0.5]), dtype=np.float64)
    speed = _num(ctx, "speed", 0.32)
    width = max(0.01, _num(ctx, "width", 0.09))
    master = _num(ctx, "brightness", 1.0)
    c = _color_param(ctx, "color", [120, 210, 255])
    c_core = _color_param(ctx, "core_color", [255, 255, 255])
    c_bg = _color_param(ctx, "bg_color", [2, 5, 12])
    radius = _fract(ctx.t * speed) * 1.75
    distance = np.linalg.norm(p - centre, axis=1)
    ring = np.exp(-(((distance - radius) / width) ** 2))
    core = np.exp(-((distance / max(0.02, width * 0.5)) ** 2)) * 0.45
    wave = np.clip(ring + core, 0.0, 1.0)
    colour = U.lerp(c, c_core, np.clip(ring**1.5, 0.0, 1.0))
    out = U.lerp(c_bg, colour, np.clip(wave, 0.0, 1.0))
    level = np.clip(wave * 1.2, 0.0, 1.0)
    return np.clip(out * (level * master)[:, None], 0.0, 255.0).astype(np.float32)


def strobe_blinder(ctx: PresetContext) -> np.ndarray:
    division = str(ctx.params.get("division", "1/8"))
    duty = np.clip(_num(ctx, "duty", 0.12), 0.01, 0.95)
    softness = np.clip(_num(ctx, "softness", 0.02), 0.0, 0.4)
    grid = max(1e-3, float(ctx.beat.get("beat_grid_s", 0.5)))
    beats = {"1/1": 4.0, "1/2": 2.0, "1/4": 1.0, "1/8": 0.5, "1/16": 0.25,
             "1/32": 0.125}
    interval = max(1e-3, grid * beats.get(division, 0.5))
    phase = _fract((ctx.t - float(ctx.beat.get("beat_offset_s", 0.0))) / interval)
    if softness <= 1e-9:
        gate = np.where(phase <= duty, 1.0, 0.0)
    else:
        gate = 1.0 - U.smoothstep(duty - softness, duty + softness, phase)
    c = _color_param(ctx, "color", [255, 250, 238])
    c_bg = _color_param(ctx, "bg_color", [4, 6, 12])
    floor = np.clip(_num(ctx, "floor", 0.02), 0.0, 1.0)
    master = _num(ctx, "brightness", 1.0)
    gate = np.clip(np.asarray(gate, dtype=np.float64), 0.0, 1.0)
    level = floor + (1.0 - floor) * gate
    colour = U.lerp(c_bg, c, gate if gate.ndim else np.full(ctx.n, float(gate)))
    factor = np.asarray(level, dtype=np.float64) * master
    if factor.ndim:
        factor = factor[..., None]
    return np.clip(colour * factor, 0.0, 255.0).astype(np.float32)


def noise_plasma(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    scale = max(0.1, _num(ctx, "scale", 3.2))
    speed = _num(ctx, "speed", 0.16)
    warp = np.clip(_num(ctx, "warp", 0.35), 0.0, 1.0)
    contrast = np.clip(_num(ctx, "contrast", 0.9), 0.0, 1.0)
    master = _num(ctx, "brightness", 1.0)
    palette = str(ctx.params.get("palette", "synth"))
    noise = fbm_noise(p, scale=scale, time=ctx.t * speed, seed=3.0, octaves=3)
    if warp > 0.0:
        wobble = value_noise(p, scale=scale * 1.7, time=ctx.t * speed * 0.7, seed=11.0)
        noise = _fract(noise + warp * (wobble - 0.5))
    plasma = 0.5 + 0.5 * np.sin(noise * math.tau * 2.0 + ctx.t * 0.3)
    plasma = plasma ** (1.0 + contrast * 2.5)
    colour = U.gradient(palette, noise * 1.3 + ctx.t * 0.02)
    return np.clip(colour * (plasma * master)[:, None], 0.0, 255.0).astype(np.float32)


def aurora_veil(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    master = _num(ctx, "brightness", 0.85)
    speed = _num(ctx, "speed", 0.18)
    width = max(0.03, _num(ctx, "veil_width", 0.22))
    palette = str(ctx.params.get("palette", "aurora"))
    shimmer = np.clip(_num(ctx, "shimmer", 0.45), 0.0, 1.0)
    t = ctx.t * speed
    ridge = 0.5 + 0.18 * np.sin(p[:, 0] * 5.2 + t * 1.7) + 0.12 * np.sin(
        p[:, 1] * 3.7 - t * 1.1 + 1.3
    )
    curtain = np.exp(-(((p[:, 2] - ridge) / width) ** 2))
    fold = 0.55 + 0.45 * np.sin(p[:, 0] * 9.0 + p[:, 1] * 4.0 + t * 2.0)
    noise = fbm_noise(p, scale=4.0, time=t * 2.0, seed=5.0, octaves=2)
    colour = U.gradient(palette, p[:, 0] * 0.5 + p[:, 1] * 0.2 + noise * 0.4 + t * 0.03)
    level = curtain * (0.45 + 0.55 * fold) * (1.0 - shimmer + shimmer * (0.5 + noise))
    return np.clip(colour * (level * master)[:, None], 0.0, 255.0).astype(np.float32)


# ---------------------------------------------------------------------------
# Additional composable basics (2026 effect pack)
# ---------------------------------------------------------------------------
def hue_sweep(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    axis = {"x": 0, "y": 1, "z": 2}.get(str(ctx.params.get("axis", "x")), 0)
    bands = max(0.25, _num(ctx, "bands", 1.5))
    speed = _num(ctx, "speed", 0.18)
    master = _num(ctx, "brightness", 0.95)
    phase = p[:, axis] * bands + ctx.t * speed
    colour = U.gradient(str(ctx.params.get("palette", "aurora")), _fract(phase))
    ripple = 0.72 + 0.28 * np.sin(phase * math.tau * 0.5)
    return np.clip(colour * (ripple * master)[:, None], 0.0, 255.0).astype(np.float32)


def radial_rings(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    centre = np.asarray(ctx.params.get("center", [0.5, 0.5, 0.5]), dtype=np.float64)
    axis_mode = str(ctx.params.get("mode", "xy"))
    if axis_mode == "xyz":
        distance = np.linalg.norm(p - centre, axis=1)
    else:
        distance = np.linalg.norm(p[:, :2] - centre[:2], axis=1)
    rings = max(1.0, _num(ctx, "rings", 7.0))
    speed = _num(ctx, "speed", 0.35)
    width = max(0.005, _num(ctx, "width", 0.08))
    master = _num(ctx, "brightness", 1.0)
    phase = _fract(distance * rings - ctx.t * speed)
    band = np.exp(-((np.minimum(phase, 1.0 - phase) / width) ** 2))
    colour = U.gradient(
        str(ctx.params.get("palette", "sunset")), distance + ctx.t * 0.05
    )
    return np.clip(colour * (band * master)[:, None], 0.0, 255.0).astype(np.float32)


def sidechain_pump(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    c_base = _color_param(ctx, "color", [120, 18, 8])
    c_peak = _color_param(ctx, "peak_color", [255, 200, 120])
    source = str(ctx.params.get("source", "kick"))
    depth = np.clip(_num(ctx, "depth", 0.85), 0.0, 1.0)
    master = _num(ctx, "brightness", 1.0)
    speed = max(0.05, _num(ctx, "rate_hz", 0.5))
    env = float(ctx.env.get(source, ctx.env.get("kick", 0.0)))
    pump = 0.5 + 0.5 * math.sin(ctx.t * speed * math.tau)
    level = (1.0 - depth) + depth * max(env, pump * 0.35)
    colour = U.lerp(c_base, c_peak, np.clip(level, 0.0, 1.0))
    spatial = 0.9 + 0.1 * np.sin(p[:, 0] * 6.0 + ctx.t * 3.0)
    return np.clip(colour * (level * spatial * master)[:, None], 0.0, 255.0).astype(
        np.float32
    )


def meter_bars(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    axis = {"x": 0, "y": 1, "z": 2}.get(str(ctx.params.get("axis", "x")), 0)
    bars = max(2.0, _num(ctx, "bars", 12.0))
    speed = _num(ctx, "speed", 1.2)
    master = _num(ctx, "brightness", 1.0)
    index = np.floor(p[:, axis] * bars)
    hashed = _hash01(index, np.zeros_like(index), np.zeros_like(index), 11.0)
    wave = 0.42 + 0.58 * np.abs(np.sin(ctx.t * speed + hashed * math.tau))
    height = p[:, 2]
    gate = np.where(height <= 0.15 + wave * 0.8, 1.0, 0.08)
    colour = U.gradient(str(ctx.params.get("palette", "ember")), index / bars)
    level = gate * (0.55 + 0.45 * wave) * master
    return np.clip(colour * level[:, None], 0.0, 255.0).astype(np.float32)


def candle_flicker(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    master = _num(ctx, "brightness", 0.9)
    speed = _num(ctx, "speed", 1.0)
    warmth = np.clip(_num(ctx, "warmth", 0.75), 0.0, 1.0)
    quant = np.floor(p * 48.0)
    hashed = _hash01(quant[:, 0], quant[:, 1], quant[:, 2], 23.0)
    flicker = 0.72 + 0.28 * np.sin(
        ctx.t * speed * (5.0 + hashed * 7.0) + hashed * 40.0
    )
    gust = 0.88 + 0.12 * np.sin(ctx.t * 0.8 + p[:, 1] * 2.5)
    level = np.clip(flicker * gust, 0.0, 1.0) * master
    c_warm = _color_param(ctx, "color", [255, 148, 48])
    c_cool = _color_param(ctx, "cool_color", [255, 88, 18])
    colour = U.lerp(c_cool, c_warm, warmth)
    return np.clip(colour * level[:, None], 0.0, 255.0).astype(np.float32)


def glitch_shards(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    grid = max(2.0, _num(ctx, "grid", 9.0))
    rate = max(0.5, _num(ctx, "rate", 6.0))
    master = _num(ctx, "brightness", 1.0)
    tick = np.floor(ctx.t * rate)
    quant = np.floor(p * grid)
    hashed = _hash01(quant[:, 0], quant[:, 1], quant[:, 2], tick)
    shift = _hash01(quant[:, 1], quant[:, 2], np.full_like(quant[:, 0], tick), 31.0)
    palette = str(ctx.params.get("palette", "synth"))
    colour = U.gradient(palette, hashed * 2.0 + shift * 0.7)
    keep = hashed > 0.32
    level = np.where(keep, 0.35 + 0.65 * hashed, 0.03) * master
    return np.clip(colour * level[:, None], 0.0, 255.0).astype(np.float32)


def comet_trails(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    axis = {"x": 0, "y": 1, "z": 2}.get(str(ctx.params.get("axis", "x")), 0)
    speed = _num(ctx, "speed", 0.22)
    tail = max(0.02, _num(ctx, "tail", 0.18))
    master = _num(ctx, "brightness", 1.0)
    c_head = _color_param(ctx, "color", [255, 245, 220])
    c_tail = _color_param(ctx, "tail_color", [255, 68, 18])
    c_bg = _color_param(ctx, "bg_color", [4, 6, 12])
    head = _fract(ctx.t * speed)
    delta = np.abs(p[:, axis] - head)
    wrapped = np.minimum(delta, 1.0 - delta)
    glow = np.exp(-(wrapped / tail) ** 2)
    colour = U.lerp(c_tail, c_head, np.clip(glow**1.8, 0.0, 1.0))
    level = np.clip(glow * 1.25, 0.0, 1.0) * master
    return np.clip(U.lerp(c_bg, colour, level), 0.0, 255.0).astype(np.float32)


def duotone_wipe(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    axis = {"x": 0, "y": 1, "z": 2}.get(str(ctx.params.get("axis", "y")), 1)
    speed = max(0.01, _num(ctx, "speed", 0.12))
    width = max(0.01, _num(ctx, "width", 0.18))
    master = _num(ctx, "brightness", 1.0)
    c_a = _color_param(ctx, "color_a", [18, 24, 90])
    c_b = _color_param(ctx, "color_b", [255, 96, 24])
    triangle = abs(_fract(ctx.t * speed) * 2.0 - 1.0)
    edge = p[:, axis] - triangle
    field = U.smoothstep(-width, width, edge)
    colour = U.lerp(c_a, c_b, field)
    seam = np.exp(-((edge / max(0.02, width * 0.35)) ** 2)) * 0.35
    return np.clip(colour * master + seam[:, None] * 255.0, 0.0, 255.0).astype(
        np.float32
    )


def prism_fan(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    centre = np.asarray(ctx.params.get("center", [0.5, 0.5]), dtype=np.float64)[:2]
    speed = _num(ctx, "speed", 0.08)
    rings = max(0.05, _num(ctx, "prisms", 8.0))
    master = _num(ctx, "brightness", 1.0)
    dx = p[:, 0] - centre[0]
    dy = p[:, 1] - centre[1]
    angle = np.arctan2(dy, dx) / math.tau + 0.5
    radius = np.linalg.norm(p[:, :2] - centre, axis=1)
    wedge = _fract(angle * rings + ctx.t * speed)
    sparkle = np.exp(-((wedge - 0.5) ** 2) / 0.02)
    falloff = 0.35 + 0.65 * np.clip(radius * 1.6, 0.0, 1.0)
    colour = U.gradient(str(ctx.params.get("palette", "aurora")), wedge * 1.2)
    level = (0.12 + 0.88 * sparkle) * falloff * master
    return np.clip(colour * level[:, None], 0.0, 255.0).astype(np.float32)


def volumetric_tunnel(ctx: PresetContext) -> np.ndarray:
    p = ctx.normalized.astype(np.float64)
    centre = np.asarray(ctx.params.get("center", [0.5, 0.5]), dtype=np.float64)[:2]
    rings = max(1.0, _num(ctx, "rings", 6.0))
    speed = _num(ctx, "speed", 0.3)
    depth_twist = _num(ctx, "twist", 0.7)
    master = _num(ctx, "brightness", 1.0)
    dx = p[:, 0] - centre[0]
    dy = p[:, 1] - centre[1]
    angle = np.arctan2(dy, dx) / math.tau
    radius = np.linalg.norm(p[:, :2] - centre, axis=1)
    phase = _fract(radius * rings - ctx.t * speed + p[:, 2] * depth_twist)
    band = np.exp(-((np.minimum(phase, 1.0 - phase) / 0.16) ** 2))
    colour = U.gradient(
        str(ctx.params.get("palette", "ice")), angle + radius * 0.4 + ctx.t * 0.04
    )
    depth_fade = 0.35 + 0.65 * p[:, 2]
    return np.clip(colour * (band * depth_fade * master)[:, None], 0.0, 255.0).astype(
        np.float32
    )


def cell_bloom(ctx: PresetContext) -> np.ndarray:
    """Slow, glassy Voronoi cells with dark seams and a soft interior bloom."""
    p = ctx.normalized.astype(np.float64)
    cells = max(2, min(32, int(_num(ctx, "cells", 10))))
    speed = max(0.0, _num(ctx, "speed", 0.12))
    edge_width = max(0.005, _num(ctx, "edge_width", 0.07))
    breath = np.clip(_num(ctx, "breath", 0.18), 0.0, 1.0)
    edge_mix = np.clip(_num(ctx, "edge_mix", 0.30), 0.0, 1.0)
    master = _num(ctx, "brightness", 0.95)
    palette = str(ctx.params.get("palette", "aurora"))
    edge_colour = _color_param(ctx, "edge_color", [255, 236, 210])

    phase = np.arange(cells, dtype=np.float64) * 2.3999632297
    t = float(ctx.t) * speed
    centres = np.stack(
        (
            0.5 + 0.33 * np.sin(t * 0.9 + phase),
            0.5 + 0.33 * np.cos(t * 0.7 + phase * 1.7),
            0.5 + 0.28 * np.sin(t * 0.5 + phase * 0.6),
        ),
        axis=1,
    )
    nearest, interior = voronoi_field(p, centres, edge_width=edge_width)
    cell_phase = phase[nearest]
    colour = U.gradient(
        palette, nearest / cells + 0.04 * U.sine01(ctx.t, 0.2, cell_phase)
    )
    colour = U.lerp(colour, edge_colour, (1.0 - interior) * edge_mix)
    breathe = 1.0 - breath * U.sine01(ctx.t, 0.08, cell_phase)
    level = (0.06 + 0.94 * interior) * breathe * master
    return np.clip(colour * level[:, None], 0.0, 255.0).astype(np.float32)


def silk_folds(ctx: PresetContext) -> np.ndarray:
    """Soft travelling folds: low-frequency bands warped by slow 3D noise."""
    p = ctx.normalized.astype(np.float64)
    folds = max(1.0, _num(ctx, "folds", 4.0))
    speed = max(0.0, _num(ctx, "speed", 0.10))
    warp = np.clip(_num(ctx, "warp", 0.35), 0.0, 1.0)
    master = _num(ctx, "brightness", 0.95)
    palette = str(ctx.params.get("palette", "sunset"))

    t = float(ctx.t) * speed
    drift = fbm_noise(p, scale=2.2, time=t * 0.8, seed=4.0, octaves=2)
    ripple = value_noise(p, scale=2.8, time=t * 0.6, seed=9.0)
    phase = (
        p[:, 0] * folds
        + p[:, 2] * 1.1
        + 0.45 * np.sin(p[:, 1] * 2.7 + t * 1.6)
        + (drift - 0.5) * warp * 2.0
    )
    band = U.sine01(phase)
    colour = U.gradient(
        palette, band * 0.55 + p[:, 2] * 0.25 + ripple * 0.2 + t * 0.01
    )
    level = (0.12 + 0.88 * band) * master
    return np.clip(colour * level[:, None], 0.0, 255.0).astype(np.float32)


def color_wash(ctx: PresetContext) -> np.ndarray:
    """Four-corner room wash with soft centre glow and edge falloff."""
    p = ctx.normalized.astype(np.float64)
    master = _num(ctx, "brightness", 0.9)
    saturation = max(0.0, _num(ctx, "saturation", 1.0))
    falloff = np.clip(_num(ctx, "edge_falloff", 0.45), 0.0, 1.0)
    softness = max(0.02, _num(ctx, "softness", 0.22))
    drift = _num(ctx, "drift", 0.08)
    speed = _num(ctx, "speed", 0.06)

    tl = _color_param(ctx, "corner_tl", [255, 112, 28])
    tr = _color_param(ctx, "corner_tr", [34, 92, 180])
    bl = _color_param(ctx, "corner_bl", [120, 28, 96])
    br = _color_param(ctx, "corner_br", [255, 226, 176])

    wobble = drift * np.sin(ctx.t * speed * math.tau + p[:, 2] * 2.0)
    x = np.clip(p[:, 0] + wobble, 0.0, 1.0)
    z = np.clip(p[:, 2] + wobble * 0.5, 0.0, 1.0)
    top = U.lerp(tl, tr, x)
    bottom = U.lerp(bl, br, x)
    colour = U.lerp(bottom, top, z)

    luma = 0.2126 * colour[:, 0] + 0.7152 * colour[:, 1] + 0.0722 * colour[:, 2]
    colour = luma[:, None] + (colour - luma[:, None]) * saturation
    centre = np.linalg.norm((p - 0.5) * 2.0, axis=1)
    glow = np.exp(-((centre / max(1e-6, softness * 3.0)) ** 2))
    level = (1.0 - falloff) + falloff * (0.25 + 0.75 * glow)
    return np.clip(colour * (level * master)[:, None], 0.0, 255.0).astype(np.float32)


def wave_interference(ctx: PresetContext) -> np.ndarray:
    """Two to four superposed 3D wave fronts with a shared drift angle."""
    p = ctx.normalized.astype(np.float64)
    sources = max(2, min(4, int(_num(ctx, "sources", 3))))
    frequency = max(0.1, _num(ctx, "frequency", 2.4))
    amplitude = np.clip(_num(ctx, "amplitude", 0.85), 0.0, 1.0)
    speed = _num(ctx, "speed", 0.22)
    angle = math.radians(_num(ctx, "angle", 28.0))
    contrast = max(0.2, _num(ctx, "contrast", 1.4))
    master = _num(ctx, "brightness", 1.0)
    palette = str(ctx.params.get("palette", "ice"))

    total = np.zeros(ctx.n, dtype=np.float64)
    for index in range(sources):
        theta = angle + index * math.pi / sources
        dz = 0.22 * math.sin(index * 1.7 + 0.4)
        direction = np.asarray([math.cos(theta), math.sin(theta), dz])
        phase = (
            p @ direction * frequency
            - ctx.t * speed * (1.0 + index * 0.17)
            + index * 1.31
        )
        total += np.sin(phase * math.tau)
    raw = (total / sources + 1.0) * 0.5
    crest = np.clip(raw, 0.0, 1.0) ** contrast
    colour = U.gradient(palette, raw * 0.75 + p[:, 2] * 0.2 + ctx.t * 0.015)
    level = (0.08 + 0.92 * crest) * amplitude * master
    seam = np.exp(-((raw - 0.5) / 0.12) ** 2) * 0.2
    return np.clip((colour + seam[:, None] * 90.0) * level[:, None], 0.0, 255.0).astype(
        np.float32
    )


def shutter_matrix(ctx: PresetContext) -> np.ndarray:
    """Beat-locked matrix shutter with per-emitter attack/decay phase spread."""
    p = ctx.normalized.astype(np.float64)
    division = str(ctx.params.get("division", "1/8"))
    attack = np.clip(_num(ctx, "attack", 0.06), 0.005, 0.8)
    decay = np.clip(_num(ctx, "decay", 0.24), 0.01, 1.0)
    spread = np.clip(_num(ctx, "emitter_spread", 0.35), 0.0, 1.0)
    floor = np.clip(_num(ctx, "background_floor", 0.03), 0.0, 1.0)
    master = _num(ctx, "brightness", 1.0)
    c_flash = _color_param(ctx, "color", [255, 248, 236])
    c_bg = _color_param(ctx, "bg_color", [5, 7, 14])

    grid = max(1e-3, float(ctx.beat.get("beat_grid_s", 0.5)))
    beats = {"1/2": 2.0, "1/4": 1.0, "1/8": 0.5, "1/16": 0.25}
    interval = max(1e-3, grid * beats.get(division, 0.5))
    rank = np.where(ctx.target_rank >= 0, ctx.target_rank, 0)
    emitter_norm = rank / max(1.0, float(ctx.target_size))
    phase = _fract((ctx.t - float(ctx.beat.get("beat_offset_s", 0.0))) / interval)
    phase = _fract(phase - emitter_norm * spread)
    gate = np.where(
        phase <= attack,
        phase / max(1e-6, attack),
        np.exp(-(phase - attack) / max(1e-6, decay)),
    )
    level = floor + (1.0 - floor) * np.clip(gate, 0.0, 1.0)
    colour = U.lerp(c_bg, c_flash, np.clip(gate, 0.0, 1.0))
    # Keep a little spatial texture so the matrix does not read as one lamp.
    texture = 0.92 + 0.08 * np.sin(p[:, 0] * 12.0 + p[:, 2] * 7.0)
    return np.clip(colour * (level * texture * master)[:, None], 0.0, 255.0).astype(
        np.float32
    )


def emitter_swarm(ctx: PresetContext) -> np.ndarray:
    """Deterministic analytic particles; no persistent simulation state."""
    p = ctx.normalized.astype(np.float64)
    origin = np.asarray(ctx.params.get("emitter_position", [0.5, 0.5, 0.25]), dtype=np.float64)
    radius = np.asarray(ctx.params.get("emitter_radius", [0.16, 0.14, 0.08]), dtype=np.float64)
    velocity = np.asarray(ctx.params.get("velocity", [0.08, 0.05, 0.18]), dtype=np.float64)
    gravity = np.asarray(ctx.params.get("gravity", [0.0, 0.0, -0.12]), dtype=np.float64)
    resistance = max(0.0, _num(ctx, "resistance", 0.45))
    turbulence = max(0.0, _num(ctx, "turbulence", 0.08))
    birth_rate = max(0.5, _num(ctx, "birth_rate", 9.0))
    lifetime = max(0.25, _num(ctx, "lifetime", 2.6))
    size = max(0.005, _num(ctx, "particle_size", 0.28))
    fade_tail = np.clip(_num(ctx, "fade_tail", 0.55), 0.0, 1.0)
    master = _num(ctx, "brightness", 1.0)
    seed = _num(ctx, "seed", 1.0)
    c_birth = _color_param(ctx, "birth_color", [255, 236, 196])
    c_death = _color_param(ctx, "death_color", [255, 54, 12])
    c_bg = _color_param(ctx, "bg_color", [3, 4, 9])
    bg_level = np.clip(_num(ctx, "bg_level", 0.01), 0.0, 0.3)

    count = max(1, min(64, int(round(birth_rate * lifetime))))
    indices = np.arange(count, dtype=np.float64)
    phase0 = _hash01(indices, np.zeros_like(indices), np.zeros_like(indices), seed)
    offsets = np.stack(
        (
            (_hash01(indices, np.zeros_like(indices), np.zeros_like(indices), seed + 3.0) - 0.5) * 2.0,
            (_hash01(indices, np.zeros_like(indices), np.zeros_like(indices), seed + 7.0) - 0.5) * 2.0,
            (_hash01(indices, np.zeros_like(indices), np.zeros_like(indices), seed + 11.0) - 0.5) * 2.0,
        ),
        axis=1,
    )
    intensity = np.zeros(ctx.n, dtype=np.float64)
    mix = np.zeros(ctx.n, dtype=np.float64)
    for index in range(count):
        cycle = _fract(ctx.t / lifetime + phase0[index])
        age = cycle * lifetime
        decayed = (1.0 - math.exp(-resistance * age)) / max(0.05, resistance)
        pos = (
            origin
            + offsets[index] * radius
            + velocity * decayed
            + 0.5 * gravity * age * age
        )
        wobble = turbulence * np.asarray(
            [
                math.sin(age * 5.1 + index * 1.7 + seed),
                math.cos(age * 4.3 + index * 2.3 + seed),
                math.sin(age * 6.7 + index * 0.9 + seed),
            ]
        )
        pos = pos + wobble
        dist = np.linalg.norm(p - pos, axis=1)
        life = 1.0 - cycle
        glow = np.exp(-((dist / size) ** 2))
        glow *= (0.25 + 0.75 * life) ** max(0.05, fade_tail)
        take = glow > intensity
        intensity = np.where(take, glow, intensity)
        mix = np.where(take, cycle, mix)
    colour = U.lerp(c_birth, c_death, mix)
    level = bg_level + (1.0 - bg_level) * np.clip(intensity, 0.0, 1.0)
    out = U.lerp(U.scale(c_bg, bg_level), colour, np.clip(intensity, 0.0, 1.0))
    return np.clip(out * (level * master)[:, None], 0.0, 255.0).astype(np.float32)



# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Preset:
    id: str
    name: str
    description: str
    category: str
    params: tuple[ParamSpec, ...]
    render: Callable[[PresetContext], np.ndarray]

    def default_params(self) -> dict[str, Any]:
        return {p.key: copy.deepcopy(p.default) for p in self.params}

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "params": [p.as_dict() for p in self.params],
        }


CATEGORY_ORDER = ("Basic", "Patterns", "Particles", "Spatial FX", "Atmosphere")
_AXIS_OPTIONS = (("x", "X / Width"), ("y", "Y / Depth"), ("z", "Z / Height"))


def _vec3(key: str, label: str, default: list[float]) -> ParamSpec:
    return ParamSpec(key, label, "vec3", list(default))


PRESETS: dict[str, Preset] = {
    # -- Basic -----------------------------------------------------------
    "solid": Preset(
        id="solid",
        name="Solid Field",
        description="Flat colour wash for bases, blackouts and cue accents.",
        category="Basic",
        params=(
            _color("color", "Colour", [255, 110, 20]),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=solid,
    ),
    "gradient_3d": Preset(
        id="gradient_3d",
        name="3D Gradient",
        description="Linear, radial or spherical fields with a rotating angle and drift.",
        category="Basic",
        params=(
            ParamSpec(
                "mode",
                "Projection",
                "select",
                "linear",
                options=(
                    ("linear", "Linear / Rotatable"),
                    ("radial", "Radial"),
                    ("spherical", "Spherical"),
                ),
            ),
            _color("color_a", "Field A", [12, 18, 58]),
            _color("color_b", "Field B", [255, 90, 24]),
            _color("color_c", "Field C", [255, 230, 180]),
            _range("angle", "Angle", 35.0, 0.0, 360.0, 1.0),
            _range("drift", "Drift", 0.25, 0.0, 1.0, 0.01),
            _range("speed", "Speed", 0.12, 0.0, 1.0, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=gradient_3d,
    ),
    "pulse_breathe": Preset(
        id="pulse_breathe",
        name="Pulse Breathe",
        description="Slow architectural breathing with per-voxel spatial phase offset.",
        category="Basic",
        params=(
            _color("color", "Core", [255, 96, 18]),
            _color("accent_color", "Accent", [255, 210, 150]),
            ParamSpec("axis", "Phase Axis", "select", "z", options=_AXIS_OPTIONS),
            _range("rate_hz", "Rate", 0.6, 0.05, 4.0, 0.01),
            _range("depth", "Depth", 0.7, 0.0, 1.0, 0.01),
            _range("spatial_offset", "Spatial Offset", 0.45, 0.0, 2.0, 0.01),
            _range("brightness", "Brightness", 0.95, 0.0, 1.0, 0.01),
        ),
        render=pulse_breathe,
    ),
    # -- Patterns --------------------------------------------------------
    "chase_scanner": Preset(
        id="chase_scanner",
        name="Chase Scanner",
        description="A directional scanner head with a decaying tail on X, Y or Z.",
        category="Patterns",
        params=(
            ParamSpec("axis", "Axis", "select", "x", options=_AXIS_OPTIONS),
            ParamSpec(
                "direction",
                "Direction",
                "select",
                "pingpong",
                options=(
                    ("pingpong", "Ping-Pong"),
                    ("forward", "Forward"),
                    ("reverse", "Reverse"),
                ),
            ),
            _color("color", "Head", [255, 255, 255]),
            _color("tail_color", "Tail", [255, 70, 20]),
            _color("bg_color", "Floor", [5, 7, 12]),
            _range("width", "Head Width", 0.14, 0.01, 0.6, 0.01),
            _range("speed", "Speed", 0.28, 0.01, 2.0, 0.01),
            _range("bg_level", "Floor Level", 0.02, 0.0, 0.5, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=chase_scanner,
    ),
    "pattern_bands": Preset(
        id="pattern_bands",
        name="Pattern Bands",
        description="Musical colour bands stepping along one room axis.",
        category="Patterns",
        params=(
            ParamSpec("axis", "Axis", "select", "y", options=_AXIS_OPTIONS),
            _palette("palette", "Palette", "synth"),
            _range("bands", "Bands", 6.0, 1.0, 32.0, 1.0),
            _range("speed", "Speed", 0.18, 0.0, 1.5, 0.01),
            _range("contrast", "Edge", 0.85, 0.0, 1.0, 0.01),
            _range("brightness", "Brightness", 0.9, 0.0, 1.0, 0.01),
        ),
        render=pattern_bands,
    ),
    "ma3_wings": Preset(
        id="ma3_wings",
        name="MA3 Wings",
        description="Beat-locked symmetric wing stepping across the target group.",
        category="Patterns",
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
    # -- Particles -------------------------------------------------------
    "particles_3d": Preset(
        id="particles_3d",
        name="3D Particles",
        description="Deterministic meteor system with trailing heads for strips and spots.",
        category="Particles",
        params=(
            _color("color", "Particle Head", [255, 236, 210]),
            _color("tail_color", "Meteor Tail", [255, 55, 12]),
            _color("bg_color", "Night", [3, 4, 8]),
            _range("count", "Particles", 28.0, 1.0, 96.0, 1.0),
            _range("speed", "Speed", 0.14, 0.01, 1.0, 0.01),
            _range("trail", "Trail Length", 0.24, 0.0, 0.9, 0.01),
            _range("size", "Head Size", 0.055, 0.01, 0.3, 0.005),
            _range("bg_level", "Night Level", 0.015, 0.0, 0.3, 0.005),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=particles_3d,
    ),
    "sparkle_twinkle": Preset(
        id="sparkle_twinkle",
        name="Sparkle Twinkle",
        description="Sparse 3D hash sparkles that twinkle independently of audio.",
        category="Particles",
        params=(
            _color("color", "Sparkle", [255, 232, 194]),
            _color("bg_color", "Night", [8, 10, 18]),
            _range("speed", "Twinkle Speed", 2.0, 0.1, 8.0, 0.01),
            _range("density", "Density", 0.55, 0.0, 1.0, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=sparkle_twinkle,
    ),
    # -- Spatial FX ------------------------------------------------------
    "shockwave_3d": Preset(
        id="shockwave_3d",
        name="Shockwave 3D",
        description="Expanding spherical pressure wave with a hot core.",
        category="Spatial FX",
        params=(
            _vec3("center", "Wave Origin", [0.5, 0.5, 0.5]),
            _color("color", "Wave", [120, 210, 255]),
            _color("core_color", "Core", [255, 255, 255]),
            _color("bg_color", "Vacuum", [2, 5, 12]),
            _range("speed", "Speed", 0.32, 0.02, 1.5, 0.01),
            _range("width", "Wave Width", 0.09, 0.01, 0.5, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=shockwave_3d,
    ),
    "strobe_blinder": Preset(
        id="strobe_blinder",
        name="Strobe Blinder",
        description="Hard shutter strobe on the musical grid without any audio input.",
        category="Spatial FX",
        params=(
            ParamSpec(
                "division",
                "Grid",
                "select",
                "1/8",
                options=(("1/8", "Eighth"), ("1/4", "Quarter"), ("1/16", "Sixteenth")),
            ),
            _color("color", "Flash", [255, 250, 238]),
            _color("bg_color", "Closed", [4, 6, 12]),
            _range("duty", "Duty", 0.12, 0.01, 0.95, 0.01),
            _range("softness", "Edge", 0.02, 0.0, 0.4, 0.005),
            _range("floor", "Closed Level", 0.02, 0.0, 1.0, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=strobe_blinder,
    ),
    # -- Atmosphere ------------------------------------------------------
    "turrell_horizon": Preset(
        id="turrell_horizon",
        name="Turrell Horizon",
        description="Architectural colour field with a luminous 3D horizon seam.",
        category="Atmosphere",
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
        category="Atmosphere",
        params=(
            _palette("palette", "Colour Atmosphere", "moonlight"),
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
    "blackbody_forge": Preset(
        id="blackbody_forge",
        name="Blackbody Forge",
        description="Deterministic thermal ember field from 1800K amber to incandescent gold.",
        category="Atmosphere",
        params=(
            _range("base_kelvin", "Idle Ember", 1850.0, 1600.0, 2600.0, 10.0),
            _range("peak_kelvin", "Peak Flash", 3800.0, 2800.0, 5500.0, 50.0),
            _range("brightness", "Master Brightness", 1.0, 0.05, 1.0, 0.01),
            _range("diffusion", "Heat Conduction", 0.60, 0.1, 1.0, 0.01),
            _range("cooling", "Radiative Cooling", 1.3, 0.5, 3.0, 0.01),
            ParamSpec("indigo_shadow", "Indigo Shadows", "bool", True),
            ParamSpec("axis", "Heat Axis", "select", "x", options=_AXIS_OPTIONS),
        ),
        render=blackbody_forge,
    ),
    "noise_plasma": Preset(
        id="noise_plasma",
        name="Noise Plasma",
        description="Warped 3D value-noise plasma with deterministic time evolution.",
        category="Atmosphere",
        params=(
            _palette("palette", "Palette", "synth"),
            _range("scale", "Noise Scale", 3.2, 0.2, 12.0, 0.1),
            _range("speed", "Flow Speed", 0.16, 0.0, 1.5, 0.01),
            _range("warp", "Domain Warp", 0.35, 0.0, 1.0, 0.01),
            _range("contrast", "Plasma Edge", 0.9, 0.0, 1.0, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=noise_plasma,
    ),
    "aurora_veil": Preset(
        id="aurora_veil",
        name="Aurora Veil",
        description="Flowing luminous curtains in 3D, independent of audio.",
        category="Atmosphere",
        params=(
            _palette("palette", "Palette", "aurora"),
            _range("speed", "Flow Speed", 0.18, 0.0, 1.2, 0.01),
            _range("veil_width", "Veil Width", 0.22, 0.03, 0.8, 0.01),
            _range("shimmer", "Shimmer", 0.45, 0.0, 1.0, 0.01),
            _range("brightness", "Brightness", 0.85, 0.0, 1.0, 0.01),
        ),
        render=aurora_veil,
    ),
    "hue_sweep": Preset(
        id="hue_sweep",
        name="Hue Sweep",
        description="Continuous palette sweep along an axis with a soft ripple.",
        category="Patterns",
        params=(
            ParamSpec("axis", "Axis", "select", "x", options=_AXIS_OPTIONS),
            _palette("palette", "Palette", "aurora"),
            _range("bands", "Bands", 1.5, 0.25, 6.0, 0.05),
            _range("speed", "Speed", 0.18, 0.0, 2.0, 0.01),
            _range("brightness", "Brightness", 0.95, 0.0, 1.0, 0.01),
        ),
        render=hue_sweep,
    ),
    "radial_rings": Preset(
        id="radial_rings",
        name="Radial Rings",
        description="Expanding concentric rings around a movable centre.",
        category="Patterns",
        params=(
            ParamSpec("mode", "Projection", "select", "xy", options=(("xy", "Floor / X-Y"), ("xyz", "3D Sphere"))),
            _vec3("center", "Centre", [0.5, 0.5, 0.5]),
            _palette("palette", "Palette", "sunset"),
            _range("rings", "Rings", 7.0, 1.0, 24.0, 1.0),
            _range("width", "Ring Width", 0.08, 0.005, 0.4, 0.005),
            _range("speed", "Speed", 0.35, 0.0, 2.0, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=radial_rings,
    ),
    "sidechain_pump": Preset(
        id="sidechain_pump",
        name="Sidechain Pump",
        description="Classic kick-ducked pump with a bright peak colour.",
        category="Basic",
        params=(
            _color("color", "Base", [120, 18, 8]),
            _color("peak_color", "Peak", [255, 200, 120]),
            ParamSpec("source", "Envelope", "select", "kick", options=(("kick", "Kick"), ("pulse", "Pulse"), ("snare", "Snare"), ("energy", "Energy"))),
            _range("depth", "Depth", 0.85, 0.0, 1.0, 0.01),
            _range("rate_hz", "Fallback Rate", 0.5, 0.05, 4.0, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=sidechain_pump,
    ),
    "meter_bars": Preset(
        id="meter_bars",
        name="Meter Bars",
        description="Independent bouncing level bars along the room axis.",
        category="Patterns",
        params=(
            ParamSpec("axis", "Bar Axis", "select", "x", options=_AXIS_OPTIONS),
            _palette("palette", "Palette", "ember"),
            _range("bars", "Bars", 12.0, 2.0, 32.0, 1.0),
            _range("speed", "Speed", 1.2, 0.05, 4.0, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=meter_bars,
    ),
    "candle_flicker": Preset(
        id="candle_flicker",
        name="Candle Flicker",
        description="Per-voxel warm candle flicker with slow gusts.",
        category="Atmosphere",
        params=(
            _color("color", "Warm", [255, 148, 48]),
            _color("cool_color", "Cool", [255, 88, 18]),
            _range("warmth", "Warmth", 0.75, 0.0, 1.0, 0.01),
            _range("speed", "Flicker Speed", 1.0, 0.1, 4.0, 0.01),
            _range("brightness", "Brightness", 0.9, 0.0, 1.0, 0.01),
        ),
        render=candle_flicker,
    ),
    "glitch_shards": Preset(
        id="glitch_shards",
        name="Glitch Shards",
        description="Quantised colour blocks that re-roll on a rhythmic tick.",
        category="Spatial FX",
        params=(
            _palette("palette", "Palette", "synth"),
            _range("grid", "Grid", 9.0, 2.0, 24.0, 1.0),
            _range("rate", "Re-roll Rate", 6.0, 0.5, 24.0, 0.5),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=glitch_shards,
    ),
    "comet_trails": Preset(
        id="comet_trails",
        name="Comet Trails",
        description="A comet head with a decaying tail looping along one axis.",
        category="Particles",
        params=(
            ParamSpec("axis", "Axis", "select", "x", options=_AXIS_OPTIONS),
            _color("color", "Head", [255, 245, 220]),
            _color("tail_color", "Tail", [255, 68, 18]),
            _color("bg_color", "Background", [4, 6, 12]),
            _range("tail", "Tail Length", 0.18, 0.02, 0.6, 0.01),
            _range("speed", "Speed", 0.22, 0.01, 1.5, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=comet_trails,
    ),
    "duotone_wipe": Preset(
        id="duotone_wipe",
        name="Duotone Wipe",
        description="Ping-pong two-colour wipe with a bright seam.",
        category="Patterns",
        params=(
            ParamSpec("axis", "Axis", "select", "y", options=_AXIS_OPTIONS),
            _color("color_a", "Colour A", [18, 24, 90]),
            _color("color_b", "Colour B", [255, 96, 24]),
            _range("width", "Softness", 0.18, 0.01, 0.6, 0.01),
            _range("speed", "Speed", 0.12, 0.01, 1.0, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=duotone_wipe,
    ),
    "prism_fan": Preset(
        id="prism_fan",
        name="Prism Fan",
        description="Rotating radial wedges radiating from a centre point.",
        category="Spatial FX",
        params=(
            _palette("palette", "Palette", "aurora"),
            _vec3("center", "Centre", [0.5, 0.5, 0.5]),
            _range("prisms", "Wedges", 8.0, 2.0, 24.0, 1.0),
            _range("speed", "Rotation", 0.08, -0.5, 0.5, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=prism_fan,
    ),
    "volumetric_tunnel": Preset(
        id="volumetric_tunnel",
        name="Volumetric Tunnel",
        description="Twisting concentric rings receding through the room.",
        category="Spatial FX",
        params=(
            _palette("palette", "Palette", "ice"),
            _vec3("center", "Centre", [0.5, 0.5, 0.5]),
            _range("rings", "Rings", 6.0, 1.0, 20.0, 1.0),
            _range("speed", "Speed", 0.3, 0.0, 2.0, 0.01),
            _range("twist", "Twist", 0.7, 0.0, 4.0, 0.05),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=volumetric_tunnel,
    ),
    "cell_bloom": Preset(
        id="cell_bloom",
        name="Cell Bloom",
        description="Slow drifting glass cells with soft seams and breathing interiors.",
        category="Spatial FX",
        params=(
            _palette("palette", "Palette", "aurora"),
            _color("edge_color", "Seam Glow", [255, 236, 210]),
            _range("cells", "Cells", 10.0, 2.0, 32.0, 1.0),
            _range("edge_width", "Seam Width", 0.07, 0.005, 0.4, 0.005),
            _range("edge_mix", "Seam Glow Mix", 0.30, 0.0, 1.0, 0.01),
            _range("speed", "Drift", 0.12, 0.0, 0.8, 0.01),
            _range("breath", "Breath Depth", 0.18, 0.0, 1.0, 0.01),
            _range("brightness", "Brightness", 0.95, 0.0, 1.0, 0.01),
        ),
        render=cell_bloom,
    ),
    "silk_folds": Preset(
        id="silk_folds",
        name="Silk Folds",
        description="Band-limited travelling folds for smooth ambient washes.",
        category="Atmosphere",
        params=(
            _palette("palette", "Palette", "sunset"),
            _range("folds", "Folds", 4.0, 1.0, 12.0, 0.1),
            _range("warp", "Noise Warp", 0.35, 0.0, 1.0, 0.01),
            _range("speed", "Drift", 0.10, 0.0, 0.6, 0.01),
            _range("brightness", "Brightness", 0.95, 0.0, 1.0, 0.01),
        ),
        render=silk_folds,
    ),
    "color_wash": Preset(
        id="color_wash",
        name="Color Wash",
        description="Four-corner room wash with saturation, centre glow and edge falloff.",
        category="Basic",
        params=(
            _color("corner_tl", "Corner TL", [255, 112, 28]),
            _color("corner_tr", "Corner TR", [34, 92, 180]),
            _color("corner_bl", "Corner BL", [120, 28, 96]),
            _color("corner_br", "Corner BR", [255, 226, 176]),
            _range("saturation", "Saturation", 1.0, 0.0, 2.0, 0.01),
            _range("edge_falloff", "Edge Falloff", 0.45, 0.0, 1.0, 0.01),
            _range("softness", "Centre Softness", 0.22, 0.02, 1.0, 0.01),
            _range("drift", "Corner Drift", 0.08, 0.0, 0.5, 0.01),
            _range("speed", "Drift Speed", 0.06, 0.0, 0.5, 0.01),
            _range("brightness", "Brightness", 0.9, 0.0, 1.0, 0.01),
        ),
        render=color_wash,
    ),
    "wave_interference": Preset(
        id="wave_interference",
        name="Wave Interference",
        description="Two to four superposed 3D wave fronts with rotating direction.",
        category="Spatial FX",
        params=(
            _palette("palette", "Palette", "ice"),
            _range("sources", "Wave Sources", 3.0, 2.0, 4.0, 1.0),
            _range("frequency", "Frequency", 2.4, 0.1, 10.0, 0.1),
            _range("amplitude", "Amplitude", 0.85, 0.0, 1.0, 0.01),
            _range("speed", "Speed", 0.22, 0.0, 2.0, 0.01),
            _range("angle", "Angle", 28.0, 0.0, 360.0, 1.0),
            _range("contrast", "Crest Contrast", 1.4, 0.2, 4.0, 0.05),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=wave_interference,
    ),
    "shutter_matrix": Preset(
        id="shutter_matrix",
        name="Shutter Matrix",
        description="Musical matrix shutter with attack, decay and per-emitter phase spread.",
        category="Spatial FX",
        params=(
            ParamSpec(
                "division",
                "Grid",
                "select",
                "1/8",
                options=(("1/2", "Half"), ("1/4", "Quarter"), ("1/8", "Eighth"), ("1/16", "Sixteenth")),
            ),
            _color("color", "Flash", [255, 248, 236]),
            _color("bg_color", "Closed", [5, 7, 14]),
            _range("attack", "Attack", 0.06, 0.005, 0.8, 0.005),
            _range("decay", "Decay", 0.24, 0.01, 1.0, 0.01),
            _range("emitter_spread", "Emitter Spread", 0.35, 0.0, 1.0, 0.01),
            _range("background_floor", "Background Floor", 0.03, 0.0, 0.5, 0.01),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=shutter_matrix,
    ),
    "emitter_swarm": Preset(
        id="emitter_swarm",
        name="Emitter Swarm",
        description="Analytic particles with birth/death colour, gravity, resistance and turbulence.",
        category="Particles",
        params=(
            _vec3("emitter_position", "Emitter Position", [0.5, 0.5, 0.25]),
            _vec3("emitter_radius", "Emitter Radii", [0.16, 0.14, 0.08]),
            _vec3("velocity", "Velocity", [0.08, 0.05, 0.18]),
            _vec3("gravity", "Gravity", [0.0, 0.0, -0.12]),
            _range("birth_rate", "Birth Rate", 9.0, 0.5, 40.0, 0.5),
            _range("lifetime", "Lifetime", 2.6, 0.25, 12.0, 0.05),
            _range("resistance", "Resistance", 0.45, 0.0, 3.0, 0.01),
            _range("turbulence", "Turbulence", 0.08, 0.0, 0.5, 0.01),
            _range("particle_size", "Particle Size", 0.28, 0.005, 0.5, 0.005),
            _range("fade_tail", "Fade Tail", 0.55, 0.0, 1.0, 0.01),
            _color("birth_color", "Birth Colour", [255, 236, 196]),
            _color("death_color", "Death Colour", [255, 54, 12]),
            _color("bg_color", "Background", [3, 4, 9]),
            _range("bg_level", "Background Level", 0.01, 0.0, 0.3, 0.005),
            _range("seed", "Seed", 1.0, 0.0, 100.0, 1.0),
            _range("brightness", "Brightness", 1.0, 0.0, 1.0, 0.01),
        ),
        render=emitter_swarm,
    ),
}



LEGACY_PRESETS: dict[str, Preset] = {
    # Legacy architectural shader, kept so existing show files still render.
    "celestial_orrery": Preset(
        id="celestial_orrery",
        name="Celestial Orrery",
        description="Two luminous bodies orbit the loft and flare as they eclipse.",
        category="Atmosphere",
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
}


def get_preset(preset_id: str) -> Preset:
    canonical = PRESET_ALIASES.get(preset_id, preset_id)
    if canonical in PRESETS:
        return PRESETS[canonical]
    if canonical in LEGACY_PRESETS:
        return LEGACY_PRESETS[canonical]
    available = ", ".join(sorted(PRESETS))
    raise KeyError(f"unknown preset '{preset_id}' (available: {available})")


def default_params(preset_id: str) -> dict[str, Any]:
    return get_preset(preset_id).default_params()


def preset_catalog() -> list[dict[str, Any]]:
    def category_index(entry: Preset) -> int:
        try:
            return CATEGORY_ORDER.index(entry.category)
        except ValueError:
            return len(CATEGORY_ORDER)

    return [
        preset.as_dict()
        for preset in sorted(
            PRESETS.values(), key=lambda item: (category_index(item), item.name)
        )
    ]


# Legacy effect ids are accepted on import; the canonical registry stays at six
# Studio presets so the UI and MCP catalog are not duplicated.
PRESET_ALIASES = {
    "turrell_skyspace": "turrell_horizon",
    "ma3_matrix_wing": "ma3_wings",
    "quad_gradient": "color_wash",
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
    "CATEGORY_ORDER",
    "LEGACY_PRESETS",
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
