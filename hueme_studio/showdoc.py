"""ShowDoc v1: deterministic declarative light shows, Oklab colour, undo/redo.

A ShowDoc is the single source of truth for a show.  The compositor evaluates
it at any timestamp without side effects.  Mutations from the web UI and from
the MCP co-director share one transactional :class:`ShowStore`, so undo/redo
can never diverge between clients.

Design notes
------------
* Colours in the document are sRGB triples in 0..255, but interpolation is
  performed in Oklab so fades between complementary colours do not pass through
  muddy grey.
* Keyframe times are relative to the clip start.  A keyframe's ``easing``
  controls the segment *starting at that keyframe*; ``hold`` keeps its value
  until the next keyframe.
* Snapshots are stored as JSON-compatible dictionaries, never as shared model
  references.  This makes undo cheap to reason about and safe across threads.
"""

from __future__ import annotations

import bisect
import json
import logging
import math
import os
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

import numpy as np
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

log = logging.getLogger("hueme_studio.showdoc")

Easing = Literal["linear", "hold", "ease_in", "ease_out", "ease_in_out", "exp_decay"]
BlendMode = Literal["normal", "add", "max", "multiply", "override"]
MaskKind = Literal["none", "plane_sweep", "sphere_narrow", "box", "parity"]
FilterKind = Literal["mirror_symmetry", "beat_gate", "dim2warm", "inertia", "audio_mod"]
MarkerKind = Literal["section", "cue", "drop", "custom"]


def _finite(value: float) -> float:
    if not math.isfinite(float(value)):
        raise ValueError("value must be finite")
    return float(value)


def _finite_vec3(value: list[float]) -> list[float]:
    if len(value) != 3 or any(not math.isfinite(float(v)) for v in value):
        raise ValueError("expected three finite coordinates")
    return [float(v) for v in value]


FiniteFloat = Annotated[float, AfterValidator(_finite)]
Normalized = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
NormalizedVec3 = Annotated[
    list[float],
    Field(min_length=3, max_length=3),
    AfterValidator(lambda v: _finite_vec3(v)),
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Colour: sRGB <-> Oklab
# ---------------------------------------------------------------------------
def srgb_to_linear(rgb: np.ndarray | list[float]) -> np.ndarray:
    arr = np.clip(np.asarray(rgb, dtype=np.float64) / 255.0, 0.0, 1.0)
    return np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(linear: np.ndarray | list[float]) -> np.ndarray:
    arr = np.clip(np.asarray(linear, dtype=np.float64), 0.0, 1.0)
    return (
        np.where(
            arr <= 0.0031308, arr * 12.92, 1.055 * np.power(arr, 1.0 / 2.4) - 0.055
        )
        * 255.0
    )


def rgb_to_oklab(rgb: np.ndarray | list[float]) -> np.ndarray:
    """Convert sRGB 0..255 (..., 3) to Oklab (..., 3)."""
    r, g, b = np.moveaxis(srgb_to_linear(rgb), -1, 0)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = np.cbrt(l), np.cbrt(m), np.cbrt(s)
    return np.stack(
        (
            0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
        ),
        axis=-1,
    )


def oklab_to_rgb(lab: np.ndarray | list[float]) -> np.ndarray:
    """Convert Oklab (..., 3) to clipped sRGB 0..255."""
    arr = np.asarray(lab, dtype=np.float64)
    L, a, b = np.moveaxis(arr, -1, 0)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_**3, m_**3, s_**3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return linear_to_srgb(np.stack((r, g, bb), axis=-1))


def lerp_oklab(
    c1: np.ndarray | list[float], c2: np.ndarray | list[float], t: float | np.ndarray
) -> np.ndarray:
    """Interpolate sRGB colours in Oklab.  ``t`` may be a scalar or broadcastable."""
    a = np.asarray(c1, dtype=np.float64)
    b = np.asarray(c2, dtype=np.float64)
    tt = np.asarray(t, dtype=np.float64)
    if tt.ndim > 0 and tt.shape[-1] not in (a.shape[-1], 1):
        tt = tt[..., None]
    lab = rgb_to_oklab(a) * (1.0 - tt) + rgb_to_oklab(b) * tt
    return oklab_to_rgb(lab)


def lerp_color(c1: list[float], c2: list[float], t: float) -> list[float]:
    """Oklab interpolation for 3- or 4-component keyframe colours."""
    a = [float(x) for x in c1]
    b = [float(x) for x in c2]
    if len(a) != len(b) or len(a) not in (3, 4):
        raise ValueError("colour keyframes need matching 3- or 4-component values")
    if len(a) == 3:
        return [float(x) for x in lerp_oklab(a, b, t)]
    mixed = lerp_oklab(a[:3], b[:3], t)
    return [float(x) for x in mixed] + [a[3] + (b[3] - a[3]) * t]


# ---------------------------------------------------------------------------
# ShowDoc models
# ---------------------------------------------------------------------------
class MatchInfo(Model):
    artist: str = ""
    title: str = ""
    normalized_keys: list[str] = Field(default_factory=list)
    ma_item_ids: list[str] = Field(default_factory=list)
    duration_s: FiniteFloat = 0.0

    @model_validator(mode="after")
    def _fill_keys(self) -> "MatchInfo":
        if not self.normalized_keys and (self.artist or self.title):
            self.normalized_keys = [
                "|".join(
                    part.strip().lower()
                    for part in (self.artist, self.title)
                    if part.strip()
                )
            ]
        return self


class Marker(Model):
    t_s: FiniteFloat = Field(ge=0.0)
    name: str = Field(min_length=1)
    kind: MarkerKind = "section"
    color: str | None = None


class Timing(Model):
    bpm: Annotated[float, Field(gt=0.0, le=400.0, allow_inf_nan=False)] = 120.0
    beat_offset_s: FiniteFloat = 0.0
    beat_grid_s: Annotated[float, Field(ge=0.0, allow_inf_nan=False)] = 0.0
    markers: list[Marker] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fill_grid(self) -> "Timing":
        if self.beat_grid_s <= 0.0:
            self.beat_grid_s = 60.0 / float(self.bpm)
        return self

    def grid_interval(self, division: str = "1/4") -> float:
        """Return seconds for a musical division relative to the beat grid.

        ``1/4`` is one beat (quarter note), ``1/8`` half a beat, ``1/1`` a
        four-beat bar.  ``bar`` is accepted as an alias for ``1/1``.
        """
        text = (division or "1/4").strip().lower().replace(" ", "")
        if text in {"bar", "1bar", "1/1bar"}:
            return self.beat_grid_s * 4.0
        beats = {
            "1/1": 4.0,
            "1/2": 2.0,
            "1/4": 1.0,
            "1/8": 0.5,
            "1/16": 0.25,
            "1/32": 0.125,
            "1/64": 0.0625,
        }
        if text not in beats:
            raise ValueError(f"unsupported grid division '{division}'")
        return self.beat_grid_s * beats[text]

    def quantize(self, t: float, division: str = "1/4") -> float:
        interval = self.grid_interval(division)
        steps = (float(t) - self.beat_offset_s) / interval
        return self.beat_offset_s + math.floor(steps + 0.5) * interval

    def beat_context(self, t: float) -> dict[str, float | int | bool]:
        grid = self.beat_grid_s
        pos = (float(t) - self.beat_offset_s) / grid
        beat_index = math.floor(pos + 1e-12)
        phase = pos - beat_index
        bar_index = math.floor(beat_index / 4)
        bar_pos = beat_index % 4
        bar_phase = (bar_pos + phase) / 4.0
        return {
            "bpm": float(self.bpm),
            "beat_grid_s": grid,
            "beat_index": beat_index,
            "beat_count": beat_index,
            "beat_phase": phase,
            "phase": phase,
            "bar_index": bar_index,
            "bar_pos": bar_pos,
            "bar_phase": bar_phase,
            "on_beat": bool(phase < 0.02),
            "on_downbeat": bool(phase < 0.02 and bar_pos == 0),
            "time_s": float(t),
        }


class Keyframe(Model):
    t_s: FiniteFloat = Field(ge=0.0)
    param: str = Field(default="opacity", min_length=1)
    value: float | list[float]
    easing: Easing = "linear"

    @field_validator("value")
    @classmethod
    def _check_value(cls, value: float | list[float]) -> float | list[float]:
        if isinstance(value, list):
            if len(value) not in (3, 4):
                raise ValueError("colour keyframe values need 3 or 4 components")
            if any(not math.isfinite(float(x)) for x in value):
                raise ValueError("keyframe values must be finite")
            return [float(x) for x in value]
        if not math.isfinite(float(value)):
            raise ValueError("keyframe values must be finite")
        return float(value)


def _ease(kind: Easing, p: float) -> float:
    p = max(0.0, min(1.0, p))
    if kind == "linear":
        return p
    if kind == "hold":
        return 0.0
    if kind == "ease_in":
        return p * p
    if kind == "ease_out":
        return 1.0 - (1.0 - p) ** 2
    if kind == "ease_in_out":
        return p * p * (3.0 - 2.0 * p)
    if kind == "exp_decay":
        return (1.0 - math.exp(-5.0 * p)) / (1.0 - math.exp(-5.0))
    return p


def evaluate_keyframes(
    keyframes: list[Keyframe], local_t: float
) -> dict[str, float | list[float]]:
    """Evaluate all keyframed parameters at a clip-local time."""
    if not keyframes:
        return {}
    tracks: dict[str, list[Keyframe]] = {}
    for kf in keyframes:
        tracks.setdefault(kf.param, []).append(kf)

    result: dict[str, float | list[float]] = {}
    for param, keys in tracks.items():
        keys = sorted(keys, key=lambda k: k.t_s)
        if local_t <= keys[0].t_s:
            result[param] = keys[0].value
            continue
        if local_t >= keys[-1].t_s:
            result[param] = keys[-1].value
            continue
        times = [k.t_s for k in keys]
        right = bisect.bisect_right(times, local_t)
        left = right - 1
        k0, k1 = keys[left], keys[right]
        span = max(1e-9, k1.t_s - k0.t_s)
        eased = _ease(k0.easing, (local_t - k0.t_s) / span)
        v0, v1 = k0.value, k1.value
        if isinstance(v0, list) or isinstance(v1, list):
            if not isinstance(v0, list) or not isinstance(v1, list):
                raise ValueError(
                    f"keyframe track '{param}' mixes scalar and colour values"
                )
            result[param] = lerp_color(v0, v1, eased)
        else:
            result[param] = float(v0) + (float(v1) - float(v0)) * eased
    return result


class SpatialMask(Model):
    kind: MaskKind = "none"
    axis: Literal["x", "y", "z"] = "x"
    position: Normalized = 0.5
    width: Annotated[float, Field(gt=0.0, le=2.0, allow_inf_nan=False)] = 0.25
    center: NormalizedVec3 = Field(default_factory=lambda: [0.5, 0.5, 0.5])
    radius: Annotated[float, Field(gt=0.0, le=3.0, allow_inf_nan=False)] = 0.3
    box_min: NormalizedVec3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    box_max: NormalizedVec3 = Field(default_factory=lambda: [1.0, 1.0, 1.0])
    modulo: Annotated[int, Field(ge=2, le=64)] = 2
    remainder: Annotated[int, Field(ge=0, le=63)] = 0
    softness: Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)] = 0.08
    invert: bool = False

    @model_validator(mode="after")
    def _check(self) -> "SpatialMask":
        if self.remainder >= self.modulo:
            raise ValueError("remainder must be smaller than modulo")
        if any(b <= a for a, b in zip(self.box_min, self.box_max)):
            raise ValueError("box_max must be greater than box_min on every axis")
        if any(x < 0.0 or x > 1.0 for x in self.center + self.box_min + self.box_max):
            raise ValueError("spatial mask coordinates must be normalized 0..1")
        return self


class FilterSpec(Model):
    type: FilterKind
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class Clip(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    start_s: FiniteFloat = Field(ge=0.0)
    end_s: FiniteFloat = Field(gt=0.0)
    fade_in_s: FiniteFloat = Field(default=0.0, ge=0.0)
    fade_out_s: FiniteFloat = Field(default=0.0, ge=0.0)
    preset: str = Field(default="solid", min_length=1)
    opacity: Normalized = 1.0
    params: dict[str, Any] = Field(default_factory=dict)
    keyframes: list[Keyframe] = Field(default_factory=list)
    spatial_mask: SpatialMask = Field(default_factory=SpatialMask)
    filters: list[FilterSpec] = Field(default_factory=list)
    snap_zcl: bool = False
    name: str | None = None
    color: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "Clip":
        duration = self.end_s - self.start_s
        if duration <= 0.0:
            raise ValueError("clip end_s must be greater than start_s")
        if self.fade_in_s + self.fade_out_s > duration + 1e-9:
            raise ValueError("clip fade in + fade out cannot exceed clip duration")
        if self.keyframes:
            outside = [k.t_s for k in self.keyframes if k.t_s > duration + 1e-9]
            if outside:
                raise ValueError(f"keyframes outside clip duration: {outside}")
        return self

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def contains(self, t: float) -> bool:
        return self.start_s <= t < self.end_s

    def local_time(self, t: float) -> float:
        return float(t) - self.start_s

    def fade_alpha(self, local_t: float) -> float:
        """Smooth attack/release envelope in 0..1."""
        duration = self.duration_s
        local = max(0.0, min(duration, float(local_t)))
        alpha = 1.0
        if self.fade_in_s > 0.0:
            x = min(1.0, local / self.fade_in_s)
            alpha *= x * x * (3.0 - 2.0 * x)
        if self.fade_out_s > 0.0:
            x = min(1.0, (duration - local) / self.fade_out_s)
            alpha *= x * x * (3.0 - 2.0 * x)
        return max(0.0, min(1.0, alpha))

    def effective_params(self, local_t: float) -> dict[str, Any]:
        params = dict(self.params)
        params.update(evaluate_keyframes(self.keyframes, local_t))
        return params

    def effective_opacity(self, local_t: float) -> float:
        params = self.effective_params(local_t)
        raw = params.get("opacity", self.opacity)
        if isinstance(raw, list):
            raise ValueError("opacity keyframes must be scalar")
        return float(np.clip(float(raw), 0.0, 1.0))


class Track(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1)
    target_group: str = Field(default="all", min_length=1)
    blend_mode: BlendMode = "normal"
    opacity: Normalized = 1.0
    muted: bool = False
    solo: bool = False
    clips: list[Clip] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "Track":
        ids = [c.id for c in self.clips]
        if len(ids) != len(set(ids)):
            raise ValueError("clip ids must be unique inside a track")
        return self

    def active_clips(self, t: float) -> list[Clip]:
        return [c for c in self.clips if c.contains(t)]


class ShowDoc(Model):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1)
    duration_s: Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
    match: MatchInfo = Field(default_factory=MatchInfo)
    timing: Timing = Field(default_factory=Timing)
    tracks: list[Track] = Field(default_factory=list)
    notes: str = ""
    created_at: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "ShowDoc":
        track_ids = [t.id for t in self.tracks]
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("track ids must be unique")
        clip_ids: list[str] = []
        for track in self.tracks:
            for clip in track.clips:
                clip_ids.append(clip.id)
                if clip.end_s > self.duration_s + 1e-6:
                    raise ValueError(
                        f"clip '{clip.id}' ends after show duration ({clip.end_s} > {self.duration_s})"
                    )
        if len(clip_ids) != len(set(clip_ids)):
            raise ValueError("clip ids must be unique across the whole show")
        for marker in self.timing.markers:
            if marker.t_s > self.duration_s + 1e-6:
                raise ValueError(f"marker '{marker.name}' lies after show duration")
        return self

    # -- helpers ----------------------------------------------------------
    def find_track(self, track_id: str) -> Track | None:
        return next((t for t in self.tracks if t.id == track_id), None)

    def find_clip(self, clip_id: str) -> tuple[Track, Clip] | None:
        for track in self.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return track, clip
        return None

    def all_clips(self) -> list[tuple[Track, Clip]]:
        return [(track, clip) for track in self.tracks for clip in track.clips]

    def active_tracks(self) -> list[Track]:
        solos = [t for t in self.tracks if t.solo and not t.muted]
        if solos:
            return solos
        return [t for t in self.tracks if not t.muted]

    def beat_context(self, t: float) -> dict[str, float | int | bool]:
        return self.timing.beat_context(t)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ShowDoc":
        return cls.model_validate(payload)

    @classmethod
    def from_json(cls, text: str) -> "ShowDoc":
        return cls.model_validate_json(text)


# ---------------------------------------------------------------------------
# Spatial mask evaluation
# ---------------------------------------------------------------------------
def _smoothstep(
    edge0: float | np.ndarray, edge1: float | np.ndarray, x: np.ndarray
) -> np.ndarray:
    e0 = np.asarray(edge0, dtype=np.float64)
    e1 = np.asarray(edge1, dtype=np.float64)
    denom = e1 - e0
    safe = np.where(np.abs(denom) < 1e-12, 1.0, denom)
    t = np.clip((x - e0) / safe, 0.0, 1.0)
    hard = np.where(x >= e1, 1.0, 0.0)
    return np.where(np.abs(denom) < 1e-12, hard, t * t * (3.0 - 2.0 * t))


def evaluate_spatial_mask(
    mask: SpatialMask,
    normalized_positions: np.ndarray,
    voxel_indices: np.ndarray | None = None,
) -> np.ndarray:
    """Evaluate a mask to a per-voxel float32 weight in 0..1."""
    pos = np.asarray(normalized_positions, dtype=np.float64)
    n = int(pos.shape[0])
    if mask.kind == "none":
        weights = np.ones(n, dtype=np.float64)
    elif mask.kind == "plane_sweep":
        axis = {"x": 0, "y": 1, "z": 2}[mask.axis]
        coord = pos[:, axis]
        half = mask.width / 2.0
        soft = mask.softness
        if soft <= 1e-9:
            weights = (
                (coord >= mask.position - half) & (coord <= mask.position + half)
            ).astype(np.float64)
        else:
            low = _smoothstep(
                mask.position - half - soft, mask.position - half + soft, coord
            )
            high = 1.0 - _smoothstep(
                mask.position + half - soft, mask.position + half + soft, coord
            )
            weights = low * high
    elif mask.kind == "sphere_narrow":
        centre = np.asarray(mask.center, dtype=np.float64)
        dist = np.linalg.norm(pos - centre, axis=1)
        if mask.softness <= 1e-9:
            weights = (dist <= mask.radius).astype(np.float64)
        else:
            weights = 1.0 - _smoothstep(mask.radius, mask.radius + mask.softness, dist)
    elif mask.kind == "box":
        lo = np.asarray(mask.box_min, dtype=np.float64)
        hi = np.asarray(mask.box_max, dtype=np.float64)
        soft = mask.softness
        if soft <= 1e-9:
            weights = np.all((pos >= lo) & (pos <= hi), axis=1).astype(np.float64)
        else:
            low = _smoothstep(lo - soft, lo + soft, pos)
            high = 1.0 - _smoothstep(hi - soft, hi + soft, pos)
            weights = np.prod(low * high, axis=1)
    elif mask.kind == "parity":
        indices = np.arange(n) if voxel_indices is None else np.asarray(voxel_indices)
        weights = ((indices % mask.modulo) == mask.remainder).astype(np.float64)
    else:  # pragma: no cover - guarded by Literal
        weights = np.ones(n, dtype=np.float64)
    if mask.invert:
        weights = 1.0 - weights
    return np.clip(weights, 0.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------------------
# Transactional store and undo/redo
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Snapshot:
    label: str
    doc: dict[str, Any]
    created_at: float


class ShowStoreError(RuntimeError):
    pass


class ShowNotFound(ShowStoreError):
    pass


class ShowStore:
    """Thread-safe show registry with atomic persistence and 50+ undo levels.

    Mutations always go through :meth:`commit` or :meth:`apply`; the store
    snapshots the *previous* document, validates the new one, writes it
    atomically, and notifies subscribers exactly once.
    """

    def __init__(
        self,
        shows_dir: str | os.PathLike[str] = "config/shows",
        *,
        history_limit: int = 50,
        persist: bool = True,
        on_change: Callable[[str, ShowDoc, str, str], None] | None = None,
    ) -> None:
        if history_limit < 50:
            raise ValueError("history_limit must be at least 50 snapshots")
        self.shows_dir = Path(shows_dir)
        self.history_limit = int(history_limit)
        self.persist = bool(persist)
        self.on_change = on_change
        self._lock = threading.RLock()
        self._docs: dict[str, ShowDoc] = {}
        self._undo: dict[str, deque[Snapshot]] = {}
        self._redo: dict[str, deque[Snapshot]] = {}
        if self.persist:
            self.shows_dir.mkdir(parents=True, exist_ok=True)
        # Loading is independent of persistence: --no-persist means a
        # read-only session, not an empty show library.
        self._load_existing()

    # -- loading / persistence -------------------------------------------
    def _load_existing(self) -> None:
        index_path = self.shows_dir / "index.json"
        files: list[Path] = []
        if index_path.exists():
            try:
                payload = json.loads(index_path.read_text(encoding="utf-8"))
                for entry in payload.get("shows", []):
                    name = entry.get("file")
                    if name:
                        files.append(self.shows_dir / str(name))
            except (OSError, ValueError, TypeError) as exc:
                log.warning("Could not read show index %s: %s", index_path, exc)
        if not files:
            files = sorted(self.shows_dir.glob("*.json"))
        for path in files:
            if path.name == "index.json" or not path.exists():
                continue
            try:
                doc = ShowDoc.from_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("Skipping invalid show file %s: %s", path, exc)
                continue
            self._docs[doc.id] = doc
            self._undo[doc.id] = deque(maxlen=self.history_limit)
            self._redo[doc.id] = deque(maxlen=self.history_limit)

    def _index_payload(self) -> dict[str, Any]:
        entries = []
        for doc in sorted(self._docs.values(), key=lambda d: d.name.lower()):
            entries.append(
                {
                    "id": doc.id,
                    "name": doc.name,
                    "file": f"{doc.id}.json",
                    "duration_s": doc.duration_s,
                    "bpm": doc.timing.bpm,
                    "track_count": len(doc.tracks),
                    "clip_count": len(doc.all_clips()),
                    "artist": doc.match.artist,
                    "title": doc.match.title,
                    "match": doc.match.model_dump(mode="json"),
                }
            )
        return {"schema_version": 1, "shows": entries}

    @staticmethod
    def _atomic_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        fd, tmp = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _persist_doc(self, doc: ShowDoc) -> None:
        if not self.persist:
            return
        self._atomic_json(self.shows_dir / f"{doc.id}.json", doc.to_dict())
        self._atomic_json(self.shows_dir / "index.json", self._index_payload())

    # -- reads ------------------------------------------------------------
    def list_shows(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._index_payload()["shows"])

    def ids(self) -> list[str]:
        with self._lock:
            return sorted(self._docs)

    def get(self, show_id: str) -> ShowDoc:
        with self._lock:
            try:
                return self._docs[show_id].model_copy(deep=True)
            except KeyError as exc:
                raise ShowNotFound(f"show '{show_id}' not found") from exc

    def has(self, show_id: str) -> bool:
        with self._lock:
            return show_id in self._docs

    # -- notifications ----------------------------------------------------
    def _notify(self, show_id: str, doc: ShowDoc, action: str, label: str) -> None:
        callback = self.on_change
        if callback is None:
            return
        try:
            callback(show_id, doc.model_copy(deep=True), action, label)
        except Exception:  # pragma: no cover - a bad subscriber must not lose edits
            log.exception("ShowStore change callback failed")

    # -- mutations --------------------------------------------------------
    def create(self, doc: ShowDoc, label: str = "create show") -> ShowDoc:
        with self._lock:
            if doc.id in self._docs:
                raise ShowStoreError(f"show '{doc.id}' already exists")
            self._docs[doc.id] = doc.model_copy(deep=True)
            self._undo[doc.id] = deque(maxlen=self.history_limit)
            self._redo[doc.id] = deque(maxlen=self.history_limit)
            self._persist_doc(doc)
            out = doc.model_copy(deep=True)
        self._notify(doc.id, out, "create", label)
        return out

    def replace(self, doc: ShowDoc, label: str = "replace show") -> ShowDoc:
        with self._lock:
            if doc.id not in self._docs:
                raise ShowNotFound(f"show '{doc.id}' not found")
            out = self._commit_locked(doc.id, doc, label)
        self._notify(doc.id, out, "commit", label)
        return out

    def commit(self, show_id: str, new_doc: ShowDoc, label: str = "edit") -> ShowDoc:
        with self._lock:
            if show_id not in self._docs:
                raise ShowNotFound(f"show '{show_id}' not found")
            if new_doc.id != show_id:
                raise ShowStoreError("cannot change a show id during commit")
            out = self._commit_locked(show_id, new_doc, label)
        self._notify(show_id, out, "commit", label)
        return out

    def _commit_locked(self, show_id: str, new_doc: ShowDoc, label: str) -> ShowDoc:
        # Validate a round-trip so manually constructed model instances cannot
        # sneak in values that JSON persistence would reject.
        validated = ShowDoc.model_validate(new_doc.model_dump(mode="json"))
        old = self._docs[show_id]
        self._undo.setdefault(show_id, deque(maxlen=self.history_limit)).append(
            Snapshot(
                label=label, doc=old.model_dump(mode="json"), created_at=time.time()
            )
        )
        self._redo.setdefault(show_id, deque(maxlen=self.history_limit)).clear()
        self._docs[show_id] = validated
        self._persist_doc(validated)
        return validated.model_copy(deep=True)

    def apply(
        self,
        show_id: str,
        label: str,
        mutate: Callable[[ShowDoc], ShowDoc | None],
    ) -> ShowDoc:
        """Run a mutation against a deep copy and commit it as one transaction."""
        with self._lock:
            if show_id not in self._docs:
                raise ShowNotFound(f"show '{show_id}' not found")
            current = self._docs[show_id].model_copy(deep=True)
            result = mutate(current)
            new_doc = current if result is None else result
            if not isinstance(new_doc, ShowDoc):
                raise ShowStoreError("mutation must return a ShowDoc or None")
            if new_doc.id != show_id:
                raise ShowStoreError("cannot change a show id during commit")
            out = self._commit_locked(show_id, new_doc, label)
        self._notify(show_id, out, "commit", label)
        return out

    def undo(self, show_id: str) -> ShowDoc | None:
        with self._lock:
            stack = self._undo.setdefault(show_id, deque(maxlen=self.history_limit))
            if not stack:
                return None
            snap = stack.pop()
            current = self._docs[show_id]
            self._redo.setdefault(show_id, deque(maxlen=self.history_limit)).append(
                Snapshot(
                    label=snap.label,
                    doc=current.model_dump(mode="json"),
                    created_at=time.time(),
                )
            )
            restored = ShowDoc.model_validate(snap.doc)
            self._docs[show_id] = restored
            self._persist_doc(restored)
            out = restored.model_copy(deep=True)
        self._notify(show_id, out, "undo", snap.label)
        return out

    def redo(self, show_id: str) -> ShowDoc | None:
        with self._lock:
            stack = self._redo.setdefault(show_id, deque(maxlen=self.history_limit))
            if not stack:
                return None
            snap = stack.pop()
            current = self._docs[show_id]
            self._undo.setdefault(show_id, deque(maxlen=self.history_limit)).append(
                Snapshot(
                    label=snap.label,
                    doc=current.model_dump(mode="json"),
                    created_at=time.time(),
                )
            )
            restored = ShowDoc.model_validate(snap.doc)
            self._docs[show_id] = restored
            self._persist_doc(restored)
            out = restored.model_copy(deep=True)
        self._notify(show_id, out, "redo", snap.label)
        return out

    def delete(self, show_id: str) -> None:
        with self._lock:
            if show_id not in self._docs:
                raise ShowNotFound(f"show '{show_id}' not found")
            doc = self._docs.pop(show_id)
            self._undo.pop(show_id, None)
            self._redo.pop(show_id, None)
            if self.persist:
                path = self.shows_dir / f"{doc.id}.json"
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("Could not remove show file %s: %s", path, exc)
                self._atomic_json(self.shows_dir / "index.json", self._index_payload())
        self._notify(show_id, doc, "delete", "delete show")

    def can_undo(self, show_id: str) -> bool:
        with self._lock:
            return bool(self._undo.get(show_id))

    def can_redo(self, show_id: str) -> bool:
        with self._lock:
            return bool(self._redo.get(show_id))

    def history(self, show_id: str) -> dict[str, Any]:
        with self._lock:
            undo = list(self._undo.get(show_id, ()))
            redo = list(self._redo.get(show_id, ()))
            return {
                "undo_depth": len(undo),
                "redo_depth": len(redo),
                "undo_labels": [s.label for s in reversed(undo)],
                "redo_labels": [s.label for s in redo],
            }

    def snapshot(self, show_id: str, label: str = "manual snapshot") -> None:
        """Explicitly push the current state (without mutating the document)."""
        with self._lock:
            if show_id not in self._docs:
                raise ShowNotFound(f"show '{show_id}' not found")
            self._undo.setdefault(show_id, deque(maxlen=self.history_limit)).append(
                Snapshot(
                    label=label,
                    doc=self._docs[show_id].model_dump(mode="json"),
                    created_at=time.time(),
                )
            )
            self._redo.setdefault(show_id, deque(maxlen=self.history_limit)).clear()


__all__ = [
    "BlendMode",
    "Clip",
    "Easing",
    "FilterSpec",
    "Keyframe",
    "Marker",
    "MatchInfo",
    "ShowDoc",
    "ShowNotFound",
    "ShowStore",
    "ShowStoreError",
    "Snapshot",
    "SpatialMask",
    "Timing",
    "Track",
    "evaluate_keyframes",
    "evaluate_spatial_mask",
    "lerp_color",
    "lerp_oklab",
    "oklab_to_rgb",
    "rgb_to_oklab",
]
